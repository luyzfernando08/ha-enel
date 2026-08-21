"""A integração Enel São Paulo."""
from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .api import EnelSPClient
from .const import CONF_INSTALLATION, DEFAULT_HEADERS
from .coordinator import EnelSPCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

EnelSPConfigEntry = ConfigEntry[EnelSPCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EnelSPConfigEntry) -> bool:
    """Configura a Enel São Paulo a partir de uma config entry."""
    # Uma sessão privada mantém os cookies de login dessa conta isolados de
    # qualquer outra conta Enel SP configurada na mesma instância do Home Assistant.
    session = aiohttp.ClientSession(headers=DEFAULT_HEADERS)
    client = EnelSPClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    coordinator = EnelSPCoordinator(hass, entry, client, entry.data[CONF_INSTALLATION])

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await session.close()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(session.close)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnelSPConfigEntry) -> bool:
    """Descarrega uma config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
