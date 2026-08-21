"""Testes unitários do cliente da API da Enel SP, usando fixtures anonimizadas
capturadas de uma sessão de login real (veja tests/fixtures/)."""
import base64
import json
from datetime import date
from pathlib import Path

import pytest

from custom_components.enel_sp.api import EnelSPClient, Installation

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _client() -> EnelSPClient:
    return EnelSPClient(session=None, username="user@example.com", password="secret")


class _FakeResponse:
    """Substituto mínimo de resposta do aiohttp, suporta `async with session.post(...)`."""

    def __init__(self, text: str = "", status: int = 200, reason: str = "OK", json_data=None):
        self._text = text
        self.status = status
        self.reason = reason
        self.url = "https://example.invalid/"
        self._json_data = json_data

    async def text(self):
        return self._text

    async def read(self):
        return self._text.encode()

    async def json(self, content_type=None):
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Registra cada chamada `.post(url, **kwargs)` para ser verificada nos testes."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


@pytest.mark.asyncio
async def test_submit_credentials_sends_exact_field_set_from_real_capture():
    """Teste de regressão: confere se o corpo do POST bate campo a campo com o
    do navegador real (capturado via HAR), incluindo o par "data" duplicado
    do qual o EnelCustomBasicAuthenticator do WSO2 pode depender."""
    from custom_components.enel_sp.const import SAMLSSO_URL

    saml_html = '<form><input type="hidden" name="SAMLResponse" value="RkFLRQ=="/></form>'
    session = _FakeSession(_FakeResponse(text=saml_html))
    client = EnelSPClient(session, username="user@example.com", password="p@ss!w0rd")

    result = await client._async_submit_credentials("session-data-key-123")

    assert result == "RkFLRQ=="
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == SAMLSSO_URL
    assert kwargs["data"] == [
        ("login_options", "Email"),
        ("data", "user@example.com"),
        ("data", "p@ss!w0rd"),
        ("username", "user@example.com"),
        ("password", "p@ss!w0rd"),
        ("tocommonauth", "true"),
        ("sessionDataKey", "session-data-key-123"),
    ]


@pytest.mark.asyncio
async def test_submit_credentials_sends_origin_host_referer_from_real_capture():
    from custom_components.enel_sp.const import ACCOUNTS_ORIGIN, WWW_ORIGIN

    saml_html = '<form><input type="hidden" name="SAMLResponse" value="RkFLRQ=="/></form>'
    session = _FakeSession(_FakeResponse(text=saml_html))
    client = EnelSPClient(session, username="user@example.com", password="p@ss!w0rd")

    await client._async_submit_credentials("session-data-key-123")

    _, kwargs = session.calls[0]
    assert kwargs["headers"] == {
        "Host": "accounts.enel.com",
        "Origin": WWW_ORIGIN,
        "Referer": f"{WWW_ORIGIN}/",
    }


@pytest.mark.asyncio
async def test_business_call_sends_origin_host_referer_per_target_host():
    from custom_components.enel_sp.const import PORTALINFO_URL, WWW_ORIGIN

    client = _client()
    client._jwt = "fake-jwt"
    session = _FakeSession(
        _FakeResponse(json_data={"Body": {"CodigoResultado": "", "ET_CONTAS": []}})
    )
    client._session = session
    installation = Installation(anlage="a", vertrag="v", vkont="k", partner="p")

    await client.async_get_bills(installation)

    _, kwargs = session.calls[0]
    assert kwargs["headers"]["Host"] == "exp-portalsp-pro.de-c1.eu1.cloudhub.io"
    assert kwargs["headers"]["Origin"] == WWW_ORIGIN
    assert kwargs["headers"]["Referer"] == f"{WWW_ORIGIN}/"


def test_most_recent_bill_picks_by_vencimento_not_array_order():
    from custom_components.enel_sp.api import most_recent_bill

    bills = [
        {"BELNR": "OLD", "VENCIMENTO": "20260110"},
        {"BELNR": "NEW", "VENCIMENTO": "20260910"},
        {"BELNR": "MID", "VENCIMENTO": "20260510"},
    ]
    assert most_recent_bill(bills)["BELNR"] == "NEW"


def test_most_recent_bill_empty_list_returns_none():
    from custom_components.enel_sp.api import most_recent_bill

    assert most_recent_bill([]) is None


