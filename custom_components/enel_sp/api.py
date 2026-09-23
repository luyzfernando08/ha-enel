"""Cliente das APIs de negócio do portal da Enel São Paulo (fatura, consumo,
medidor inteligente, PDF). O login/SAML mora em ``auth.py``; este módulo cuida
do que vem depois de logado.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import aiohttp

from .auth import (
    EnelSPAuthError,
    EnelSPAuthMixin,
    EnelSPError,
    Installation,
    _DEBUG_SNIPPET_LEN,
    _host_of,
)
from .const import (
    ANALISE_CONSUMO_URL,
    BILLANALYSIS_URL,
    CANAL,
    COD_SISTEMA,
    DEFAULT_UPDATE_INTERVAL,
    GENERATE_PDF_URL,
    GETCLIENTBILLS_URL,
    PORTALHISTORYINFO_URL,
    SAO_PAULO_TZ,
    WWW_ORIGIN,
)

_LOGGER = logging.getLogger(__name__)

_PT_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11,
    "dezembro": 12,
}
_NEXT_READING_RE = re.compile(r"(\d{1,2})\s+de\s+([A-Za-zçÇ]+)", re.IGNORECASE)


def _strip_accents(text: str) -> str:
    return (
        text.lower()
        .replace("ç", "c")
        .replace("á", "a").replace("â", "a").replace("ã", "a")
        .replace("é", "e").replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o").replace("ô", "o").replace("õ", "o")
        .replace("ú", "u")
    )


def _parse_yyyymmdd(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _parse_next_reading_date(text: str | None, reference: date | None) -> date | None:
    """Converte um texto no formato "10 de Setembro" (sem ano) numa data completa.

    A API nunca informa o ano, então a próxima leitura é considerada a
    primeira ocorrência daquele dia/mês estritamente após ``reference``.
    """
    if not text or not reference:
        return None
    match = _NEXT_READING_RE.search(text)
    if not match:
        return None
    day = int(match.group(1))
    month = _PT_MONTHS.get(_strip_accents(match.group(2)))
    if not month:
        return None
    try:
        candidate = date(reference.year, month, day)
    except ValueError:
        return None
    if candidate <= reference:
        try:
            candidate = date(reference.year + 1, month, day)
        except ValueError:
            return None
    return candidate


_READING_BUFFER = timedelta(days=1)


def compute_next_update_interval(
    next_reading_date: date | None, now: datetime | None = None
) -> timedelta:
    """Intervalo até a próxima atualização agendada: ``next_reading_date + 1
    dia`` (a Enel costuma fechar a leitura no próprio dia, então esperamos
    mais um dia pra garantir que o dado já esteja disponível no portal).

    Cai no ``DEFAULT_UPDATE_INTERVAL`` (fallback) se ``next_reading_date`` não
    vier informado, ou se o alvo calculado já estiver no passado/presente —
    o que indicaria um dado desatualizado, não um agendamento válido.
    """
    reference = now or datetime.now(SAO_PAULO_TZ)
    if next_reading_date is None:
        return DEFAULT_UPDATE_INTERVAL
    target = datetime.combine(
        next_reading_date, time.min, tzinfo=SAO_PAULO_TZ
    ) + _READING_BUFFER
    delta = target - reference
    return delta if delta > timedelta(0) else DEFAULT_UPDATE_INTERVAL


class EnelSPApiError(EnelSPError):
    """Levantado quando uma chamada de API de negócio falha ou retorna um erro."""


@dataclass
class EnelSPData:
    """Dados agregados de uma unidade consumidora, consumidos pelo coordinator."""

    installation: Installation
    tariff_flag: str = ""
    current_period: str = ""
    current_consumption_kwh: float | None = None
    bills: list[dict[str, Any]] = field(default_factory=list)
    next_due_bill: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    current_reading_date: date | None = None
    next_reading_date: date | None = None
    current_meter_reading: int | None = None
    previous_meter_reading: int | None = None
    supply_suspended: bool = False
    supply_suspended_message: str = ""
    bill_analysis_message: str = ""
    daily_consumption_kwh: float | None = None
    daily_amount: float | None = None
    # Preenchido pelo coordinator (precisa de acesso ao hass pra salvar o
    # arquivo), não pelo cliente da API.
    bill_pdf_url: str | None = None


def most_recent_bill(bills: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A fatura de maior VENCIMENTO (não a posição 0 — a API não garante ordem)."""
    return max(bills, key=lambda b: b.get("VENCIMENTO") or "") if bills else None


