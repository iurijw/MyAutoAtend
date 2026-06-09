"""Aplicação principal: servidor MCP + painel web no mesmo processo.

  /mcp     -> endpoint MCP (streamable-http) consumido pelo n8n
  /admin   -> painel de configuração

Rodar:  uvicorn app.main:app --reload --port 8000

O n8n identifica o solicitante na URL do endpoint MCP:
  http://mcp_agendamentos:8000/mcp/?solicitante=<remoteJid>
ou no header `X-Solicitante-Telefone`. O middleware abaixo lê esse valor e o
grava no contextvar usado pela camada de autorização — o modelo nunca decide
quem é o solicitante.

OBS: o `remoteJid` do WhatsApp vem com sufixo, ex.:
  ?solicitante=554599307290@s.whatsapp.net
O `@s.whatsapp.net` é descartado em `phone.normalizar()` (corta no "@") antes
de qualquer comparação.
"""

from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from . import auth
from .admin import router as admin_router
from .tools import mcp

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
@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Gerenciador de Agendamentos", lifespan=lifespan)

app.include_router(admin_router)
app.mount("/mcp", SolicitanteMiddleware(mcp_app))


@app.get("/")
def home():
    return RedirectResponse("/admin")


@app.get("/health")
def health():
    return {"status": "ok"}
