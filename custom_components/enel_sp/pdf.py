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

from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import PDF_SUBDIR


def _pdf_path(hass: HomeAssistant, anlage: str) -> Path:
    # Um arquivo por unidade consumidora (não por fatura): o link fica
    # estável e cada atualização apenas substitui o conteúdo pelo mais recente.
    return Path(hass.config.path("www", PDF_SUBDIR, f"{anlage}.pdf"))


def _write_pdf(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def async_save_bill_pdf(hass: HomeAssistant, anlage: str, content: bytes) -> str:
    """Salva o PDF no disco e devolve o caminho relativo pra acessá-lo.

    Sem host: o link é relativo (``/local/...``), resolvido pelo navegador em
    cima da mesma origem de onde o HA está sendo acessado no momento — assim
    funciona tanto local quanto por qualquer URL externa/proxy configurado,
    sem depender de ``get_url()`` acertar qual delas usar.
    """
    path = _pdf_path(hass, anlage)
    await hass.async_add_executor_job(_write_pdf, path, content)

    return f"/local/{PDF_SUBDIR}/{anlage}.pdf"
