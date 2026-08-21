"""DataUpdateCoordinator da integração Enel São Paulo."""
from __future__ import annotations

import logging

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EnelSPApiError,
    EnelSPAuthError,
    EnelSPClient,
    EnelSPData,
    Installation,
    most_recent_bill,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .pdf import async_save_bill_pdf
from .smartmeter_cache import async_merge_hourly_points
from .statistics import async_import_consumption_statistics

_LOGGER = logging.getLogger(__name__)


class EnelSPCoordinator(DataUpdateCoordinator[EnelSPData]):
    """Faz login e busca dados de fatura/consumo de uma única unidade consumidora."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: EnelSPClient,
        anlage: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.entry = entry
        self.client = client
        self.anlage = anlage

    async def _async_update_data(self) -> EnelSPData:
        # O site emite tokens de curta duração (~4h) e não expõe um endpoint
        # de refresh, então cada atualização simplesmente repete o login inteiro.
        try:
            await self.client.async_login()
            installations = self.client.get_installations()
            installation = next(
                (i for i in installations if i.anlage == self.anlage), None
            )
            if installation is None:
                raise UpdateFailed(
                    f"Installation {self.anlage} is no longer linked to this account"
                )
            data = await self.client.async_get_all_data(installation)
            data.bill_pdf_url = await self._async_save_bill_pdf(installation, data.bills)
            if installation.smart_meter:
                await self._async_import_statistics(installation, data.history)
            return data
        except EnelSPAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EnelSPApiError as err:
            raise UpdateFailed(str(err)) from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with Enel SP: {err}") from err

    async def _async_import_statistics(
        self, installation: Installation, monthly_history: list[dict]
    ) -> None:
        # Melhor-esforço: isso só alimenta o Painel de Energia, não é dado
        # essencial dos sensores, então uma falha aqui não deve derrubar a
        # atualização inteira (nem disparar reautenticação/retry).
        try:
            chart_data = await self.client.async_get_smart_meter_chart_data(installation)
            # A API só devolve os últimos 7 dias por chamada: funde no cache
            # local acumulado da UC em vez de usar só a janela desta chamada,
            # senão dias que saem da janela seriam perdidos a cada atualização.
            hourly_points = await async_merge_hourly_points(
                self.hass, installation.anlage, chart_data.get("T_GRAPHIC_HOUR", [])
            )
            async_import_consumption_statistics(
                self.hass,
                installation,
                monthly_history,
                hourly_points,
            )
        except (EnelSPApiError, aiohttp.ClientError) as err:
            _LOGGER.warning("Could not import smart meter statistics: %s", err)

    async def _async_save_bill_pdf(
        self, installation: Installation, bills: list[dict]
    ) -> str | None:
        # Melhor-esforço, mesma lógica da importação de estatísticas: um PDF
        # que falhou não deve derrubar os sensores.
        bill = most_recent_bill(bills)
        if not bill or not bill.get("BELNR"):
            return None
        try:
            pdf_bytes = await self.client.async_get_bill_pdf(installation, bill)
            return await async_save_bill_pdf(self.hass, installation.anlage, pdf_bytes)
        except (EnelSPApiError, aiohttp.ClientError, OSError) as err:
            _LOGGER.warning("Could not save bill PDF: %s", err)
            return None