@pytest.mark.asyncio
async def test_get_bill_pdf_decodes_base64_and_sends_expected_body():
    from custom_components.enel_sp.const import GENERATE_PDF_URL

    # A resposta real desse endpoint não tem envelope "Body" como os demais
    # (Header + campos soltos no nível raiz).
    fake_pdf_b64 = base64.b64encode(b"%PDF-1.4 fake content").decode()
    session = _FakeSession(_FakeResponse(json_data={
        "Header": {"IdPeticion": "x"},
        "CodigoResultado": "",
        "E_MSG": "",
        "E_BIN_FAT": fake_pdf_b64,
    }))
    client = _client()
    client._jwt = "fake-jwt"
    client._session = session
    installation = Installation(
        anlage="0069999999", vertrag="0003999999", vkont="100099999999", partner="0011111111"
    )
    bill = {"BELNR": "530338055643", "ORIGEM_DOC": "C"}

    pdf_bytes = await client.async_get_bill_pdf(installation, bill)

    assert pdf_bytes == b"%PDF-1.4 fake content"
    url, kwargs = session.calls[0]
    assert url == GENERATE_PDF_URL
    body = kwargs["json"]["Body"]
    assert body["I_BELNR"] == "530338055643"
    assert body["I_ORIGEM_DOC"] == "C"
    assert body["I_ANLAGE"] == "0069999999"
    assert body["I_VKONT"] == "100099999999"
    assert body["I_PARTNER"] == "0011111111"
    assert body["I_VERTRAG"] == "0003999999"
    assert kwargs["json"]["Header"]["Funcionalidad"] == "generatePdf"


@pytest.mark.asyncio
async def test_get_bill_pdf_raises_when_no_pdf_data():
    from custom_components.enel_sp.api import EnelSPApiError

    session = _FakeSession(_FakeResponse(json_data={
        "Header": {"IdPeticion": "x"}, "CodigoResultado": "", "E_MSG": "erro qualquer",
    }))
    client = _client()
    client._jwt = "fake-jwt"
    client._session = session
    installation = Installation(anlage="a", vertrag="v", vkont="k", partner="p")

    with pytest.raises(EnelSPApiError):
        await client.async_get_bill_pdf(installation, {"BELNR": "123"})


@pytest.mark.asyncio
async def test_submit_credentials_raises_auth_error_without_saml_response():
    session = _FakeSession(_FakeResponse(text="<html>Invalid credentials</html>"))
    client = EnelSPClient(session, username="user@example.com", password="wrong")

    from custom_components.enel_sp.api import EnelSPAuthError

    with pytest.raises(EnelSPAuthError):
        await client._async_submit_credentials("session-data-key-123")


def test_extract_saml_response_with_double_quotes():
    from custom_components.enel_sp.api import _extract_saml_response
    import html as html_mod

    html_body = (
        '<form><input type="hidden" name="SAMLResponse" '
        'value="PD94bWwgdmVyc2lvbj0mIzQzOw=="/></form>'
    )
    result = _extract_saml_response(html_body)
    assert result is not None
    assert html_mod.unescape(result) == "PD94bWwgdmVyc2lvbj0mIzQzOw=="


def test_extract_saml_response_with_single_quotes():
    """Regressão: a página real do WSO2 IS usa aspas simples nos atributos
    (`name='SAMLResponse' value='...'`), não aspas duplas — foi por isso que
    o login parava de funcionar em produção mesmo com credenciais corretas."""
    from custom_components.enel_sp.api import _extract_saml_response

    html_body = (
        "<form method='post' action='https://www.enel.com.br/pt-saopaulo/login.html'>"
        "<input type='hidden' name='SAMLResponse' value='PD94bWwgZmFrZQ=='>"
        "</form>"
    )
    assert _extract_saml_response(html_body) == "PD94bWwgZmFrZQ=="


def test_extract_saml_response_returns_none_when_absent():
    from custom_components.enel_sp.api import _extract_saml_response

    assert _extract_saml_response("<html>Invalid credentials</html>") is None


