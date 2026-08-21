"""Plataforma de binary_sensor da integração Enel São Paulo."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnelSPConfigEntry
from .entity import EnelSPBaseEntity

SUPPLY_SUSPENDED_DESCRIPTION = BinarySensorEntityDescription(
    key="fornecimento_suspenso",
    translation_key="fornecimento_suspenso",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnelSPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cria os binary_sensors da Enel São Paulo a partir de uma config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [EnelSPSupplySuspendedBinarySensor(coordinator, SUPPLY_SUSPENDED_DESCRIPTION)]
    )


class EnelSPEntity(EnelSPBaseEntity, BinarySensorEntity):
    """Binary sensor da Enel SP (mix-in de EnelSPBaseEntity + BinarySensorEntity)."""

    _entity_domain = "binary_sensor"


class EnelSPSupplySuspendedBinarySensor(EnelSPEntity):
    """Se o fornecimento de energia da UC está suspenso (corte por falta de pagamento).

    ``device_class="problem"``: ligado (``on``) significa que há um problema
    (fornecimento suspenso); desligado significa que está tudo normal.
    """

    @property
    def is_on(self) -> bool:
        return self._data.supply_suspended

    @property
    def extra_state_attributes(self) -> dict:
        if not self._data.supply_suspended_message:
            return {}
        return {"mensagem": self._data.supply_suspended_message}