class EnelSPClient(EnelSPAuthMixin):
    """Conversa com accounts.enel.com / www.enel.com.br / as APIs do Mulesoft.

    O login/SAML (``async_login``, ``get_installations``, ``tariff_flag``) vem
    de ``EnelSPAuthMixin`` (``auth.py``); esta classe só adiciona as chamadas
    de API de negócio, feitas depois de logado.
    """

    def _business_headers(self, url: str) -> dict[str, str]:
        if not self._jwt:
            raise EnelSPAuthError("Not logged in")
        return {
            "Content-Type": "application/json",
            "SID": self._sid,
            "enel-jwt-token": self._jwt,
            "CLIENT_IP": "123",
            "Host": _host_of(url),
            "Origin": WWW_ORIGIN,
            "Referer": f"{WWW_ORIGIN}/",
        }

    def _envelope(self, funcionalidad: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "Header": {
                "Funcionalidad": funcionalidad,
                "CodSistema": COD_SISTEMA,
                "SistemaOrigen": COD_SISTEMA,
                "FechaHora": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            },
            "Body": body,
        }

    async def _async_post_business(self, url: str, funcionalidad: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = self._envelope(funcionalidad, body)
        async with self._session.post(url, json=payload, headers=self._business_headers(url)) as resp:
            _LOGGER.debug("POST %s (%s) -> %s %s", url, funcionalidad, resp.status, resp.reason)
            if resp.status == 401:
                raise EnelSPAuthError("Session expired, please log in again")
            if resp.status != 200:
                text = await resp.text()
                _LOGGER.debug("%s error body:\n%s", funcionalidad, text[:_DEBUG_SNIPPET_LEN])
                raise EnelSPApiError(f"{funcionalidad} call failed with status {resp.status}")
            data = await resp.json(content_type=None)

        body_out = data.get("Body", {})
        error_msg = body_out.get("DescripcionResultado") or body_out.get("E_MSG")
        codigo = body_out.get("CodigoResultado") or body_out.get("E_RESULT")
        if codigo and str(codigo).strip() not in ("", "0"):
            _LOGGER.debug("%s returned error payload: %s", funcionalidad, body_out)
            raise EnelSPApiError(f"{funcionalidad} returned an error: {error_msg or codigo}")
        return body_out

    async def async_get_consumption(self, installation: Installation) -> dict[str, Any]:
        return await self._async_post_business(
            ANALISE_CONSUMO_URL,
            "getAnaliseConsumo",
            {
                "I_CANAL": CANAL,
                "I_COD_SERV": "TC",
                "I_ANLAGE": "",
                "I_ENELID": self._enel_id or "",
                "I_VKONT": installation.vkont,
                "I_VERTRAG": installation.vertrag,
                "I_PARTNER": installation.partner,
            },
        )

    async def async_get_bills(self, installation: Installation) -> dict[str, Any]:
        return await self._async_post_business(
            GETCLIENTBILLS_URL,
            "getClientBills",
            {
                "I_COD_SERV": "TC",
                "I_QTDE_FAT": "99",
                "I_CANAL": CANAL,
                "I_VKONT": installation.vkont,
                "I_VERTRAG": installation.vertrag,
                "I_PARTNER": installation.partner,
            },
        )

    async def async_get_history(self, installation: Installation) -> dict[str, Any]:
        return await self._async_post_business(
            PORTALHISTORYINFO_URL,
            "portalhistoryinfo",
            {
                "I_CANAL": CANAL,
                "I_COD_SERV": "TC",
                "I_SOLIC": "",
                "I_NOTIF_FULL": "",
                "I_SERVICOS_FULL": "",
                "I_ENELID": self._enel_id or "",
                "I_VKONT": installation.vkont,
                "I_VERTRAG": installation.vertrag,
                "I_PARTNER": installation.partner,
            },
        )

    async def async_get_bill_analysis(self, installation: Installation, belnr: str) -> dict[str, Any]:
        return await self._async_post_business(
            BILLANALYSIS_URL,
            "billanalysis",
            {
                "I_CANAL": CANAL,
                "I_COD_SERV": "AF",
                "I_VKONT": installation.vkont,
                "I_PARTNER": installation.partner,
                "I_VERTRAG": installation.vertrag,
                "I_BELNR": belnr,
                "I_ID": "",
                "I_SSO_GUID": "",
            },
        )

    async def async_get_bill_pdf(self, installation: Installation, bill: dict[str, Any]) -> bytes:
        """Baixa o PDF de uma fatura e devolve os bytes já decodificados.

        Ao contrário de todo o resto da API, a resposta desse endpoint não
        vem dentro de um envelope ``Body`` — os campos ficam soltos no nível
        raiz, então não dá pra reaproveitar ``_async_post_business`` aqui.

        Esse endpoint falha esporadicamente com 500 do lado da Enel (não tem
        relação com o payload — a mesma fatura pedida de novo logo em
        seguida costuma funcionar), então tentamos mais uma vez antes de
        desistir.
        """
        payload = self._envelope(
            "generatePdf",
            {
                "I_CANAL": CANAL,
                "I_COD_SERV": "SV",
                "I_MOTIVO_EXT_SITE": "05",
                "I_ORIGEM_DOC": bill.get("ORIGEM_DOC", "C"),
                "I_BELNR": bill.get("BELNR", ""),
                "I_CONTATO": "",
                "I_TOTEM_MOB": "",
                "I_PARTNER": installation.partner,
                "I_VERTRAG": installation.vertrag,
                "I_VKONT": installation.vkont,
                "I_ANLAGE": installation.anlage,
                "I_SSO_GUID": "",
            },
        )
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            async with self._session.post(
                GENERATE_PDF_URL, json=payload, headers=self._business_headers(GENERATE_PDF_URL)
            ) as resp:
                _LOGGER.debug(
                    "POST %s (generatePdf) -> %s %s (tentativa %d/%d)",
                    GENERATE_PDF_URL, resp.status, resp.reason, attempt, max_attempts,
                )
                if resp.status != 200:
                    text = await resp.text()
                    _LOGGER.debug("generatePdf error body:\n%s", text[:_DEBUG_SNIPPET_LEN])
                    if resp.status >= 500 and attempt < max_attempts:
                        await asyncio.sleep(2)
                        continue
                    raise EnelSPApiError(f"generatePdf call failed with status {resp.status}")
                data = await resp.json(content_type=None)
            break

        pdf_b64 = data.get("E_BIN_FAT")
        if not pdf_b64:
            raise EnelSPApiError(f"generatePdf returned no PDF data: {data.get('E_MSG') or data}")
        return base64.b64decode(pdf_b64)

    def _extract_bill_analysis_info(self, analysis: dict[str, Any]) -> dict[str, Any]:
        """Extrai leitura do medidor, mensagem de análise e médias diárias do billanalysis."""
        current_reading_date = _parse_yyyymmdd(analysis.get("E_DT_LEITURA_ATUAL"))
        next_reading_date = _parse_next_reading_date(
            analysis.get("E_PROX_LEIT"), current_reading_date
        )

        # Leituras de medidor são contadores inteiros (o registrador físico não
        # tem casa decimal), então arredondamos para int em vez de expor float.
        current_meter_reading = None
        leitura_item = next(
            (i for i in analysis.get("ET_MENU_RAPIDO", []) if i.get("ID") == "LEITATUAL"),
            None,
        )
        if leitura_item and leitura_item.get("VALOR2") not in (None, ""):
            try:
                current_meter_reading = round(float(leitura_item["VALOR2"]))
            except (TypeError, ValueError):
                current_meter_reading = None

        # A leitura anterior não é usada aqui: E_CONS_TOTAL/E_CONSTANTE deste
        # endpoint se referem ao ciclo de faturamento da última fatura, que
        # não bate com o "consumo do período" mostrado em outro sensor (esse
        # vem do endpoint getAnaliseConsumo). É calculada em async_get_all_data
        # a partir da leitura atual e do mesmo ATUAL_CONSUMO usado por aquele
        # sensor, para os dois números baterem.
        _LOGGER.debug(
            "Reading info extracted: current_reading_date=%s next_reading_date=%s "
            "current_meter_reading=%s "
            "(E_DT_LEITURA_ATUAL=%s E_PROX_LEIT=%s)",
            current_reading_date, next_reading_date, current_meter_reading,
            analysis.get("E_DT_LEITURA_ATUAL"), analysis.get("E_PROX_LEIT"),
        )

        bill_analysis_message = analysis.get("E_MSG") or analysis.get("DescripcionResultado") or ""

        daily_consumption_kwh = None
        if analysis.get("E_CONS_DIA") is not None:
            try:
                daily_consumption_kwh = float(analysis["E_CONS_DIA"])
            except (TypeError, ValueError):
                daily_consumption_kwh = None

        daily_amount = None
        if analysis.get("E_VALOR_DIA") is not None:
            try:
                daily_amount = float(analysis["E_VALOR_DIA"])
            except (TypeError, ValueError):
                daily_amount = None

        return {
            "current_reading_date": current_reading_date,
            "next_reading_date": next_reading_date,
            "current_meter_reading": current_meter_reading,
            "bill_analysis_message": bill_analysis_message,
            "daily_consumption_kwh": daily_consumption_kwh,
            "daily_amount": daily_amount,
        }

    async def async_get_all_data(self, installation: Installation) -> EnelSPData:
        consumption = await self.async_get_consumption(installation)
        bills = await self.async_get_bills(installation)
        history = await self.async_get_history(installation)

        installations = consumption.get("ET_INSTALACAO", [])
        current = next(
            (i for i in installations if i.get("ANLAGE") == installation.anlage),
            installations[0] if installations else {},
        )

        bill_list = bills.get("ET_CONTAS", [])
        # A mais recente entre as em aberto, pelo VENCIMENTO — não a primeira
        # do array na posição em que veio (a API não garante essa ordem,
        # mesmo problema já corrigido pra `latest_bill` abaixo).
        next_due = most_recent_bill([b for b in bill_list if b.get("SITUACAO") != "Paga"])

        reading_info: dict[str, Any] = {}
        latest_bill = most_recent_bill(bill_list)
        if latest_bill and latest_bill.get("BELNR"):
            _LOGGER.debug(
                "Fetching billanalysis for most recent bill: BELNR=%s VENCIMENTO=%s ANO_MES_REF=%s",
                latest_bill["BELNR"], latest_bill.get("VENCIMENTO"), latest_bill.get("ANO_MES_REF"),
            )
            analysis = await self.async_get_bill_analysis(installation, latest_bill["BELNR"])
            reading_info = self._extract_bill_analysis_info(analysis)

        # Leitura anterior = leitura atual - consumo do período atual (mesmo
        # ATUAL_CONSUMO do sensor "Consumo do período"), para os dois baterem.
        previous_meter_reading = None
        current_meter_reading = reading_info.get("current_meter_reading")
        current_consumption_kwh = current.get("ATUAL_CONSUMO")
        if current_meter_reading is not None and current_consumption_kwh is not None:
            try:
                previous_meter_reading = round(
                    current_meter_reading - float(current_consumption_kwh)
                )
            except (TypeError, ValueError):
                previous_meter_reading = None
        reading_info["previous_meter_reading"] = previous_meter_reading

        return EnelSPData(
            installation=installation,
            tariff_flag=self.tariff_flag,
            current_period=current.get("PERIODO", ""),
            current_consumption_kwh=current.get("ATUAL_CONSUMO"),
            bills=bill_list,
            next_due_bill=next_due,
            history=history.get("ET_MEDIA_CONS", []),
            supply_suspended=current.get("SUSPENSA") == "X",
            supply_suspended_message=current.get("MSG_SUSPENSAO") or "",
            **reading_info,
        )