def test_get_installations_from_fixture():
    client = _client()
    client._raw_current_user = _load("currentuser")["currentUser"]

    installations = client.get_installations()

    assert len(installations) == 1
    inst = installations[0]
    assert inst.anlage == "0069999999"
    assert inst.vertrag == "0003999999"
    assert inst.vkont == "100099999999"
    assert inst.partner == "0011111111"
    assert inst.smart_meter is True
    assert inst.unique_id == "0069999999"


def test_tariff_flag_from_fixture():
    client = _client()
    client._raw_current_user = _load("currentuser")["currentUser"]
    assert client.tariff_flag == "AMARELA"


def test_envelope_has_expected_shape():
    client = _client()
    envelope = client._envelope("getAnaliseConsumo", {"I_CANAL": "ZINT"})
    assert envelope["Header"]["Funcionalidad"] == "getAnaliseConsumo"
    assert envelope["Header"]["CodSistema"] == "WEB"
    assert envelope["Body"] == {"I_CANAL": "ZINT"}
    # FechaHora deve parecer um timestamp ISO-8601 UTC, ex.: 2026-08-20T21:19:12.711Z
    assert envelope["Header"]["FechaHora"].endswith("Z")


def test_parse_yyyymmdd():
    from custom_components.enel_sp.api import _parse_yyyymmdd

    assert _parse_yyyymmdd("20260810") == date(2026, 8, 10)
    assert _parse_yyyymmdd("") is None
    assert _parse_yyyymmdd(None) is None
    assert _parse_yyyymmdd("not-a-date") is None


def test_parse_next_reading_date_same_year():
    from custom_components.enel_sp.api import _parse_next_reading_date

    reference = date(2026, 8, 10)
    assert _parse_next_reading_date("10 de Setembro", reference) == date(2026, 9, 10)


def test_parse_next_reading_date_wraps_to_next_year():
    from custom_components.enel_sp.api import _parse_next_reading_date

    reference = date(2026, 12, 20)
    assert _parse_next_reading_date("10 de Janeiro", reference) == date(2027, 1, 10)


def test_extract_bill_analysis_info_from_fixture():
    client = _client()
    analysis = _load("billanalisys")["Body"]

    info = client._extract_bill_analysis_info(analysis)

    assert info["current_reading_date"] == date(2026, 8, 10)
    assert info["next_reading_date"] == date(2026, 9, 10)
    assert info["current_meter_reading"] == 5024
    assert isinstance(info["current_meter_reading"], int)
    assert "previous_meter_reading" not in info
    assert info["bill_analysis_message"] == (
        "Você gastou R$ 10,55 a menos que o mês anterior e sua média diária "
        "desse mês foi de R$ 4,95."
    )
    assert info["daily_consumption_kwh"] == 5.42
    assert info["daily_amount"] == 4.95


@pytest.mark.asyncio
async def test_async_get_all_data_aggregates_fixtures(monkeypatch):
    client = _client()
    client._jwt = "fake-jwt"
    client._enel_id = "fake-enel-id"
    client._raw_current_user = _load("currentuser")["currentUser"]

    installation = client.get_installations()[0]

    async def fake_consumption(inst: Installation):
        assert inst is installation
        return _load("getAnaliseConsumo")["Body"]

    async def fake_bills(inst: Installation):
        return _load("portalinfo")["Body"]

    async def fake_history(inst: Installation):
        return _load("portalhistoryinfo")["Body"]

    async def fake_bill_analysis(inst: Installation, belnr: str):
        assert belnr == "000000000001"
        return _load("billanalisys")["Body"]

    monkeypatch.setattr(client, "async_get_consumption", fake_consumption)
    monkeypatch.setattr(client, "async_get_bills", fake_bills)
    monkeypatch.setattr(client, "async_get_history", fake_history)
    monkeypatch.setattr(client, "async_get_bill_analysis", fake_bill_analysis)

    data = await client.async_get_all_data(installation)

    assert data.installation is installation
    assert data.tariff_flag == "AMARELA"
    assert data.current_period == "Agosto/2026"
    assert data.current_consumption_kwh == 168
    assert data.current_amount == 153.44
    # A conta "Pendente" deve prevalecer sobre a que já está paga.
    assert data.next_due_bill["SITUACAO"] == "Pendente"
    assert data.next_due_bill["MONTANTE"] == 153.44
    assert len(data.bills) == 2
    assert len(data.history) == 2
    assert data.current_reading_date == date(2026, 8, 10)
    assert data.next_reading_date == date(2026, 9, 10)
    assert data.current_meter_reading == 5024
    # anterior = atual (5024) - consumo do período atual (168), o mesmo
    # ATUAL_CONSUMO usado pelo sensor "Consumo do período".
    assert data.previous_meter_reading == 4856
    assert data.supply_suspended is False
    assert data.supply_suspended_message == ""
    assert data.bill_analysis_message.startswith("Você gastou R$ 10,55 a menos")
    assert data.daily_consumption_kwh == 5.42
    assert data.daily_amount == 4.95


