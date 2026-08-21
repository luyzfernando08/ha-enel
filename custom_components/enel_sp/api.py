"""Cliente da API do portal de clientes da Enel São Paulo.

Reproduz o fluxo de login do navegador em https://www.enel.com.br/pt-saopaulo/login.html:

1. GET no ponto de entrada do SAML SSO para obter um ``sessionDataKey`` do
   WSO2 Identity Server.
2. POST das credenciais no mesmo endpoint de SAML SSO, que devolve um form
   HTML de auto-submit contendo um ``SAMLResponse`` em base64 (SAML2 HTTP-POST
   binding).
3. POST desse ``SAMLResponse`` na Assertion Consumer Service URL do site, que
   estabelece uma sessão autenticada (cookies) com o backend Adobe AEM.
4. Chamada ao servlet ``currentuser`` para obter o ``access_token`` da conta
   (um JWT de curta duração) e os identificadores SAP IS-U
   (ANLAGE/VERTRAG/VKONT/PARTNER) necessários para consultar as APIs de
   negócio hospedadas no Mulesoft.

O header ``SID`` usado nessas APIs de negócio não é emitido pelo servidor: o
app web oficial o gera no cliente com ``crypto.randomUUID()`` e o reaproveita
durante a sessão, então fazemos o mesmo.
"""
from __future__ import annotations

import base64
import html
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from .const import (
    ACCOUNTS_ORIGIN,
    ACS_URL,
    ANALISE_CONSUMO_URL,
    BILLANALYSIS_URL,
    CANAL,
    COD_SISTEMA,
    CURRENTUSER_URL,
    GENERATE_PDF_URL,
    PORTALHISTORYINFO_URL,
    PORTALINFO_URL,
    SAMLSSO_URL,
    SMARTMETER_ACTIVE_ENERGY_REGISTER,
    SMARTMETER_CHART_URL,
    WWW_ORIGIN,
)

_LOGGER = logging.getLogger(__name__)


def _host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


# O formulário de auto-submit do WSO2 IS usa aspas simples nos atributos
# (`name='SAMLResponse' value='...'`), diferente do que a maioria dos exemplos
# de SAML por aí mostra (aspas duplas) — por isso aceitamos os dois estilos e
# não fixamos a ordem dos atributos dentro da tag <input>.
_SAML_INPUT_TAG_RE = re.compile(
    r"<input\b[^>]*\bname=['\"]SAMLResponse['\"][^>]*>", re.IGNORECASE
)
_VALUE_ATTR_RE = re.compile(r"value=['\"]([^'\"]*)['\"]")


def _extract_saml_response(html_text: str) -> str | None:
    tag_match = _SAML_INPUT_TAG_RE.search(html_text)
    if not tag_match:
        return None
    value_match = _VALUE_ATTR_RE.search(tag_match.group(0))
    if not value_match:
        return None
    return value_match.group(1)

# Quantos caracteres de uma resposta de erro HTML/JSON inesperada logar em
# nível DEBUG quando uma etapa falha, para ajudar no diagnóstico sem lotar o log.
_DEBUG_SNIPPET_LEN = 1500

_PT_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11,
    "dezembro": 12,
}
_NEXT_READING_RE = re.compile(r"(\d{1,2})\s+de\s+([A-Za-zçÇ]+)", re.IGNORECASE)

# Abreviação de 3 letras usada em T_GRAPHIC_MONTH (diferente do texto por
# extenso de E_PROX_LEIT, daí um mapa separado de _PT_MONTHS).
_PT_MONTH_ABBR = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}


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


# As UCs da Enel SP ficam todas na região de São Paulo, então usamos esse
# fuso fixo pra interpretar as datas/horas do medidor — não o fuso do
# servidor onde o Home Assistant roda, que pode ser outro.
_SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


