"""Plataforma de sensores da integração Enel São Paulo."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EnelSPConfigEntry
from .entity import EnelSPBaseEntity


def _pending_bills(bills: list[dict]) -> list[dict]:
    """Faturas ainda não pagas — critério único, compartilhado por todo
    sensor que precisa saber se há conta em aberto."""
    return [b for b in bills if b.get("SITUACAO") != "Paga"]


BILL_DESCRIPTION = SensorEntityDescription(
    key="valor_ultima_fatura_fechada",
    translation_key="valor_ultima_fatura_fechada",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement="BRL",
)

CONSUMPTION_DESCRIPTION = SensorEntityDescription(
    key="consumo_periodo_atual",
    translation_key="consumo_periodo_atual",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement="kWh",
    state_class=SensorStateClass.TOTAL,
)

TARIFF_FLAG_DESCRIPTION = SensorEntityDescription(
    key="bandeira_tarifaria",
    translation_key="bandeira_tarifaria",
    icon="mdi:flag",
)

NEXT_READING_DATE_DESCRIPTION = SensorEntityDescription(
    key="data_proxima_leitura",
    translation_key="data_proxima_leitura",
    device_class=SensorDeviceClass.TIMESTAMP,
)

CURRENT_READING_DATE_DESCRIPTION = SensorEntityDescription(
    key="data_leitura_atual",
    translation_key="data_leitura_atual",
    device_class=SensorDeviceClass.TIMESTAMP,
)

PREVIOUS_METER_READING_DESCRIPTION = SensorEntityDescription(
    key="valor_medidor_anterior",
    translation_key="valor_medidor_anterior",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement="kWh",
    icon="mdi:counter",
    # Sem isso, o front-end do HA formata sensores "energy" com 2 casas
    # decimais por padrão, mesmo com um int por baixo.
    suggested_display_precision=0,
)

CURRENT_METER_READING_DESCRIPTION = SensorEntityDescription(
    key="valor_medidor_atual",
    translation_key="valor_medidor_atual",
    device_class=SensorDeviceClass.ENERGY,
    native_unit_of_measurement="kWh",
    state_class=SensorStateClass.TOTAL_INCREASING,
    icon="mdi:counter",
    suggested_display_precision=0,
)

BILL_PDF_URL_DESCRIPTION = SensorEntityDescription(
    key="link_fatura_pdf",
    translation_key="link_fatura_pdf",
    icon="mdi:file-pdf-box",
    entity_category=EntityCategory.DIAGNOSTIC,
)

PIX_CODE_DESCRIPTION = SensorEntityDescription(
    key="codigo_pix",
    translation_key="codigo_pix",
    icon="mdi:qrcode",
)

# Limite de tamanho de um estado de entidade no HA. O código Pix "copia e
# cola" às vezes ultrapassa isso; quando passa, o estado fica truncado e o
# valor completo vai pro atributo `codigo_completo`.
_MAX_STATE_LEN = 255

ACCOUNT_STATUS_DESCRIPTION = SensorEntityDescription(
    key="status_conta",
    translation_key="status_conta",
    icon="mdi:file-document-alert-outline",
)

PENDING_BILLS_COUNT_DESCRIPTION = SensorEntityDescription(
    key="total_contas_abertas",
    translation_key="total_contas_abertas",
    icon="mdi:file-document-multiple-outline",
    state_class=SensorStateClass.MEASUREMENT,
)

BILL_ANALYSIS_MESSAGE_DESCRIPTION = SensorEntityDescription(
    key="mensagem_analise_fatura",
    translation_key="mensagem_analise_fatura",
    icon="mdi:message-text-outline",
)

# Sem device_class: são taxas ("por dia"), não uma quantidade/valor absoluto,
# então a unidade não precisa (nem pode, se tivesse device_class) ser uma das
# unidades fixas que o HA valida para "energy"/"monetary".
DAILY_CONSUMPTION_DESCRIPTION = SensorEntityDescription(
    key="consumo_medio_diario",
    translation_key="consumo_medio_diario",
    native_unit_of_measurement="kWh/d",
    icon="mdi:lightning-bolt-outline",
)

DAILY_AMOUNT_DESCRIPTION = SensorEntityDescription(
    key="gasto_medio_diario",
    translation_key="gasto_medio_diario",
    native_unit_of_measurement="BRL/d",
    icon="mdi:currency-usd",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnelSPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cria os sensores da Enel São Paulo a partir de uma config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            EnelSPBillSensor(coordinator, BILL_DESCRIPTION),
            EnelSPConsumptionSensor(coordinator, CONSUMPTION_DESCRIPTION),
            EnelSPTariffFlagSensor(coordinator, TARIFF_FLAG_DESCRIPTION),
            EnelSPNextReadingDateSensor(coordinator, NEXT_READING_DATE_DESCRIPTION),
            EnelSPCurrentReadingDateSensor(coordinator, CURRENT_READING_DATE_DESCRIPTION),
            EnelSPPreviousMeterReadingSensor(coordinator, PREVIOUS_METER_READING_DESCRIPTION),
            EnelSPCurrentMeterReadingSensor(coordinator, CURRENT_METER_READING_DESCRIPTION),
            EnelSPBillPdfUrlSensor(coordinator, BILL_PDF_URL_DESCRIPTION),
            EnelSPPixCodeSensor(coordinator, PIX_CODE_DESCRIPTION),
            EnelSPAccountStatusSensor(coordinator, ACCOUNT_STATUS_DESCRIPTION),
            EnelSPPendingBillsCountSensor(coordinator, PENDING_BILLS_COUNT_DESCRIPTION),
            EnelSPBillAnalysisMessageSensor(coordinator, BILL_ANALYSIS_MESSAGE_DESCRIPTION),
            EnelSPDailyConsumptionSensor(coordinator, DAILY_CONSUMPTION_DESCRIPTION),
            EnelSPDailyAmountSensor(coordinator, DAILY_AMOUNT_DESCRIPTION),
        ]
    )


class EnelSPEntity(EnelSPBaseEntity, SensorEntity):
    """Sensor da Enel SP (mix-in de EnelSPBaseEntity + SensorEntity)."""


class EnelSPBillSensor(EnelSPEntity):
    """Valor da última fatura fechada (a mais recente não paga)."""

    @property
    def native_value(self) -> float | None:
        bill = self._data.next_due_bill
        return bill.get("MONTANTE") if bill else None

    @property
    def extra_state_attributes(self) -> dict:
        bill = self._data.next_due_bill
        if not bill:
            return {}
        return {
            "vencimento": bill.get("VENCIMENTO"),
            "situacao": bill.get("SITUACAO"),
            "mes_referencia": bill.get("ANO_MES_REF"),
            "codigo_barras": bill.get("COD_BARRAS_NOVO") or bill.get("O_COD_BARRAS"),
            "codigo_pix": bill.get("QRCODE") or None,
        }


class EnelSPConsumptionSensor(EnelSPEntity):
    """Consumo de energia do período de faturamento atual."""

    @property
    def native_value(self) -> float | None:
        return self._data.current_consumption_kwh

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "periodo": self._data.current_period,
            "historico": self._data.history,
        }


class EnelSPTariffFlagSensor(EnelSPEntity):
    """Bandeira tarifária atual."""

    @property
    def native_value(self) -> str | None:
        return self._data.tariff_flag or None


class EnelSPNextReadingDateSensor(EnelSPEntity):
    """Data prevista para a próxima leitura do medidor."""

    @property
    def native_value(self):
        # A API só informa o dia, sem horário; usamos a meia-noite local em
        # vez de UTC para não fazer a data "voltar" um dia na exibição.
        date_value = self._data.next_reading_date
        return dt_util.start_of_local_day(date_value) if date_value else None


class EnelSPCurrentReadingDateSensor(EnelSPEntity):
    """Data da última leitura do medidor (período de faturamento atual)."""

    @property
    def native_value(self):
        date_value = self._data.current_reading_date
        return dt_util.start_of_local_day(date_value) if date_value else None


class EnelSPPreviousMeterReadingSensor(EnelSPEntity):
    """Valor do registrador do medidor na leitura anterior.

    Não é retornado diretamente pela API: calculado como
    ``leitura atual - (consumo do período / constante do medidor)``.
    """

    @property
    def native_value(self) -> int | None:
        return self._data.previous_meter_reading


class EnelSPCurrentMeterReadingSensor(EnelSPEntity):
    """Valor do registrador do medidor na leitura atual (mais recente)."""

    @property
    def native_value(self) -> int | None:
        return self._data.current_meter_reading


class EnelSPBillPdfUrlSensor(EnelSPEntity):
    """URL local (config/www/, sem login) pra abrir o PDF da fatura mais recente."""

    @property
    def native_value(self) -> str | None:
        return self._data.bill_pdf_url


class EnelSPPixCodeSensor(EnelSPEntity):
    """Código Pix "copia e cola" da fatura em aberto.

    Vem pronto no mesmo ``ET_CONTAS[]`` do ``portalinfo`` (campo ``QRCODE``,
    populado pela Enel só quando a fatura está em aberto). Fica vazio quando
    não há fatura pendente ou quando a mais recente já foi paga.
    """

    @property
    def _pix_code(self) -> str | None:
        bill = self._data.next_due_bill
        return (bill.get("QRCODE") or None) if bill else None

    @property
    def native_value(self) -> str | None:
        code = self._pix_code
        # Códigos Pix às vezes passam do limite de 255 caracteres que o HA
        # aceita no estado de uma entidade; nesse caso o valor completo fica
        # só no atributo `codigo_completo`.
        return code[:_MAX_STATE_LEN] if code else None

    @property
    def extra_state_attributes(self) -> dict:
        code = self._pix_code
        if code and len(code) > _MAX_STATE_LEN:
            return {"codigo_completo": code}
        return {}


class EnelSPAccountStatusSensor(EnelSPEntity):
    """Se há alguma conta em aberto entre todas as consultadas (não só a mais recente)."""

    @property
    def native_value(self) -> str:
        return "Em aberto" if _pending_bills(self._data.bills) else "Paga"

    @property
    def extra_state_attributes(self) -> dict:
        pending = _pending_bills(self._data.bills)
        return {
            "quantidade_pendentes": len(pending),
            "valor_total_pendente": round(sum(b.get("MONTANTE") or 0 for b in pending), 2),
            "meses_pendentes": [b.get("ANO_MES_REF") for b in pending],
        }


class EnelSPPendingBillsCountSensor(EnelSPEntity):
    """Quantidade de faturas em aberto entre todas as consultadas (não só a
    mais recente) — mesmo critério e mesma contagem do atributo
    `quantidade_pendentes` do sensor "Status da conta", como sensor próprio.
    """

    @property
    def native_value(self) -> int:
        return len(_pending_bills(self._data.bills))


class EnelSPBillAnalysisMessageSensor(EnelSPEntity):
    """Resumo da Enel sobre a variação da fatura mais recente (ex.: "Você
    gastou R$ 10,55 a menos que o mês anterior...")."""

    @property
    def native_value(self) -> str | None:
        message = self._data.bill_analysis_message
        if not message:
            return None
        return message[:_MAX_STATE_LEN]

    @property
    def extra_state_attributes(self) -> dict:
        message = self._data.bill_analysis_message
        if message and len(message) > _MAX_STATE_LEN:
            return {"mensagem_completa": message}
        return {}


class EnelSPDailyConsumptionSensor(EnelSPEntity):
    """Consumo médio diário (kWh/dia) do ciclo de faturamento mais recente."""

    @property
    def native_value(self) -> float | None:
        return self._data.daily_consumption_kwh


class EnelSPDailyAmountSensor(EnelSPEntity):
    """Gasto médio diário (R$/dia) do ciclo de faturamento mais recente."""

    @property
    def native_value(self) -> float | None:
        return self._data.daily_amount