@pytest.mark.asyncio
async def test_async_get_all_data_flags_supply_suspended(monkeypatch):
    client = _client()
    client._jwt = "fake-jwt"
    client._enel_id = "fake-enel-id"
    client._raw_current_user = _load("currentuser")["currentUser"]
    installation = client.get_installations()[0]

    consumption = _load("getAnaliseConsumo")["Body"]
    consumption["ET_INSTALACAO"][0]["SUSPENSA"] = "X"
    consumption["ET_INSTALACAO"][0]["MSG_SUSPENSAO"] = "Fornecimento suspenso por falta de pagamento"

    async def fake_consumption(inst):
        return consumption

    async def fake_bills(inst):
        return {"ET_CONTAS": []}

    async def fake_history(inst):
        return {"ET_MEDIA_CONS": []}

    monkeypatch.setattr(client, "async_get_consumption", fake_consumption)
    monkeypatch.setattr(client, "async_get_bills", fake_bills)
    monkeypatch.setattr(client, "async_get_history", fake_history)

    data = await client.async_get_all_data(installation)

    assert data.supply_suspended is True
    assert data.supply_suspended_message == "Fornecimento suspenso por falta de pagamento"


@pytest.mark.asyncio
async def test_async_get_all_data_previous_meter_reading_ignores_billanalysis_constante(monkeypatch):
    """Regressão: E_CONS_TOTAL/E_CONSTANTE do billanalysis se referem ao ciclo
    de faturamento da última fatura, não ao "consumo do período atual" (esse é
    ATUAL_CONSUMO, do getAnaliseConsumo). Usar E_CONS_TOTAL/E_CONSTANTE fazia a
    leitura anterior bater com o número errado (ex.: 4520 em vez de 4856)."""
    client = _client()
    client._jwt = "fake-jwt"
    client._enel_id = "fake-enel-id"
    client._raw_current_user = _load("currentuser")["currentUser"]
    installation = client.get_installations()[0]

    billanalysis = _load("billanalisys")["Body"]
    billanalysis["E_CONS_TOTAL"] = 504
    billanalysis["E_CONSTANTE"] = 1

    async def fake_consumption(inst):
        return _load("getAnaliseConsumo")["Body"]

    async def fake_bills(inst):
        return _load("portalinfo")["Body"]

    async def fake_history(inst):
        return _load("portalhistoryinfo")["Body"]

    async def fake_bill_analysis(inst, belnr):
        return billanalysis

    monkeypatch.setattr(client, "async_get_consumption", fake_consumption)
    monkeypatch.setattr(client, "async_get_bills", fake_bills)
    monkeypatch.setattr(client, "async_get_history", fake_history)
    monkeypatch.setattr(client, "async_get_bill_analysis", fake_bill_analysis)

    data = await client.async_get_all_data(installation)

    assert data.current_meter_reading == 5024
    assert data.current_consumption_kwh == 168
    assert data.previous_meter_reading == 4856