def _format_sm_date(value: datetime) -> str:
    # O app web monta esse timestamp com os componentes de hora LOCAL e cola
    # um "Z" no final mesmo não sendo UTC de verdade (bug deles). Reproduzimos
    # o mesmo formato porque é o que o backend espera receber.
    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _smart_meter_date_range(now: datetime | None = None) -> tuple[str, str]:
    """Últimos 7 dias terminando ontem — a mesma janela que o app web pede.

    O dia de hoje nunca é incluído: o medidor ainda não fechou a leitura dele.
    """
    reference = (now or datetime.now(_SAO_PAULO_TZ)).astimezone(_SAO_PAULO_TZ)
    end = (reference - timedelta(days=1)).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    start = (end - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    return _format_sm_date(start), _format_sm_date(end)


def smart_meter_month_history(chart_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Converte ``T_GRAPHIC_MONTH`` (do próprio ``smartmetergetconsumptionchartdata``)
    para o mesmo formato do ``ET_MEDIA_CONS`` (``portalhistoryinfo``), pra dar
    pra reaproveitar ``build_consumption_statistics`` sem duplicar o parsing.

    Ao contrário de ``T_GRAPHIC_HOUR``, que só traz os dias dentro do
    ``StartDate``/``EndDate`` pedido, ``T_GRAPHIC_MONTH`` devolve o histórico
    mensal inteiro do medidor **independente da janela pedida** — confirmado
    numa captura real, em que uma janela de 8 dias trouxe 11 meses de
    histórico junto. Por isso essa é a fonte preferida do histórico mensal
    (mais completa que o ``portalhistoryinfo``, que costuma trazer bem menos
    meses).
    """
    months: list[dict[str, Any]] = []
    for item in chart_data.get("T_GRAPHIC_MONTH", []):
        mes = _PT_MONTH_ABBR.get(str(item.get("Month", "")).upper())
        ano_raw = item.get("Year")
        consumo = item.get("ConsumoKW")
        if mes is None or not ano_raw or consumo is None:
            continue
        try:
            ano = int(ano_raw)
            ano = 2000 + ano if ano < 100 else ano
            months.append(
                {
                    "MESREF": f"{mes:02d}/{ano}",
                    "CONSUMO": float(consumo),
                    # Data real de fechamento do ciclo (YYYYMMDD) — usada por
                    # build_consumption_statistics pra saber com precisão
                    # quais meses já fecharam de verdade, em vez de assumir
                    # que é sempre o último item do array (ver LastReading).
                    "DATA_FECHAMENTO": str(item.get("Date") or ""),
                }
            )
        except (TypeError, ValueError):
            continue
    return months


def build_consumption_statistics(
    monthly_history: list[dict[str, Any]],
    hourly_data: list[dict[str, Any]],
    last_reading: str | None = None,
) -> list[dict[str, Any]]:
    """Monta a série de consumo (kWh) usada para alimentar o Painel de Energia.

    Usa os meses já fechados do histórico mensal como base histórica,
    excluindo o(s) que ainda não fecharam de verdade — eles cobrem o mesmo
    período que os dados horários do medidor inteligente abaixo, e somar os
    dois contaria o mesmo consumo duas vezes. Em seguida, continua a soma
    cumulativa com os dados horários reais do medidor (``T_GRAPHIC_HOUR``,
    registrador de energia ativa).

    ``monthly_history`` normalmente vem de ``smart_meter_month_history()``
    (preferencial) ou do ``ET_MEDIA_CONS`` do ``portalhistoryinfo``
    (fallback) — mesmo formato ``{"MESREF": "MM/AAAA", "CONSUMO": kWh}`` nos
    dois casos.

    ``last_reading`` é o ``LastReading`` (``YYYYMMDD``) do
    ``smartmetergetconsumptionchartdata`` — a data em que o ciclo de leitura
    mais recente fechou de verdade segundo o próprio medidor. Isso importa
    porque o ciclo de leitura **não é necessariamente alinhado ao mês
    calendário** (o dia do mês em que fecha varia por conta e pode até mudar
    ao longo do tempo — não é um valor fixo, por isso sempre lemos
    ``LastReading`` em vez de assumir um dia): sem ``last_reading``, cai no
    fallback de assumir que é sempre o último item do array que ainda está
    em andamento (correto na maioria das vezes, mas impreciso perto da
    virada do mês quando o ciclo não fecha no dia 1). Com ``last_reading``,
    mês e hora são filtrados pela mesma fronteira real
    (``DATA_FECHAMENTO``/``Date`` <= ou > ``last_reading``), o que evita tanto
    contar consumo em dobro quanto deixar uma lacuna nos dias entre a virada
    do mês calendário e o fechamento de fato do ciclo.

    Sempre recalcula a série inteira a partir do zero: como
    ``async_add_external_statistics`` faz upsert por timestamp, reenviar os
    mesmos pontos em cada atualização é seguro (idempotente) e nunca conta
    consumo duas vezes nem deixa a soma cumulativa diminuir.
    """
    months: list[tuple[str, datetime, float, str]] = []
    for item in monthly_history:
        mesref = item.get("MESREF")
        consumo = item.get("CONSUMO")
        if not mesref or consumo is None or "/" not in mesref:
            continue
        try:
            mm, yyyy = mesref.split("/")
            sort_key = f"{yyyy}{mm}"
            # Precisa ser fuso horário de São Paulo, igual aos pontos horários
            # abaixo: meia-noite UTC do dia 1 é 21h do dia 30 do mês anterior
            # em horário local, e o HA agrupa "Mês" pelo fuso local — isso
            # jogava o ponto inteiro pro mês errado (o anterior).
            start = datetime(int(yyyy), int(mm), 1, tzinfo=_SAO_PAULO_TZ)
            fechamento = str(item.get("DATA_FECHAMENTO") or "")
            months.append((sort_key, start, float(consumo), fechamento))
        except (ValueError, TypeError):
            continue
    months.sort(key=lambda m: m[0])

    if last_reading and all(m[3] for m in months):
        months = [m for m in months if m[3] <= last_reading]
    else:
        months = months[:-1]

    hours: list[tuple[datetime, float]] = []
    for item in hourly_data:
        if item.get("Register") != SMARTMETER_ACTIVE_ENERGY_REGISTER:
            continue
        date_str, time_str, consumo_str = (
            item.get("Date"), item.get("Time"), item.get("ConsumoKW")
        )
        if not date_str or not time_str or consumo_str is None:
            continue
        if last_reading and date_str <= last_reading:
            # Já coberto pelo ciclo fechado correspondente em `months` —
            # incluir de novo aqui contaria o mesmo consumo duas vezes.
            continue
        try:
            start = datetime.strptime(
                f"{date_str}{time_str}", "%Y%m%d%H%M%S"
            ).replace(tzinfo=_SAO_PAULO_TZ)
            hours.append((start, float(consumo_str)))
        except (ValueError, TypeError):
            continue
    hours.sort(key=lambda h: h[0])

    # "state" fica igual a "sum": é um registrador cumulativo (como o do
    # medidor físico), não um sensor com leitura instantânea própria. Sem
    # isso, cartões genéricos de estatística que pedem "Estado" em vez de
    # "Soma" (o tipo que o Painel de Energia usa) mostram o gráfico vazio.
    running_sum = 0.0
    statistics: list[dict[str, Any]] = []
    for _, start, consumo, _fechamento in months:
        running_sum += consumo
        statistics.append({"start": start, "sum": running_sum, "state": running_sum})
    for start, consumo in hours:
        running_sum += consumo
        statistics.append({"start": start, "sum": running_sum, "state": running_sum})

    return statistics


class EnelSPError(Exception):
    """Erro base do cliente da Enel SP."""


class EnelSPAuthError(EnelSPError):
    """Levantado quando o login falha (credenciais erradas ou página de login inesperada)."""


class EnelSPApiError(EnelSPError):
    """Levantado quando uma chamada de API de negócio falha ou retorna um erro."""


@dataclass
class Installation:
    """Uma unidade consumidora (UC) vinculada à conta."""

    anlage: str
    vertrag: str
    vkont: str
    partner: str
    address: str = ""
    nickname: str = ""
    smart_meter: bool = False
    serial: str = ""

    @property
    def unique_id(self) -> str:
        return self.anlage


@dataclass
class EnelSPData:
    """Dados agregados de uma unidade consumidora, consumidos pelo coordinator."""

    installation: Installation
    tariff_flag: str = ""
    current_period: str = ""
    current_consumption_kwh: float | None = None
    current_amount: float | None = None
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


class EnelSPClient:
    """Conversa com accounts.enel.com / www.enel.com.br / as APIs do Mulesoft."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._sid = str(uuid.uuid4())
        self._jwt: str | None = None
        self._enel_id: str | None = None
        self._raw_current_user: dict[str, Any] = {}

    async def async_login(self) -> dict[str, Any]:
        """Executa o fluxo de login SAML completo e busca o perfil da conta.

        Retorna o payload bruto ``currentUser`` (unidades consumidoras, bandeira
        tarifária, ...).
        """
        _LOGGER.debug("Starting login (sid=%s)", self._sid)
        session_data_key = await self._async_get_session_data_key()
        saml_response = await self._async_submit_credentials(session_data_key)
        await self._async_submit_saml_response(saml_response)
        current_user = await self._async_fetch_current_user()
        _LOGGER.debug(
            "Login finished: %d installation(s) found", len(current_user.get("ET_INST", []))
        )
        return current_user

    async def _async_get_session_data_key(self) -> str:
        # Captura real do navegador: navegação simples de topo, sem Origin/Referer.
        # Sem header Host explícito aqui: esse GET redireciona entre hosts
        # diferentes (accounts.enel.com -> www.enel.com.br), e o aiohttp não
        # atualiza um Host definido explicitamente ao longo dos redirects, só
        # o que ele mesmo calcula automaticamente — um valor explícito ficaria
        # desatualizado e quebraria o último salto.
        async with self._session.get(SAMLSSO_URL, allow_redirects=True) as resp:
            final_url = resp.url
            session_data_key = final_url.query.get("sessionDataKey")
            _LOGGER.debug(
                "GET %s -> %s %s (final URL host=%s path=%s, sessionDataKey found=%s)",
                SAMLSSO_URL, resp.status, resp.reason, final_url.host, final_url.path,
                bool(session_data_key),
            )
            if not session_data_key:
                body = await resp.text()
                _LOGGER.debug(
                    "No sessionDataKey in final redirect URL; response body follows:\n%s",
                    body[:_DEBUG_SNIPPET_LEN],
                )
        if not session_data_key:
            raise EnelSPAuthError("Could not obtain sessionDataKey from accounts.enel.com")
        return session_data_key

    async def _async_submit_credentials(self, session_data_key: str) -> str:
        # Corpo do POST idêntico, campo a campo, ao do navegador real
        # (capturado via HAR), incluindo o par "data" aparentemente redundante:
        # o EnelCustomBasicAuthenticator do WSO2 pode depender de qualquer uma
        # das duas representações.
        payload = [
            ("login_options", "Email"),
            ("data", self._username),
            ("data", self._password),
            ("username", self._username),
            ("password", self._password),
            ("tocommonauth", "true"),
            ("sessionDataKey", session_data_key),
        ]
        headers = {
            "Host": _host_of(SAMLSSO_URL),
            "Origin": WWW_ORIGIN,
            "Referer": f"{WWW_ORIGIN}/",
        }
        async with self._session.post(SAMLSSO_URL, data=payload, headers=headers) as resp:
            text = await resp.text()
            _LOGGER.debug(
                "POST %s -> %s %s (%d bytes)", SAMLSSO_URL, resp.status, resp.reason, len(text)
            )

        saml_response = _extract_saml_response(text)
        if saml_response is None:
            _LOGGER.debug(
                "No SAMLResponse in the login result; response body follows "
                "(look for an error/CAPTCHA message from Enel):\n%s",
                text[:_DEBUG_SNIPPET_LEN],
            )
            raise EnelSPAuthError("Login failed: no SAMLResponse in the login result (check credentials)")
        return html.unescape(saml_response)

    async def _async_submit_saml_response(self, saml_response: str) -> None:
        # Esse POST redireciona internamente (logininterceptor -> post-login.html),
        # mas fica em www.enel.com.br o tempo todo, então um Host explícito é seguro.
        headers = {
            "Host": _host_of(ACS_URL),
            "Origin": ACCOUNTS_ORIGIN,
            "Referer": f"{ACCOUNTS_ORIGIN}/",
        }
        async with self._session.post(
            ACS_URL, data={"SAMLResponse": saml_response}, headers=headers, allow_redirects=True
        ) as resp:
            body = await resp.read()
            _LOGGER.debug(
                "POST %s -> %s %s (final URL=%s, %d bytes)",
                ACS_URL, resp.status, resp.reason, resp.url, len(body),
            )

    async def _async_fetch_current_user(self) -> dict[str, Any]:
        headers = {
            "sid": self._sid,
            "Host": _host_of(CURRENTUSER_URL),
            "Origin": WWW_ORIGIN,
            "Referer": f"{WWW_ORIGIN}/pt-saopaulo/servico/post-login.html",
        }
        async with self._session.post(
            CURRENTUSER_URL,
            json={},
            headers=headers,
        ) as resp:
            _LOGGER.debug("POST %s -> %s %s", CURRENTUSER_URL, resp.status, resp.reason)
            if resp.status != 200:
                body = await resp.text()
                _LOGGER.debug("currentuser error body:\n%s", body[:_DEBUG_SNIPPET_LEN])
                raise EnelSPAuthError(f"currentuser call failed with status {resp.status}")
            payload = await resp.json(content_type=None)

        current_user = payload.get("currentUser") or {}
        self._jwt = current_user.get("access_token")
        self._enel_id = current_user.get("enel_id")
        if not self._jwt:
            _LOGGER.debug(
                "currentuser response had no access_token; top-level keys=%s, status=%s",
                list(payload.keys()), payload.get("status"),
            )
            raise EnelSPAuthError("Login succeeded but no access_token was returned")

        self._raw_current_user = current_user
        return current_user

    def get_installations(self) -> list[Installation]:
        """Retorna as unidades consumidoras extraídas do último payload currentUser."""
        installations = []
        for inst in self._raw_current_user.get("ET_INST", []):
            installations.append(
                Installation(
                    anlage=inst.get("ANLAGE", ""),
                    vertrag=inst.get("VERTRAG", ""),
                    vkont=inst.get("VKONT", ""),
                    partner=inst.get("PARTNER", ""),
                    address=inst.get("ENDERECO", ""),
                    nickname=inst.get("APELIDO", ""),
                    smart_meter=inst.get("SMARTMETER") == "X",
                    serial=inst.get("SERIE", ""),
                )
            )
        return installations

    @property
    def tariff_flag(self) -> str:
        return self._raw_current_user.get("E_BANDEIRA", "")

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
            PORTALINFO_URL,
            "portalinfo",
            {
                "I_CANAL": CANAL,
                "I_COD_SERV": "TC",
                "I_SERVICOS": "X",
                "I_PREFERENCIA_FULL": "",
                "I_ALERTAS_FULL": "",
                "I_CONTAS": "X",
                "I_ENELID": self._enel_id or "",
                "I_ANLAGE": installation.anlage,
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

    async def async_get_smart_meter_chart_data(self, installation: Installation) -> dict[str, Any]:
        """Consumo por hora/dia/mês do medidor inteligente (só UCs com SMARTMETER=X).

        Usada para alimentar estatísticas do Painel de Energia, não para sensores.
        """
        start_date, end_date = _smart_meter_date_range()
        return await self._async_post_business(
            SMARTMETER_CHART_URL,
            "SmartMeter",
            {
                "Contract": installation.vertrag,
                "ContractAccount": installation.vkont,
                "PartnerNumber": installation.partner,
                "AtendCanal": CANAL,
                "ServiceCode": "GF",
                "InstallationNumber": installation.anlage,
                "Meter": installation.serial,
                "StartDate": start_date,
                "EndDate": end_date,
                "TimeScale": "D",
                "T_REGISTERS": ["03", "04", "06", "08"],
                "I_CANAL": CANAL,
                "I_VKONT": installation.vkont,
                "I_VERTRAG": installation.vertrag,
                "I_PARTNER": installation.partner,
            },
        )

    async def async_get_bill_pdf(self, installation: Installation, bill: dict[str, Any]) -> bytes:
        """Baixa o PDF de uma fatura e devolve os bytes já decodificados.

        Ao contrário de todo o resto da API, a resposta desse endpoint não
        vem dentro de um envelope ``Body`` — os campos ficam soltos no nível
        raiz, então não dá pra reaproveitar ``_async_post_business`` aqui.
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
        async with self._session.post(
            GENERATE_PDF_URL, json=payload, headers=self._business_headers(GENERATE_PDF_URL)
        ) as resp:
            _LOGGER.debug("POST %s (generatePdf) -> %s %s", GENERATE_PDF_URL, resp.status, resp.reason)
            if resp.status != 200:
                text = await resp.text()
                _LOGGER.debug("generatePdf error body:\n%s", text[:_DEBUG_SNIPPET_LEN])
                raise EnelSPApiError(f"generatePdf call failed with status {resp.status}")
            data = await resp.json(content_type=None)

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
        next_due = next((b for b in bill_list if b.get("SITUACAO") != "Paga"), None)

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
            current_amount=current.get("ATUAL_VALOR"),
            bills=bill_list,
            next_due_bill=next_due,
            history=history.get("ET_MEDIA_CONS", []),
            supply_suspended=current.get("SUSPENSA") == "X",
            supply_suspended_message=current.get("MSG_SUSPENSAO") or "",
            **reading_info,
        )
