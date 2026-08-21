"""Importa o consumo da Enel SP como Long-Term Statistics do Home Assistant.

Não cria nenhum sensor: só alimenta o Painel de Energia com uma série de
consumo (kWh) que combina o histórico mensal já fechado com os dados horários
reais do medidor inteligente (quando a UC tiver um).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant

from .api import Installation, build_consumption_statistics
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def statistic_id_for(anlage: str) -> str:
    """Id da série de estatísticas dessa unidade consumidora."""
    return f"{DOMAIN}:consumption_{anlage}"


def async_import_consumption_statistics(
    hass: HomeAssistant,
    installation: Installation,
    monthly_history: list[dict[str, Any]],
    hourly_data: list[dict[str, Any]],
) -> None:
    """Recalcula e reenvia a série de consumo inteira dessa UC.

    Chamar isso de novo a cada atualização é seguro: a série é sempre
    recomputada do zero a partir da mesma fonte de dados, e o import de
    estatísticas externas do HA faz upsert por timestamp.
    """
    points = build_consumption_statistics(monthly_history, hourly_data)
    if not points:
        return

    name = installation.nickname or installation.address or installation.anlage
    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=f"Enel SP consumo ({name})",
        source=DOMAIN,
        statistic_id=statistic_id_for(installation.anlage),
        unit_of_measurement="kWh",
    )
    statistics = [
        StatisticData(start=p["start"], sum=p["sum"], state=p["state"]) for p in points
    ]

    async_add_external_statistics(hass, metadata, statistics)
    _LOGGER.debug(
        "Imported %d consumption statistics points for %s (ends at %.2f kWh)",
        len(statistics), metadata["statistic_id"], statistics[-1]["sum"],
    )
