"""Constantes da integração Enel São Paulo."""
from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "enel_sp"

# As UCs da Enel SP ficam todas na região de São Paulo, então usamos esse
# fuso fixo pra interpretar datas/horas da própria Enel (leitura do medidor,
# agendamento) — não o fuso do servidor onde o Home Assistant roda.
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")

# SP_ENTITY_ID, a URL do samlsso e a Assertion Consumer Service URL não são
# mais usadas aqui: essas etapas do login (desafiadas pelo WAF) foram
# movidas para o add-on `enel_sp_auth` (ver addon_client.py), que mantém sua
# própria cópia dessas constantes em `enel_sp_auth/rootfs/app/`.
CURRENTUSER_URL = "https://www.enel.com.br/bin/enel-br/pt-saopaulo/currentuser"

PORTALWEB_BASE = "https://exp-portalweb-pro.de-c1.eu1.cloudhub.io/api"
PORTALSP_BASE = "https://exp-portalsp-pro.de-c1.eu1.cloudhub.io/api"

ANALISE_CONSUMO_URL = f"{PORTALWEB_BASE}/getAnaliseConsumo"
PORTALINFO_URL = f"{PORTALSP_BASE}/portalinfo"
# Ao contrário de portalinfo, devolve o campo QRCODE (Pix "copia e cola") em
# ET_CONTAS. Mesmo formato de item que portalinfo, por isso substitui o
# portalinfo como fonte das faturas.
GETCLIENTBILLS_URL = f"{PORTALSP_BASE}/getClientBills"
PORTALHISTORYINFO_URL = f"{PORTALSP_BASE}/portalhistoryinfo"
# O caminho do endpoint está com erro de digitação ("billanalisys") no próprio
# servidor da Enel; mantido igual ao original.
BILLANALYSIS_URL = f"{PORTALSP_BASE}/billanalisys"
GENERATE_PDF_URL = f"{PORTALSP_BASE}/generatepdf"

# Subpasta de config/www/ onde o PDF da fatura é salvo (servida pelo HA em
# /local/<PDF_SUBDIR>/... sem exigir login).
PDF_SUBDIR = "enel_sp"

CANAL = "ZINT"
COD_SISTEMA = "WEB"

WWW_ORIGIN = "https://www.enel.com.br"

# O site fica atrás de um WAF (Imperva) que costuma desafiar requisições que
# não parecem vir de um navegador de verdade (headers padrão do aiohttp, sem
# User-Agent/Accept-*, entregam isso na hora), então toda requisição do
# cliente usa esses headers como base.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

CONF_INSTALLATION = "installation"

# Usado só como intervalo inicial (antes da primeira leitura) e como
# fallback quando `next_reading_date` não vier informado pela Enel — o
# intervalo de regime normal é recalculado dinamicamente a cada atualização
# (ver `api.compute_next_update_interval`), com base na próxima leitura do
# medidor informada pela própria Enel.
DEFAULT_UPDATE_INTERVAL = timedelta(hours=24)

ATTR_DUE_DATE = "vencimento"
ATTR_BARCODE = "codigo_barras"
ATTR_STATUS = "situacao"
ATTR_REFERENCE_MONTH = "mes_referencia"
ATTR_HISTORY = "historico"

TARIFF_FLAG_ICONS = {
    "VERDE": "mdi:flag",
    "AMARELA": "mdi:flag",
    "VERMELHA1": "mdi:flag",
    "VERMELHA2": "mdi:flag",
}
