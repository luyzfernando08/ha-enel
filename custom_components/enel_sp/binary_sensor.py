"""Plataforma de binary_sensor da integração Enel São Paulo."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnelSPConfigEntry
from .entity import EnelSPBaseEntity

# Sem device_class: "ligado" aqui significa "está tudo normal" (não é um
# indicador de problema), então os rótulos padrão de device_class="problem"
# ("OK"/"Problema") ficariam invertidos e confusos ao lado desse nome.
SUPPLY_NORMAL_DESCRIPTION = BinarySensorEntityDescription(
    key="fornecimento_normal",
    translation_key="fornecimento_normal",
    entity_category=EntityCategory.DIAGNOSTIC,
)

SMART_METER_DESCRIPTION = BinarySensorEntityDescription(
    key="medidor_inteligente",
    translation_key="medidor_inteligente",
    icon="mdi:meter-electric",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnelSPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cria os binary_sensors da Enel São Paulo a partir de uma config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            EnelSPSupplyNormalBinarySensor(coordinator, SUPPLY_NORMAL_DESCRIPTION),
            EnelSPSmartMeterBinarySensor(coordinator, SMART_METER_DESCRIPTION),
        ]
    )


class EnelSPEntity(EnelSPBaseEntity, BinarySensorEntity):
    """Binary sensor da Enel SP (mix-in de EnelSPBaseEntity + BinarySensorEntity)."""

    _entity_domain = "binary_sensor"


class EnelSPSupplyNormalBinarySensor(EnelSPEntity):
    """Se o fornecimento de energia da UC está normal (ligado) ou suspenso (corte
    por falta de pagamento).
    """

    @property
    def is_on(self) -> bool:
        return not self._data.supply_suspended

    @property
    def extra_state_attributes(self) -> dict:
        if not self._data.supply_suspended_message:
            return {}
        return {"mensagem": self._data.supply_suspended_message}


class EnelSPSmartMeterBinarySensor(EnelSPEntity):
    """Se a unidade consumidora tem medidor inteligente."""

    @property
    def is_on(self) -> bool:
        return self._data.installation.smart_meter

    @property
    def extra_state_attributes(self) -> dict:
        return {"numero_serie": self._data.installation.serial}
