"""Plataforma de button da integração Enel São Paulo."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnelSPConfigEntry
from .entity import EnelSPBaseEntity

REFRESH_DESCRIPTION = ButtonEntityDescription(
    key="atualizar_dados",
    translation_key="atualizar_dados",
    icon="mdi:refresh",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnelSPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cria os buttons da Enel São Paulo a partir de uma config entry."""
    coordinator = entry.runtime_data
    async_add_entities([EnelSPRefreshButton(coordinator, REFRESH_DESCRIPTION)])


class EnelSPEntity(EnelSPBaseEntity, ButtonEntity):
    """Button da Enel SP (mix-in de EnelSPBaseEntity + ButtonEntity)."""

    _entity_domain = "button"


class EnelSPRefreshButton(EnelSPEntity):
    """Força uma atualização imediata dos dados da UC.

    O estado nativo do botão (padrão do HA) já é o horário do último aperto
    manual. O atributo ``ultima_atualizacao`` complementa isso com o horário
    da última atualização que realmente terminou com sucesso, seja ela manual
    (por este botão) ou automática (do ciclo periódico do coordinator) — as
    duas chamam o mesmo `_async_update_data`.
    """

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.last_updated:
            return {}
        return {"ultima_atualizacao": self.coordinator.last_updated}
