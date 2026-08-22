"""Login SAML e descoberta da conta no portal da Enel São Paulo.

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
"""
from __future__ import annotations

import html
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import ACCOUNTS_ORIGIN, ACS_URL, CURRENTUSER_URL, SAMLSSO_URL, WWW_ORIGIN

_LOGGER = logging.getLogger(__name__)

# Quantos caracteres de uma resposta de erro HTML/JSON inesperada logar em
# nível DEBUG quando uma etapa falha, para ajudar no diagnóstico sem lotar o log.
_DEBUG_SNIPPET_LEN = 1500


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
