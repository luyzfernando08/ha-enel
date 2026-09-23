"""Login SAML e descoberta da conta no portal da Enel São Paulo.

Reproduz o fluxo de login do navegador em https://www.enel.com.br/pt-saopaulo/login.html:

1-3. GET do ponto de entrada do SAML SSO, POST das credenciais e POST do
   ``SAMLResponse`` resultante na Assertion Consumer Service URL do site —
   essas três etapas são desafiadas por um WAF (Imperva/Incapsula) que exige
   execução de JavaScript/fingerprinting de navegador, então são delegadas
   ao add-on ``enel_sp_auth`` (Playwright), em ``addon_client.py``. O
   resultado são os cookies de sessão já aplicados na sessão ``aiohttp``
   desta classe.
4. Chamada ao servlet ``currentuser`` para obter o ``access_token`` da conta
   (um JWT de curta duração) e os identificadores SAP IS-U
   (ANLAGE/VERTRAG/VKONT/PARTNER) necessários para consultar as APIs de
   negócio hospedadas no Mulesoft — só usa os cookies já obtidos, sem
   precisar passar pelo WAF de novo.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import aiohttp

from .const import CURRENTUSER_URL, WWW_ORIGIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Quantos caracteres de uma resposta de erro HTML/JSON inesperada logar em
# nível DEBUG quando uma etapa falha, para ajudar no diagnóstico sem lotar o log.
_DEBUG_SNIPPET_LEN = 1500


def _host_of(url: str) -> str:
    return urlsplit(url).hostname or ""


class EnelSPError(Exception):
    """Erro base do cliente da Enel SP."""


class EnelSPAuthError(EnelSPError):
    """Levantado quando o login falha (credenciais erradas ou página de login inesperada)."""


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


class EnelSPAuthMixin:
    """Login SAML e descoberta de conta/UCs — mix-in usado por ``EnelSPClient``.

    Dono do estado de sessão (``_sid``/``_jwt``/``_enel_id``/``_raw_current_user``)
    que as chamadas de API de negócio (``api.py``) leem depois de logado.
    """

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        # O header SID usado nas APIs de negócio não é emitido pelo servidor:
        # o app web oficial o gera no cliente com crypto.randomUUID() e o
        # reaproveita durante a sessão, então fazemos o mesmo.
        self._sid = str(uuid.uuid4())
        self._jwt: str | None = None
        self._enel_id: str | None = None
        self._raw_current_user: dict[str, Any] = {}

    async def async_login(self, hass: "HomeAssistant") -> dict[str, Any]:
        """Executa o login completo e busca o perfil da conta.

        Retorna o payload bruto ``currentUser`` (unidades consumidoras, bandeira
        tarifária, ...).
        """
        # Import local pra evitar import circular (addon_client.py importa
        # EnelSPAuthError/EnelSPError deste módulo).
        from .addon_client import async_login_via_addon

        _LOGGER.debug("Starting login (sid=%s)", self._sid)
        await async_login_via_addon(hass, self._session, self._username, self._password)
        current_user = await self._async_fetch_current_user()
        _LOGGER.debug(
            "Login finished: %d installation(s) found", len(current_user.get("ET_INST", []))
        )
        return current_user

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
