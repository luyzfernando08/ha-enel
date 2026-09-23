"""Geração e persistência da API key que protege POST /api/login.

Gerada sozinha na primeira execução e salva em /share/enel_sp/api_key —
volume compartilhado entre o add-on e o Core do Home Assistant sob HAOS/
Supervised, então a integração lê o mesmo arquivo sem nenhuma configuração
manual do usuário.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

_API_KEY_DIR = Path("/share/enel_sp")
_API_KEY_PATH = _API_KEY_DIR / "api_key"


def ensure_api_key() -> str:
    """Devolve a API key persistida, gerando uma nova (32 bytes, urlsafe) na
    primeira execução."""
    if _API_KEY_PATH.exists():
        return _API_KEY_PATH.read_text(encoding="utf-8").strip()

    _API_KEY_DIR.mkdir(parents=True, exist_ok=True)
    api_key = secrets.token_urlsafe(32)
    _API_KEY_PATH.write_text(api_key, encoding="utf-8")
    os.chmod(_API_KEY_PATH, 0o600)
    return api_key
