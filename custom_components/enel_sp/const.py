"""Constantes da integração Enel São Paulo."""
from datetime import timedelta

DOMAIN = "enel_sp"

SP_ENTITY_ID = "ENEL_SP_WEB_BRA"

SAMLSSO_URL = f"https://accounts.enel.com/samlsso?spEntityID={SP_ENTITY_ID}"
ACS_URL = "https://www.enel.com.br/pt-saopaulo/login.html"
CURRENTUSER_URL = "https://www.enel.com.br/bin/enel-br/pt-saopaulo/currentuser"

PORTALWEB_BASE = "https://exp-portalweb-pro.de-c1.eu1.cloudhub.io/api"
PORTALSP_BASE = "https://exp-portalsp-pro.de-c1.eu1.cloudhub.io/api"

ANALISE_CONSUMO_URL = f"{PORTALWEB_BASE}/getAnaliseConsumo"
PORTALINFO_URL = f"{PORTALSP_BASE}/portalinfo"
PORTALHISTORYINFO_URL = f"{PORTALSP_BASE}/portalhistoryinfo"
# O caminho do endpoint está com erro de digitação ("billanalisys") no próprio
# servidor da Enel; mantido igual ao original.
BILLANALYSIS_URL = f"{PORTALSP_BASE}/billanalisys"
SMARTMETER_CHART_URL = f"{PORTALWEB_BASE}/smartmetergetconsumptionchartdata"
GENERATE_PDF_URL = f"{PORTALSP_BASE}/generatepdf"

# Subpasta de config/www/ onde o PDF da fatura é salvo (servida pelo HA em
# /local/<PDF_SUBDIR>/... sem exigir login).
PDF_SUBDIR = "enel_sp"

# Registrador do medidor inteligente que representa energia ativa consumida
# (os outros três valores pedidos pelo app, "04"/"06"/"08", não trazem dados
# para uma UC residencial monofásica como as que testamos).
SMARTMETER_ACTIVE_ENERGY_REGISTER = "03"

CANAL = "ZINT"
COD_SISTEMA = "WEB"

WWW_ORIGIN = "https://www.enel.com.br"
ACCOUNTS_ORIGIN = "https://accounts.enel.com"

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

DEFAULT_UPDATE_INTERVAL = timedelta(hours=6)

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