@pytest.mark.asyncio
async def test_async_get_all_data_picks_most_recent_bill_by_vencimento_not_array_order(monkeypatch):
    """Regressão: a fatura mais recente deve ser escolhida pelo VENCIMENTO,
    não pela posição no array (a API não garante essa ordem)."""
    client = _client()
    client._jwt = "fake-jwt"
    client._enel_id = "fake-enel-id"
    client._raw_current_user = _load("currentuser")["currentUser"]
    installation = client.get_installations()[0]

    bills_out_of_order = {
        "ET_CONTAS": [
            {"BELNR": "OLD", "VENCIMENTO": "20260110", "SITUACAO": "Paga", "MONTANTE": 10},
            {"BELNR": "NEW", "VENCIMENTO": "20260910", "SITUACAO": "Pendente", "MONTANTE": 20},
        ]
    }

    async def fake_consumption(inst):
        return _load("getAnaliseConsumo")["Body"]

    async def fake_bills(inst):
        return bills_out_of_order

    async def fake_history(inst):
        return _load("portalhistoryinfo")["Body"]

    seen_belnr = []

    async def fake_bill_analysis(inst, belnr):
        seen_belnr.append(belnr)
        return _load("billanalisys")["Body"]

    monkeypatch.setattr(client, "async_get_consumption", fake_consumption)
    monkeypatch.setattr(client, "async_get_bills", fake_bills)
    monkeypatch.setattr(client, "async_get_history", fake_history)
    monkeypatch.setattr(client, "async_get_bill_analysis", fake_bill_analysis)

    await client.async_get_all_data(installation)

    assert seen_belnr == ["NEW"]


def test_build_consumption_statistics_drops_current_month_and_chains_hourly():
    from custom_components.enel_sp.api import build_consumption_statistics

    monthly_history = _load("portalhistoryinfo")["Body"]["ET_MEDIA_CONS"]
    hourly_data = _load("smartmetergetconsumptionchartdata")["Body"]["T_GRAPHIC_HOUR"]

    points = build_consumption_statistics(monthly_history, hourly_data)

    # O fixture tem 2 meses (07/2026 e 08/2026); o mais recente (08/2026) é
    # descartado por se sobrepor à janela horária -> sobra 1 ponto mensal.
    # Dos 4 pontos horários, só 3 são do registrador "03" (energia ativa); o
    # de registrador "04" é ignorado.
    assert len(points) == 1 + 3

    monthly_points = points[:1]
    hourly_points = points[1:]

    assert monthly_points[-1]["sum"] == 173.0
    # Regressão: precisa ser meia-noite em horário de São Paulo, não UTC —
    # meia-noite UTC do dia 1 é 21h do dia 30 do mês anterior em horário
    # local, e o HA agrupa estatísticas por "Mês" usando o fuso local, então
    # UTC jogava esse ponto inteiro pro mês errado.
    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo

    sp_tz = ZoneInfo("America/Sao_Paulo")
    assert monthly_points[0]["start"] == _datetime(2026, 7, 1, tzinfo=sp_tz)
    assert monthly_points[0]["start"].astimezone(sp_tz).month == 7

    # Pontos horários em ordem cronológica, continuando a soma cumulativa.
    assert [p["start"].hour for p in hourly_points] == [22, 23, 0]
    assert hourly_points[-1]["sum"] == 173.0 + 0.10 + 2.13 + 0.23

    # "state" acompanha "sum": cartões genéricos que pedem "Estado" (em vez
    # de "Soma", o tipo que o Painel de Energia usa) também precisam ter dado.
    assert all(p["state"] == p["sum"] for p in points)


def test_build_consumption_statistics_empty_inputs():
    from custom_components.enel_sp.api import build_consumption_statistics

    assert build_consumption_statistics([], []) == []


@pytest.mark.asyncio
async def test_smart_meter_chart_data_request_shape():
    from custom_components.enel_sp.const import SMARTMETER_CHART_URL

    client = _client()
    client._jwt = "fake-jwt"
    session = _FakeSession(_FakeResponse(json_data=_load("smartmetergetconsumptionchartdata")))
    client._session = session
    installation = Installation(
        anlage="0069999999", vertrag="0003999999", vkont="100099999999",
        partner="0011111111", serial="FAKE000000000",
    )

    await client.async_get_smart_meter_chart_data(installation)

    url, kwargs = session.calls[0]
    assert url == SMARTMETER_CHART_URL
    body = kwargs["json"]["Body"]
    assert body["Contract"] == "0003999999"
    assert body["ContractAccount"] == "100099999999"
    assert body["PartnerNumber"] == "0011111111"
    assert body["InstallationNumber"] == "0069999999"
    assert body["Meter"] == "FAKE000000000"
    assert body["ServiceCode"] == "GF"
    assert body["TimeScale"] == "D"
    assert body["T_REGISTERS"] == ["03", "04", "06", "08"]
    assert kwargs["json"]["Header"]["Funcionalidad"] == "SmartMeter"
