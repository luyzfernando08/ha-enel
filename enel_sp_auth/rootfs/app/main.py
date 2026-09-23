"""API HTTP do add-on enel_sp_auth.

Só dois endpoints, sem UI própria — o usuário nunca interage diretamente com
este add-on; quem fala com ele é a integração ``enel_sp`` (ver
``custom_components/enel_sp/addon_client.py`` no repositório principal),
depois que o usuário já preencheu usuário/senha no formulário normal de
configuração da integração dentro do Home Assistant.

``POST /api/login`` exige o header ``X-API-Key``, comparado contra a chave
persistida em ``/share/enel_sp/api_key`` (gerada sozinha na primeira
execução — ver ``api_key.py``).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from api_key import ensure_api_key
from login_flow import async_login

logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)

app = FastAPI(title="Enel SP Auth")

# Serializa chamadas de login: cada uma abre um Chromium novo, e rodar
# várias ao mesmo tempo (múltiplas contas Enel configuradas, atualizando
# perto da mesma hora) pesa demais em hardware limitado (ex.: Raspberry Pi).
_login_lock = asyncio.Lock()


class LoginRequest(BaseModel):
    username: str
    password: str


@app.on_event("startup")
async def _on_startup() -> None:
    # Gera a API key já na subida do add-on, não só na primeira chamada,
    # pra ficar disponível em /share assim que o add-on iniciar.
    ensure_api_key()


def _check_api_key(x_api_key: str | None) -> None:
    if not x_api_key or x_api_key != ensure_api_key():
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/login")
async def login(payload: LoginRequest, x_api_key: str | None = Header(default=None)) -> dict:
    _check_api_key(x_api_key)

    # Id curto só pra correlacionar as linhas de log de uma mesma tentativa
    # (login_flow.py prefixa cada etapa com ele) — nunca inclui usuário/senha.
    request_id = uuid.uuid4().hex[:8]
    _LOGGER.info("[%s] POST /api/login recebido", request_id)

    if _login_lock.locked():
        _LOGGER.info(
            "[%s] Outro login em andamento, aguardando a vez (fila serializada)", request_id
        )

    async with _login_lock:
        result = await async_login(payload.username, payload.password, request_id)

    if result.success:
        _LOGGER.info("[%s] Respondendo 200 (status=success)", request_id)
        return {"status": "success", "cookies": result.cookies}

    _LOGGER.warning(
        "[%s] Respondendo 200 (status=error): error_type=%s message=%s",
        request_id, result.error_type, result.message,
    )
    return {"status": "error", "error_type": result.error_type, "message": result.message}
