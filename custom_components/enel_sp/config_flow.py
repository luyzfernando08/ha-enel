"""Config flow da integração Enel São Paulo."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.hassio import is_hassio

from .addon_client import EnelSPAddonUnavailableError, EnelSPWafBlockedError
from .api import EnelSPAuthError, EnelSPClient, EnelSPError, Installation
from .const import CONF_INSTALLATION, DEFAULT_HEADERS, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Placeholder pro texto de ajuda do step "user" (strings.json/translations) —
# hassfest não deixa URL/domínio literal na string traduzida.
_USER_STEP_DESCRIPTION_PLACEHOLDERS = {"enel_url": "www.enel.com.br"}

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _async_validate_login(
    hass: HomeAssistant, username: str, password: str
) -> list[Installation]:
    """Faz login (via add-on Playwright) e retorna as unidades consumidoras
    vinculadas à conta.

    Usa uma sessão aiohttp privada, então esse teste nunca compartilha cookies
    com a sessão de uma config entry em execução.
    """
    async with aiohttp.ClientSession(headers=DEFAULT_HEADERS) as session:
        client = EnelSPClient(session, username, password)
        await client.async_login(hass)
        return client.get_installations()


class EnelSPConfigFlow(ConfigFlow, domain=DOMAIN):
    """Conduz o config flow da Enel São Paulo."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._installations: list[Installation] = []
        self._reauth_entry = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # O add-on `enel_sp_auth` (Playwright), obrigatório para o login,
        # só existe sob Home Assistant OS/Supervised.
        if not is_hassio(self.hass):
            return self.async_abort(reason="not_hassio")

        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                installations = await _async_validate_login(self.hass, username, password)
            except EnelSPAuthError as err:
                _LOGGER.debug("Login validation failed: %s", err)
                errors["base"] = "invalid_auth"
            except EnelSPWafBlockedError as err:
                _LOGGER.debug("Login blocked by WAF: %s", err)
                errors["base"] = "waf_blocked"
            except EnelSPAddonUnavailableError as err:
                _LOGGER.debug("Add-on unavailable: %s", err)
                errors["base"] = "addon_unavailable"
            except (EnelSPError, aiohttp.ClientError) as err:
                _LOGGER.debug("Could not connect to Enel SP: %s", err)
                errors["base"] = "cannot_connect"
            else:
                if not installations:
                    errors["base"] = "no_installations"
                else:
                    self._username = username
                    self._password = password
                    self._installations = installations
                    if len(installations) == 1:
                        return await self._async_finish(installations[0])
                    return await self.async_step_installation()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders=_USER_STEP_DESCRIPTION_PLACEHOLDERS,
        )

    async def async_step_installation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            anlage = user_input[CONF_INSTALLATION]
            installation = next(
                i for i in self._installations if i.anlage == anlage
            )
            return await self._async_finish(installation)

        options = {
            i.anlage: (i.nickname or i.address or i.anlage) for i in self._installations
        }
        return self.async_show_form(
            step_id="installation",
            data_schema=vol.Schema(
                {vol.Required(CONF_INSTALLATION): vol.In(options)}
            ),
        )

    async def _async_finish(self, installation: Installation) -> ConfigFlowResult:
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_INSTALLATION: installation.anlage,
                },
            )

        await self.async_set_unique_id(installation.unique_id)
        self._abort_if_unique_id_configured()
        title = installation.nickname or installation.address or installation.anlage
        return self.async_create_entry(
            title=f"Enel SP - {title}",
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_INSTALLATION: installation.anlage,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                installations = await _async_validate_login(self.hass, username, password)
            except EnelSPAuthError as err:
                _LOGGER.debug("Login validation failed: %s", err)
                errors["base"] = "invalid_auth"
            except EnelSPWafBlockedError as err:
                _LOGGER.debug("Login blocked by WAF: %s", err)
                errors["base"] = "waf_blocked"
            except EnelSPAddonUnavailableError as err:
                _LOGGER.debug("Add-on unavailable: %s", err)
                errors["base"] = "addon_unavailable"
            except (EnelSPError, aiohttp.ClientError) as err:
                _LOGGER.debug("Could not connect to Enel SP: %s", err)
                errors["base"] = "cannot_connect"
            else:
                anlage = self._reauth_entry.data.get(CONF_INSTALLATION)
                installation = next(
                    (i for i in installations if i.anlage == anlage),
                    installations[0] if installations else None,
                )
                if installation is None:
                    errors["base"] = "no_installations"
                else:
                    self._username = username
                    self._password = password
                    self._installations = installations
                    return await self._async_finish(installation)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )
