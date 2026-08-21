"""Cache local dos pontos horários do medidor inteligente.

A API do medidor (``smartmetergetconsumptionchartdata``) só devolve uma janela
móvel dos últimos 7 dias a cada chamada — não existe forma de pedir dias mais
antigos depois que eles saem dessa janela. Sem acumular esses pontos entre
atualizações, a série de estatísticas do mês corrente encolheria a cada nova
chamada (em vez de só crescer), porque dias já vistos, mas fora da janela
atual, deixariam de entrar na soma cumulativa.

Este módulo funde os pontos recém-buscados num cache persistido por UC (via
``homeassistant.helpers.storage.Store``), descartando pontos de meses
anteriores ao corrente — esses já são cobertos pelo histórico mensal fechado
(``portalhistoryinfo``), então não precisam ficar acumulados aqui.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION = 1
_SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


def _store_for(hass: HomeAssistant, anlage: str) -> Store:
    return Store(hass, _STORAGE_VERSION, f"{DOMAIN}_smartmeter_hours_{anlage}")


async def async_merge_hourly_points(
    hass: HomeAssistant, anlage: str, new_points: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Funde ``new_points`` (janela de 7 dias recém-buscada) no cache
    persistido dessa UC e devolve a série acumulada do mês corrente."""
    store = _store_for(hass, anlage)
    cached: dict[str, dict[str, Any]] = await store.async_load() or {}

    for item in new_points:
        date_str, time_str = item.get("Date"), item.get("Time")
        if not date_str or not time_str:
            continue
        key = f"{date_str}{time_str}_{item.get('Register', '')}"
        cached[key] = item

    current_month = datetime.now(_SAO_PAULO_TZ).strftime("%Y%m")
    cached = {
        key: item
        for key, item in cached.items()
        if str(item.get("Date", "")).startswith(current_month)
    }

    await store.async_save(cached)
    return list(cached.values())
