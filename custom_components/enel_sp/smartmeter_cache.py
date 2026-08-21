"""Cache local dos pontos horários do medidor inteligente.

A API do medidor (``smartmetergetconsumptionchartdata``) só devolve uma janela
móvel dos últimos 7 dias a cada chamada — não existe forma de pedir dias mais
antigos depois que eles saem dessa janela. Sem acumular esses pontos entre
atualizações, a série de estatísticas do ciclo em andamento encolheria a cada
nova chamada (em vez de só crescer), porque dias já vistos, mas fora da
janela atual, deixariam de entrar na soma cumulativa.

Este módulo funde os pontos recém-buscados num cache persistido por UC (via
``homeassistant.helpers.storage.Store``), descartando pontos já cobertos pelo
ciclo de leitura mais recente (``LastReading``) — esses já entram pelo total
mensal fechado, então mantê-los aqui também contaria o mesmo consumo duas
vezes.
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
    hass: HomeAssistant,
    anlage: str,
    new_points: list[dict[str, Any]],
    last_reading: str | None = None,
) -> list[dict[str, Any]]:
    """Funde ``new_points`` (janela de 7 dias recém-buscada) no cache
    persistido dessa UC e devolve a série acumulada do ciclo em andamento.

    ``last_reading`` é o ``LastReading`` (``YYYYMMDD``) da mesma resposta —
    a data em que o ciclo de leitura mais recente fechou de verdade. Pontos
    com ``Date <= last_reading`` são descartados do cache porque já entram
    pelo total mensal fechado correspondente (ver
    ``build_consumption_statistics``). Sem isso, podar pelo mês calendário
    (como uma versão anterior fazia) desalinha com o ciclo de leitura real do
    medidor — o dia do mês em que ele fecha varia por conta e não é um valor
    fixo, por isso usamos sempre esse campo em vez de assumir um dia — o que
    abria uma lacuna logo após a virada do mês (quando o ciclo não fecha no
    dia 1) e depois contava esses mesmos dias em dobro quando o ciclo
    finalmente fechava.
    """
    store = _store_for(hass, anlage)
    cached: dict[str, dict[str, Any]] = await store.async_load() or {}

    for item in new_points:
        date_str, time_str = item.get("Date"), item.get("Time")
        if not date_str or not time_str:
            continue
        key = f"{date_str}{time_str}_{item.get('Register', '')}"
        cached[key] = item

    if last_reading:
        cached = {
            key: item
            for key, item in cached.items()
            if str(item.get("Date", "")) > last_reading
        }
    else:
        # Sem LastReading, cai numa poda mais grosseira (mês calendário) só
        # pra não deixar o cache crescer sem limite — na prática sempre
        # devemos ter LastReading, já que ele vem da mesma resposta.
        current_month = datetime.now(_SAO_PAULO_TZ).strftime("%Y%m")
        cached = {
            key: item
            for key, item in cached.items()
            if str(item.get("Date", "")).startswith(current_month)
        }

    await store.async_save(cached)
    return list(cached.values())
