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


class _FakeSequentialSession:
    """Como `_FakeSession`, mas devolve uma resposta diferente por chamada
    (na ordem dada) — usada pra testar retry."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses[len(self.calls) - 1]


@pytest.mark.asyncio
async def test_business_call_sends_origin_host_referer_per_target_host():
    from custom_components.enel_sp.const import WWW_ORIGIN

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
async def test_get_bill_pdf_retries_once_on_server_error(monkeypatch):
    """Regressão: generatePdf falha esporadicamente com 500 do lado da Enel
    (sem relação com o payload) — uma segunda tentativa costuma resolver."""
    import custom_components.enel_sp.api as api_module

    monkeypatch.setattr(api_module.asyncio, "sleep", lambda *_a, **_kw: _noop())

    fake_pdf_b64 = base64.b64encode(b"%PDF-1.4 fake content").decode()
    session = _FakeSequentialSession([
        _FakeResponse(status=500, reason="Internal Server Error", text="deu ruim"),
        _FakeResponse(json_data={
            "Header": {"IdPeticion": "x"}, "CodigoResultado": "", "E_MSG": "",
            "E_BIN_FAT": fake_pdf_b64,
        }),
    ])
    client = _client()
    client._jwt = "fake-jwt"
    client._session = session
    installation = Installation(anlage="a", vertrag="v", vkont="k", partner="p")

    pdf_bytes = await client.async_get_bill_pdf(installation, {"BELNR": "123"})

    assert pdf_bytes == b"%PDF-1.4 fake content"
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_get_bill_pdf_gives_up_after_max_attempts(monkeypatch):
    from custom_components.enel_sp.api import EnelSPApiError
    import custom_components.enel_sp.api as api_module

    monkeypatch.setattr(api_module.asyncio, "sleep", lambda *_a, **_kw: _noop())

    session = _FakeSequentialSession([
        _FakeResponse(status=500, reason="Internal Server Error", text="deu ruim"),
        _FakeResponse(status=500, reason="Internal Server Error", text="deu ruim de novo"),
    ])
    client = _client()
    client._jwt = "fake-jwt"
    client._session = session
    installation = Installation(anlage="a", vertrag="v", vkont="k", partner="p")

    with pytest.raises(EnelSPApiError):
        await client.async_get_bill_pdf(installation, {"BELNR": "123"})

    assert len(session.calls) == 2


async def _noop():
    return None


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
        return _load("getClientBills")["Body"]

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
    # "Consumo do período" continua vindo do getAnaliseConsumo (mesmo ciclo
    # já fechado usado por current_meter_reading/previous_meter_reading).
    assert data.current_consumption_kwh == 168
    # A conta "Pendente" deve prevalecer sobre a que já está paga.
    assert data.next_due_bill["SITUACAO"] == "Pendente"
    assert data.next_due_bill["MONTANTE"] == 153.44
    assert data.next_due_bill["QRCODE"].startswith("00020126580014BR.GOV.BCB.")
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
async def test_async_get_all_data_no_pix_when_no_pending_bill(monkeypatch):
    """Regressão: sem conta em aberto, next_due_bill (fonte do sensor/imagem
    de Pix) precisa ficar None mesmo que uma fatura já paga tenha QRCODE
    preenchido (a Enel manda QRCODE em contas pagas também)."""
    client = _client()
    client._jwt = "fake-jwt"
    client._enel_id = "fake-enel-id"
    client._raw_current_user = _load("currentuser")["currentUser"]
    installation = client.get_installations()[0]

    all_paid = {
        "ET_CONTAS": [
            {
                "BELNR": "PAID",
                "SITUACAO": "Paga",
                "VENCIMENTO": "20260810",
                "MONTANTE": 163.99,
                "QRCODE": "00020126580014BR.GOV.BCB.PAIDCODE",
            }
        ]
    }

    async def fake_consumption(inst):
        return _load("getAnaliseConsumo")["Body"]

    async def fake_bills(inst):
        return all_paid

    async def fake_history(inst):
        return {"ET_MEDIA_CONS": []}

    async def fake_bill_analysis(inst, belnr):
        return _load("billanalisys")["Body"]

    monkeypatch.setattr(client, "async_get_consumption", fake_consumption)
    monkeypatch.setattr(client, "async_get_bills", fake_bills)
    monkeypatch.setattr(client, "async_get_history", fake_history)
    monkeypatch.setattr(client, "async_get_bill_analysis", fake_bill_analysis)

    data = await client.async_get_all_data(installation)

    assert data.next_due_bill is None


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
        return _load("getClientBills")["Body"]

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


@pytest.mark.asyncio
async def test_async_get_all_data_next_due_bill_picks_most_recent_pending(monkeypatch):
    """Regressão: entre várias faturas em aberto fora de ordem no array,
    "Última fatura fechada" precisa ser a de VENCIMENTO mais recente — não a
    primeira do array que estiver com SITUACAO != "Paga"."""
    client = _client()
    client._jwt = "fake-jwt"
    client._enel_id = "fake-enel-id"
    client._raw_current_user = _load("currentuser")["currentUser"]
    installation = client.get_installations()[0]

    bills_out_of_order = {
        "ET_CONTAS": [
            {"BELNR": "NEWER", "VENCIMENTO": "20260910", "SITUACAO": "Pendente", "MONTANTE": 20},
            {"BELNR": "OLDER", "VENCIMENTO": "20260810", "SITUACAO": "Pendente", "MONTANTE": 10},
        ]
    }

    async def fake_consumption(inst):
        return _load("getAnaliseConsumo")["Body"]

    async def fake_bills(inst):
        return bills_out_of_order

    async def fake_history(inst):
        return _load("portalhistoryinfo")["Body"]

    async def fake_bill_analysis(inst, belnr):
        return _load("billanalisys")["Body"]

    monkeypatch.setattr(client, "async_get_consumption", fake_consumption)
    monkeypatch.setattr(client, "async_get_bills", fake_bills)
    monkeypatch.setattr(client, "async_get_history", fake_history)
    monkeypatch.setattr(client, "async_get_bill_analysis", fake_bill_analysis)

    data = await client.async_get_all_data(installation)

    assert data.next_due_bill["BELNR"] == "NEWER"


def test_compute_next_update_interval_uses_next_reading_plus_one_day():
    from datetime import date, datetime, timedelta
    from custom_components.enel_sp.api import compute_next_update_interval
    from custom_components.enel_sp.const import SAO_PAULO_TZ

    now = datetime(2026, 8, 10, 12, 0, tzinfo=SAO_PAULO_TZ)
    interval = compute_next_update_interval(date(2026, 9, 10), now=now)

    expected_target = datetime(2026, 9, 11, 0, 0, tzinfo=SAO_PAULO_TZ)
    assert interval == expected_target - now


def test_compute_next_update_interval_falls_back_when_missing():
    from datetime import datetime
    from custom_components.enel_sp.api import compute_next_update_interval
    from custom_components.enel_sp.const import DEFAULT_UPDATE_INTERVAL, SAO_PAULO_TZ

    now = datetime(2026, 8, 10, 12, 0, tzinfo=SAO_PAULO_TZ)
    assert compute_next_update_interval(None, now=now) == DEFAULT_UPDATE_INTERVAL


def test_compute_next_update_interval_falls_back_when_target_in_past():
    from datetime import date, datetime
    from custom_components.enel_sp.api import compute_next_update_interval
    from custom_components.enel_sp.const import DEFAULT_UPDATE_INTERVAL, SAO_PAULO_TZ

    # next_reading_date + 1 dia já ficou no passado (dado desatualizado).
    now = datetime(2026, 9, 20, 12, 0, tzinfo=SAO_PAULO_TZ)
    assert compute_next_update_interval(date(2026, 9, 10), now=now) == DEFAULT_UPDATE_INTERVAL
