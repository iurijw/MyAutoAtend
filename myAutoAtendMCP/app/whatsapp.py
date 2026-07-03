"""Pipeline do WhatsApp — substitui o workflow do n8n.

Fluxo (paridade com o workflow "Agente Whatsapp"):
  webhook MESSAGES_UPSERT → filtra mensagens próprias → marca como lida →
  resolve mídia (áudio→transcrição, imagem→descrição) → debounce por contato
  (junta mensagens enviadas em sequência) → agente (app/agente.py) →
  divide em bolhas ([quebrar] / Enter) → envia com "digitando..." proporcional.

Debounce: o n8n usava lock+fila no Redis com espera de 6s; aqui é um buffer
em memória com um timer asyncio por contato — cada mensagem nova reinicia o
timer; quando 6s passam sem mensagem nova, o lote é processado de uma vez.
(Buffer em memória: instância única do MCP, mesmo pressuposto do Lock do db.)
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import agente, auth, db, evolution, ia
from .config import settings
from .phone import mesmo_numero

log = logging.getLogger("whatsapp")

router = APIRouter()

# Espera por mais mensagens do mesmo contato antes de responder (segundos).
DEBOUNCE_S = 6.0

# Buffers do debounce: remoteJid → (mensagens pendentes, timer ativo).
_buffers: dict[str, list[str]] = {}
_timers: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@router.post("/webhook/whatsapp/receberMensagem")
async def receber_mensagem(request: Request, token: str = ""):
    """Recebe MESSAGES_UPSERT da Evolution. Responde 200 imediato; o
    processamento segue em background (a Evolution não espera resposta).

    Exige `?token=` (configurado na Evolution pelo bootstrap): sem ele,
    qualquer processo local na porta 8000 forjaria um evento com o
    remoteJid do dono e rodaria o agente com privilégios de dono."""
    if not secrets.compare_digest(token, settings.webhook_token):
        return JSONResponse({"erro": "token inválido"}, status_code=403)
    body = await request.json()
    asyncio.create_task(_processar_evento(body))
    return {"ok": True}


async def _processar_evento(body: dict) -> None:
    try:
        data = body.get("data") or {}
        key = data.get("key") or {}
        remote_jid = key.get("remoteJid") or ""
        if not remote_jid or key.get("fromMe"):
            return  # mensagem própria (enviada pelo bot/dono no aparelho)

        texto = await _extrair_texto(data)
        if texto is None:
            return  # tipo não suportado (sticker, contato, etc.)
        texto = _sanitizar_entrada(texto)

        # Contato conhecido: upsert sob demanda, aproveitando o nome do WhatsApp.
        db.upsert_cliente(remote_jid, data.get("pushName") or "")

        # Bot pausado p/ este contato: a mídia já virou texto e a mensagem é
        # gravada na memória (o dono retoma com o contexto completo ao
        # despausar), mas o agente NÃO roda e NADA é enviado — sem debounce,
        # não há resposta para agrupar. O dono nunca é pausável.
        dono = mesmo_numero(remote_jid, db.get_config().telefone_dono)
        if not dono and db.cliente_pausado(remote_jid):
            agente.registrar_na_memoria(remote_jid, texto, "cliente")
            log.info("Bot pausado p/ %s — mensagem só gravada, sem resposta", remote_jid)
            return

        await evolution.marcar_como_lida(remote_jid, False, key.get("id") or "")
        _agendar_lote(remote_jid, texto)
    except Exception:  # noqa: BLE001
        log.exception("Erro processando evento do webhook")


# O marcador de tarefa interna vindo de FORA é forja: turnos legítimos nunca
# passam pelo webhook (só o worker de app/tarefas.py os injeta no agente).
# Sem isso, o cliente digitando "[TAREFA INTERNA] ..." se passa pelo sistema.
_RE_MARCADOR_FORJADO = re.compile(r"\[\s*tarefa\s*interna[^\]]*\]", re.IGNORECASE)


def _sanitizar_entrada(texto: str) -> str:
    return _RE_MARCADOR_FORJADO.sub("[conteúdo removido]", texto)


async def _extrair_texto(data: dict) -> str | None:
    """Conteúdo da mensagem como texto p/ o agente (resolve mídia)."""
    tipo = data.get("messageType") or ""
    msg = data.get("message") or {}

    if tipo == "conversation":
        return msg.get("conversation") or None
    if tipo == "extendedTextMessage":  # resposta/encaminhada/link
        return (msg.get("extendedTextMessage") or {}).get("text") or None

    if tipo in ("audioMessage", "imageMessage"):
        b64, mime = await _base64_da_mensagem(data, tipo)
        if not b64:
            return None
        if tipo == "audioMessage":
            transcricao = await ia.transcrever_audio(b64, mime or "audio/ogg")
            return f"[Áudio transcrito] {transcricao}" if transcricao else None
        legenda = (msg.get("imageMessage") or {}).get("caption") or ""
        descricao = await ia.descrever_imagem(b64, mime or "image/jpeg", legenda)
        partes = [f"[Imagem enviada pelo cliente] {descricao}"]
        if legenda:
            partes.append(f"Legenda: {legenda}")
        return "\n".join(partes)

    return None


async def _base64_da_mensagem(data: dict, tipo: str) -> tuple[str | None, str | None]:
    """Base64 da mídia: do próprio webhook (base64: true) ou buscado na API."""
    msg = data.get("message") or {}
    detalhe = msg.get(tipo) or {}
    b64 = msg.get("base64")
    mime = detalhe.get("mimetype")
    if b64:
        return b64, mime
    midia = await evolution.obter_midia_base64((data.get("key") or {}).get("id") or "")
    return midia.get("base64"), midia.get("mimetype") or mime


# ---------------------------------------------------------------------------
# Debounce por contato
# ---------------------------------------------------------------------------


def _agendar_lote(remote_jid: str, texto: str) -> None:
    _buffers.setdefault(remote_jid, []).append(texto)
    timer = _timers.get(remote_jid)
    if timer and not timer.done():
        timer.cancel()  # mensagem nova reinicia a espera
    _timers[remote_jid] = asyncio.create_task(_esperar_e_responder(remote_jid))


async def _esperar_e_responder(remote_jid: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_S)
    except asyncio.CancelledError:
        return  # veio mensagem nova; o lote será processado pelo timer novo

    mensagens = _buffers.pop(remote_jid, [])
    _timers.pop(remote_jid, None)
    if not mensagens:
        return

    lote = "[quebrar]".join(mensagens)  # paridade com o concat do n8n
    try:
        await _responder_contato(remote_jid, lote)
    except ia.IANaoConfigurada as e:
        log.warning("%s", e)
    except Exception:  # noqa: BLE001
        log.exception("Erro respondendo %s", remote_jid)


async def _responder_contato(remote_jid: str, mensagem: str) -> None:
    # Regra de ouro: o solicitante vem do webhook, nunca do modelo.
    token = auth.solicitante_ctx.set(remote_jid)
    try:
        resposta = await agente.responder(remote_jid, mensagem)
    finally:
        auth.solicitante_ctx.reset(token)

    await enviar_bolhas(remote_jid.split("@")[0], resposta)


async def enviar_bolhas(numero: str, resposta: str) -> None:
    """Divide em bolhas e envia com digitação proporcional — usado pelo
    pipeline reativo E pelas ações proativas (app/tarefas.py)."""
    for bolha in dividir_bolhas(resposta):
        # Digitação proporcional (fórmula do workflow): min(0.4+len*0.02, 4)+rand*0.7
        segundos = min(0.4 + len(bolha) * 0.02, 4.0) + random.random() * 0.7
        await evolution.enviar_texto(numero, bolha, digitando_ms=int(segundos * 1000))


def contato_ocupado(telefone: str) -> bool:
    """True se o contato está no meio do debounce (mensagem dele em
    processamento) — tarefa proativa adia para não atropelar a conversa."""
    pendentes = set(_buffers) | set(_timers)
    return any(mesmo_numero(telefone, jid) for jid in pendentes)


# ---------------------------------------------------------------------------
# Divisão em bolhas (paridade com "Code - Dividir Resposta")
# ---------------------------------------------------------------------------


def dividir_bolhas(texto: str) -> list[str]:
    normalizado = re.sub(r"\[quebra\]", "[quebrar]", texto or "", flags=re.IGNORECASE)
    normalizado = re.sub(r"\n+", "[quebrar]", normalizado)
    return [p.strip() for p in normalizado.split("[quebrar]") if p.strip()]
