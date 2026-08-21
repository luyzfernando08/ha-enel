"""Entidade base compartilhada entre as plataformas (sensor, binary_sensor, ...).

Cada plataforma faz o mix-in do seu próprio tipo de entidade do HA por cima
dessa base (ex.: ``class EnelSPEntity(EnelSPBaseEntity, SensorEntity)``); ela
só cuida do que é comum a todas: unique_id, DeviceInfo e o formato do entity_id.
"""
from __future__ import annotations

from homeassistant.const import CONF_USERNAME
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .api import EnelSPData, Installation
from .const import DOMAIN


class EnelSPBaseEntity(CoordinatorEntity):
    """Entidade base vinculada a uma unidade consumidora da Enel SP."""

    _attr_has_entity_name = True
    # Sobrescrito por plataforma (ex.: "binary_sensor") no mix-in concreto.
    _entity_domain = "sensor"

    def __init__(self, coordinator, description: EntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        installation: Installation = coordinator.data.installation
        login = coordinator.entry.data[CONF_USERNAME]
        self._attr_unique_id = f"{installation.anlage}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, installation.anlage)},
            name=login,
            manufacturer="Enel",
            model="Unidade consumidora",
            configuration_url="https://www.enel.com.br/pt-saopaulo/login.html",
        )
        # entity_id no formato <domínio>.enel_sp_<login antes do @>_<chave>,
        # em vez do padrão (nome do dispositivo + nome traduzido). Isso só
        # vale pra criação da entidade: uma vez registrada, o HA respeita
        # renomeações feitas pelo usuário e não sobrescreve depois.
        login_local_part = login.split("@", 1)[0]
        self.entity_id = (
            f"{self._entity_domain}.enel_sp_{slugify(login_local_part)}_{description.key}"
        )

    @property
    def _data(self) -> EnelSPData:
        return self.coordinator.data
