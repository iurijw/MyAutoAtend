"""Aplicação principal: agente do WhatsApp + painel web + servidor MCP.

  /webhook/whatsapp/receberMensagem -> pipeline do agente (Evolution → IA)
  /admin   -> painel de configuração
  /mcp     -> endpoint MCP (streamable-http) p/ clients externos (opcional)

Rodar:  uvicorn app.main:app --reload --port 8000

Solicitante (autorização): no pipeline interno, o remoteJid do webhook é
gravado direto no contextvar (whatsapp.py). Para clients MCP externos, vale
o esquema antigo: query `?solicitante=` ou header `X-Solicitante-Telefone`,
lidos pelo middleware abaixo. O modelo nunca decide quem é o solicitante.

OBS: o `remoteJid` do WhatsApp vem com sufixo, ex.:
  554599307290@s.whatsapp.net
O `@s.whatsapp.net` é descartado em `phone.normalizar()` (corta no "@") antes
de qualquer comparação.
"""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, evolution, sessao, tarefas
from .admin import router as admin_router
from .config import settings
from .tools import mcp
from .whatsapp import router as whatsapp_router

# App ASGI do MCP (transporte streamable-http)
mcp_app = mcp.streamable_http_app()


class SolicitanteMiddleware:
    """Middleware ASGI puro: extrai o telefone do solicitante e o injeta no
    contextvar. Puro (não BaseHTTPMiddleware) para o contextvar valer dentro
    do mesmo task que executa as tools."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        sol = None
        qs = scope.get("query_string", b"").decode()
        if qs:
            sol = parse_qs(qs).get("solicitante", [None])[0]
        if not sol:
            headers = dict(scope.get("headers") or [])
            sol = headers.get(b"x-solicitante-telefone", b"").decode() or None

        token = auth.solicitante_ctx.set(sol)
        try:
            await self.app(scope, receive, send)
        finally:
            auth.solicitante_ctx.reset(token)


# O session manager do MCP precisa ser iniciado junto com o app principal.
# No startup, uma task garante a instância Evolution + webhook apontando p/ cá
# (substitui a parte Evolution do antigo init-n8n.sh).
@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap = asyncio.create_task(
        evolution.garantir_instancia(
            f"{settings.webhook_url}?token={settings.webhook_token}"
        )
    )
    worker = asyncio.create_task(tarefas.worker())  # ações proativas do bot
    async with mcp.session_manager.run():
        yield
    worker.cancel()
    bootstrap.cancel()


app = FastAPI(title="Gerenciador de Agendamentos", lifespan=lifespan)

app.include_router(admin_router)
app.include_router(whatsapp_router)
app.mount("/mcp", SolicitanteMiddleware(mcp_app))
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.exception_handler(sessao.SessaoInvalida)
async def sessao_invalida(request: Request, _exc: sessao.SessaoInvalida):
    """Sem sessão válida. Navegação do browser vai para a tela de login (com o
    destino guardado em ?next=); chamada de JS recebe 401 + marcador, e o
    js/sessao.js manda a página para o login."""
    if "text/html" in request.headers.get("accept", ""):
        alvo = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(alvo, safe='')}", status_code=303)
    return JSONResponse(
        {"detail": "Sua sessão expirou. Entre de novo."},
        status_code=401,
        headers={"X-Sessao": "expirada"},
    )


@app.get("/")
def home():
    return RedirectResponse("/admin")


@app.get("/health")
def health():
    return {"status": "ok"}
