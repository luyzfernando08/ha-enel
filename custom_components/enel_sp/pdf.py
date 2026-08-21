"""Salva a fatura em PDF em ``config/www/`` e monta a URL pra abrir no navegador.

O Home Assistant serve automaticamente qualquer arquivo colocado em
``config/www/`` no caminho ``/local/...`` — **sem exigir login**. É o mesmo
mecanismo que outras integrações usam pra expor fotos de câmera, por exemplo,
não é um bug daqui. Na prática isso quer dizer que qualquer um com acesso à
rede/porta do seu HA consegue abrir esse link e ver a fatura (nome, endereço,
valores, código de barras) sem entrar na sua conta do HA. Se sua instância
estiver exposta pra internet sem proteção adicional (proxy com autenticação,
VPN, etc.), vale ter isso em mente.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import PDF_SUBDIR

_LOGGER = logging.getLogger(__name__)


def _pdf_path(hass: HomeAssistant, anlage: str) -> Path:
    # Um arquivo por unidade consumidora (não por fatura): o link fica
    # estável e cada atualização apenas substitui o conteúdo pelo mais recente.
    return Path(hass.config.path("www", PDF_SUBDIR, f"{anlage}.pdf"))


def _write_pdf(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def async_save_bill_pdf(hass: HomeAssistant, anlage: str, content: bytes) -> str:
    """Salva o PDF no disco e devolve a URL local pra acessá-lo."""
    path = _pdf_path(hass, anlage)
    await hass.async_add_executor_job(_write_pdf, path, content)

    try:
        base_url = get_url(hass, prefer_external=False)
    except NoURLAvailableError:
        _LOGGER.debug("No HA base URL available yet; using a relative /local/ URL")
        base_url = ""

    return f"{base_url}/local/{PDF_SUBDIR}/{anlage}.pdf"
