"""Plataforma de image da integração Enel São Paulo."""
from __future__ import annotations

import io

import qrcode
from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EnelSPConfigEntry
from .entity import EnelSPBaseEntity

PIX_QRCODE_DESCRIPTION = ImageEntityDescription(
    key="qrcode_pix",
    translation_key="qrcode_pix",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnelSPConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cria as entidades de imagem da Enel São Paulo a partir de uma config entry."""
    coordinator = entry.runtime_data
    async_add_entities([EnelSPPixQrCodeImage(coordinator, PIX_QRCODE_DESCRIPTION, hass)])


class EnelSPEntity(EnelSPBaseEntity, ImageEntity):
    """Image da Enel SP (mix-in de EnelSPBaseEntity + ImageEntity).

    ``ImageEntity.__init__`` precisa do ``hass`` logo na criação (monta o
    cliente HTTP interno), por isso não dá pra depender só do encadeamento
    de ``super().__init__`` de ``EnelSPBaseEntity`` (que só conhece
    ``coordinator``/``description``) — os dois construtores são chamados à
    parte, cada um cuidando dos seus próprios atributos.
    """

    _entity_domain = "image"
    _attr_content_type = "image/png"

    def __init__(
        self, coordinator, description: ImageEntityDescription, hass: HomeAssistant
    ) -> None:
        EnelSPBaseEntity.__init__(self, coordinator, description)
        ImageEntity.__init__(self, hass)


class EnelSPPixQrCodeImage(EnelSPEntity):
    """QR Code do Pix "copia e cola" da fatura em aberto.

    Mesma fonte e mesmo critério do sensor "Código Pix" (``ET_CONTAS[]`` da
    fatura pendente mais recente → campo ``QRCODE``): só existe imagem
    enquanto houver conta em aberto, senão a entidade fica indisponível.
    """

    def __init__(
        self, coordinator, description: ImageEntityDescription, hass: HomeAssistant
    ) -> None:
        super().__init__(coordinator, description, hass)
        # A primeira atualização do coordinator já rodou antes das
        # plataformas serem montadas (ver EnelSPConfigEntry.async_setup_entry),
        # então já há um código Pix (ou não) disponível aqui na criação — sem
        # isso, `image_last_updated` ficaria None (e o estado, "unknown") até
        # a próxima atualização do coordinator, em até 24h.
        self._last_pix_code = self._pix_code
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def _pix_code(self) -> str | None:
        bill = self._data.next_due_bill
        return (bill.get("QRCODE") or None) if bill else None

    @property
    def available(self) -> bool:
        return super().available and self._pix_code is not None

    def image(self) -> bytes | None:
        code = self._pix_code
        if not code:
            return None
        buffer = io.BytesIO()
        qrcode.make(code).save(buffer, format="PNG")
        return buffer.getvalue()

    @callback
    def _handle_coordinator_update(self) -> None:
        code = self._pix_code
        if code != self._last_pix_code:
            self._last_pix_code = code
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()
