"""Cliente do add-on ``enel_sp_auth`` (Playwright).

O portal da Enel fica atrás de um WAF (Imperva/Incapsula) que exige
execução de JavaScript/fingerprinting de navegador antes de aceitar o
login — algo que o ``aiohttp`` não faz. Esse módulo delega as etapas do
login desafiadas pelo WAF para um add-on separado do Home Assistant
Supervisor (``enel_sp_auth``, Playwright headless) e injeta os cookies de
sessão resultantes na sessão ``aiohttp`` da integração, que segue o fluxo
normalmente a partir daí.

O add-on só roda sob Home Assistant OS/Supervised — não há suporte a
Core/Container standalone.
"""
from __future__ import annotations

import http.cookies
import logging
import os
from typing import TYPE_CHECKING, Any

import aiohttp

from .auth import EnelSPAuthError, EnelSPError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Sufixo do slug do add-on (o Supervisor prefixa com o nome do repositório,
# ex.: "local_enel_sp_auth" ou "<hash>_enel_sp_auth").
_ADDON_SLUG_SUFFIX = "_enel_sp_auth"
_ADDON_PORT = 8978
_API_KEY_PATH = "/share/enel_sp/api_key"
_LOGIN_TIMEOUT = aiohttp.ClientTimeout(total=90)


class EnelSPAddonError(EnelSPError):
    """O add-on respondeu, mas o login não pôde ser concluído por um motivo
    que não é bloqueio de WAF nem credencial inválida (timeout interno,
    erro inesperado no navegador, etc.)."""


class EnelSPAddonUnavailableError(EnelSPAddonError):
    """Add-on não instalado, não iniciado, instância não é HAOS/Supervised,
    ou não foi possível falar com ele (Supervisor/rede)."""


class EnelSPWafBlockedError(EnelSPError):
    """O WAF da Enel bloqueou a tentativa de login. Não é um problema de
    usuário/senha — repetir com a mesma credencial pode voltar a funcionar
    sozinho num próximo ciclo."""


def cookies_to_simplecookie(playwright_cookies: list[dict[str, Any]]) -> http.cookies.SimpleCookie:
    """Converte cookies no formato devolvido pelo add-on (mesmo formato de
    ``browser_context.cookies()`` do Playwright: ``name``/``value``/``domain``/
    ``path``/``secure``/...) para um ``http.cookies.SimpleCookie``.

    Cada cookie vira um ``Morsel`` com ``domain``/``path`` explícitos, o que
    permite um único ``session.cookie_jar.update_cookies()`` aplicar
    corretamente cookies vindos de domínios diferentes (``accounts.enel.com``
    e ``www.enel.com.br``) na mesma chamada.
    """
    jar: http.cookies.SimpleCookie = http.cookies.SimpleCookie()
    for cookie in playwright_cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        jar[name] = value
        morsel = jar[name]
        if cookie.get("domain"):
            morsel["domain"] = cookie["domain"]
        if cookie.get("path"):
            morsel["path"] = cookie["path"]
        if cookie.get("secure"):
            morsel["secure"] = True
    return jar


async def _async_discover_addon_base_url(hass: "HomeAssistant") -> str:
    """Descobre a URL interna do add-on via API do Supervisor.

    Usa ``SUPERVISOR_TOKEN`` (variável de ambiente disponível ao Core só sob
    HAOS/Supervised) para listar os add-ons instalados e achar o nosso pelo
    slug, montando a URL a partir do hostname interno do container.
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise EnelSPAddonUnavailableError(
            "Esta integração precisa do add-on 'Enel SP Auth', disponível só "
            "em instâncias Home Assistant OS/Supervised."
        )

    session = async_get_clientsession(hass)
    try:
        async with session.get(
            "http://supervisor/addons",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            if resp.status != 200:
                raise EnelSPAddonUnavailableError(
                    f"Supervisor respondeu {resp.status} ao listar add-ons"
                )
            payload = await resp.json(content_type=None)
    except aiohttp.ClientError as err:
        raise EnelSPAddonUnavailableError(
            f"Não foi possível falar com o Supervisor: {err}"
        ) from err

    addons = payload.get("data", {}).get("addons", [])
    for addon in addons:
        slug = addon.get("slug", "")
        if slug.endswith(_ADDON_SLUG_SUFFIX) and addon.get("state") == "started":
            hostname = slug.replace("_", "-")
            return f"http://{hostname}:{_ADDON_PORT}"

    raise EnelSPAddonUnavailableError(
        "Add-on 'Enel SP Auth' não encontrado ou não está em execução. "
        "Instale-o e inicie-o pela loja de add-ons do Supervisor."
    )


async def _async_read_api_key(hass: "HomeAssistant") -> str:
    """Lê a API key que o add-on gera sozinho na primeira execução e
    compartilha com o Core via ``/share/enel_sp/api_key``."""

    def _read() -> str:
        try:
            with open(_API_KEY_PATH, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError as err:
            raise EnelSPAddonUnavailableError(
                "Add-on 'Enel SP Auth' ainda não gerou sua chave de API "
                f"({_API_KEY_PATH}); confirme que ele já rodou pelo menos "
                "uma vez."
            ) from err

    return await hass.async_add_executor_job(_read)


async def async_login_via_addon(
    hass: "HomeAssistant",
    session: aiohttp.ClientSession,
    username: str,
    password: str,
) -> None:
    """Faz login via add-on Playwright e injeta os cookies de sessão
    resultantes em ``session``.

    Não retorna nada: o efeito é a sessão ``aiohttp`` autenticada, pronta
    para as chamadas subsequentes (``currentuser``, APIs de negócio).
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    base_url = await _async_discover_addon_base_url(hass)
    api_key = await _async_read_api_key(hass)

    addon_session = async_get_clientsession(hass)
    try:
        async with addon_session.post(
            f"{base_url}/api/login",
            json={"username": username, "password": password},
            headers={"X-API-Key": api_key},
            timeout=_LOGIN_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise EnelSPAddonError(f"Add-on respondeu {resp.status} em /api/login")
            payload = await resp.json(content_type=None)
    except aiohttp.ClientError as err:
        raise EnelSPAddonUnavailableError(
            f"Não foi possível falar com o add-on: {err}"
        ) from err

    if payload.get("status") == "success":
        cookies = cookies_to_simplecookie(payload.get("cookies", []))
        session.cookie_jar.update_cookies(cookies)
        return

    error_type = payload.get("error_type")
    message = payload.get("message") or "Falha desconhecida no login via add-on"
    _LOGGER.debug("Add-on login failed: error_type=%s message=%s", error_type, message)
    if error_type == "invalid_credentials":
        raise EnelSPAuthError(message)
    if error_type == "waf_blocked":
        raise EnelSPWafBlockedError(message)
    raise EnelSPAddonError(message)
