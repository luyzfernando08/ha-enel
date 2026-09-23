"""DataUpdateCoordinator da integração Enel São Paulo."""
from __future__ import annotations

import logging
from datetime import datetime

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .addon_client import EnelSPAddonError, EnelSPAddonUnavailableError, EnelSPWafBlockedError
from .api import (
    EnelSPApiError,
    EnelSPAuthError,
    EnelSPClient,
    EnelSPData,
    Installation,
    compute_next_update_interval,
    most_recent_bill,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .pdf import async_save_bill_pdf

_LOGGER = logging.getLogger(__name__)

_WAF_ISSUE_ID = "waf_blocked_{entry_id}"
_ADDON_ISSUE_ID = "addon_unavailable_{entry_id}"


def issue_ids_for_entry(entry_id: str) -> tuple[str, str]:
    """Ids das duas issues que este coordinator pode criar, pra reaproveitar
    tanto na limpeza automática quanto no unload da config entry."""
    return _WAF_ISSUE_ID.format(entry_id=entry_id), _ADDON_ISSUE_ID.format(entry_id=entry_id)


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
        # Horário da última atualização bem-sucedida, manual (botão) ou
        # automática (ciclo periódico) — as duas passam por aqui igual.
        self.last_updated: datetime | None = None
        # Horário estimado do próximo ciclo automático (last_updated +
        # update_interval, ambos recalculados a cada atualização bem-sucedida
        # — ver compute_next_update_interval). Não reflete um refresh manual
        # feito pelo botão entre um ciclo e outro.
        self.next_update: datetime | None = None

    async def _async_update_data(self) -> EnelSPData:
        # O site emite tokens de curta duração (~4h) e não expõe um endpoint
        # de refresh, então cada atualização simplesmente repete o login inteiro
        # (via add-on Playwright, que é quem passa pelo desafio do WAF).
        waf_issue_id, addon_issue_id = issue_ids_for_entry(self.entry.entry_id)
        try:
            await self.client.async_login(self.hass)
            # Chegou até aqui: o WAF/add-on estão funcionando de novo — limpa
            # qualquer aviso pendente de uma tentativa anterior malsucedida.
            ir.async_delete_issue(self.hass, DOMAIN, waf_issue_id)
            ir.async_delete_issue(self.hass, DOMAIN, addon_issue_id)

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
            self.last_updated = dt_util.utcnow()
            self.update_interval = compute_next_update_interval(data.next_reading_date)
            self.next_update = self.last_updated + self.update_interval
            _LOGGER.debug(
                "Next update scheduled in %s (next_reading_date=%s)",
                self.update_interval, data.next_reading_date,
            )
            return data
        except EnelSPWafBlockedError as err:
            # Bloqueio de WAF não é um problema de credencial — pedir a
            # mesma senha de novo (via ConfigEntryAuthFailed/reauth) não
            # resolveria nada. O coordinator continua tentando nos próximos
            # ciclos; só avisamos o usuário de forma persistente.
            ir.async_create_issue(
                self.hass, DOMAIN, waf_issue_id,
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="waf_blocked",
            )
            raise UpdateFailed(str(err)) from err
        except EnelSPAddonUnavailableError as err:
            ir.async_create_issue(
                self.hass, DOMAIN, addon_issue_id,
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="addon_unavailable",
            )
            raise UpdateFailed(str(err)) from err
        except EnelSPAddonError as err:
            # Erro genérico do add-on (não é bloqueio de WAF nem add-on
            # ausente) — sem issue dedicada, só tenta de novo no próximo ciclo.
            raise UpdateFailed(str(err)) from err
        except EnelSPAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except EnelSPApiError as err:
            raise UpdateFailed(str(err)) from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with Enel SP: {err}") from err

    async def _async_save_bill_pdf(
        self, installation: Installation, bills: list[dict]
    ) -> str | None:
        # Melhor-esforço: um PDF que falhou não deve derrubar a atualização inteira.
        bill = most_recent_bill(bills)
        if not bill or not bill.get("BELNR"):
            return None
        try:
            pdf_bytes = await self.client.async_get_bill_pdf(installation, bill)
            return await async_save_bill_pdf(self.hass, installation.anlage, pdf_bytes)
        except (EnelSPApiError, aiohttp.ClientError, OSError) as err:
            _LOGGER.warning("Could not save bill PDF: %s", err)
            return None
