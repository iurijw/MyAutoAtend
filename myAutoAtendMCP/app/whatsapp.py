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
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import agente, auth, db, evolution, ia, midia
from .agente import dividir_bolhas  # mora no agente: o painel lê pelas mesmas regras
from .config import settings
from .phone import mesmo_numero, normalizar

log = logging.getLogger("whatsapp")

router = APIRouter()

# Espera por mais mensagens do mesmo contato antes de responder (segundos).
# 12s e não 6: gente escreve em rajada ("oi" / "queria marcar" / "amanhã?") e
# com a janela curta o bot respondia duas vezes à mesma pergunta partida.
DEBOUNCE_S = 12.0

# Buffers do debounce: remoteJid → (mensagens pendentes, timer ativo).
_buffers: dict[str, list[str]] = {}
_timers: dict[str, asyncio.Task] = {}
# Um turno por contato de cada vez. O que chegar enquanto o agente responde
# fica no buffer e entra NO PRÓXIMO lote — sem isso, mensagem que chega durante
# a geração da resposta abre um turno paralelo e o cliente recebe resposta dobrada.
_locks: dict[str, asyncio.Lock] = {}
# Lote que já saiu do buffer e está com o agente. Só existe para o painel não
# ter um buraco: a mensagem do cliente só entra na memória quando o agente
# termina, e sem isto ela sumiria da tela entre o fim do debounce e a resposta.
_em_voo: dict[str, list[str]] = {}


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
        remote_jid = _jid_do_contato(key)
        if not remote_jid:
            return

        de_nos = bool(key.get("fromMe"))

        # Reação é enfeite de uma mensagem que já existe: nunca vira turno da
        # conversa e NUNCA aciona o agente (responder a um 👍 é ruído). Vai
        # para a mensagem original, que o painel desenha embaixo da bolha.
        reacao = midia.desembrulhar(data.get("message") or {}).get("reactionMessage")
        if isinstance(reacao, dict):
            _registrar_reacao(reacao, "bot" if de_nos else "cliente")
            return

        if de_nos:
            # Saiu do NOSSO número. Pode ser eco do que o próprio bot mandou
            # (já está na memória) ou o dono escrevendo pelo celular — este
            # segundo caso é conversa de verdade e precisa aparecer no painel.
            await _registrar_saida(remote_jid, data, key)
            return

        # `descritor` (e não `midia`) para não sombrear o módulo app/midia.py
        texto, descritor = await _conteudo_da_mensagem(data, de_nos=False)
        if texto is None:
            return  # tipo sem representação (enquete, protocolo, etc.)
        texto = _sanitizar_entrada(texto)

        # Contato conhecido: upsert sob demanda, aproveitando o nome do WhatsApp.
        db.upsert_cliente(remote_jid, data.get("pushName") or "")
        msg_id = key.get("id") or ""
        _guardar_midia(remote_jid, "cliente", texto, descritor, msg_id)
        db.registrar_mensagem(msg_id, remote_jid, "cliente", texto)

        # Bot pausado p/ este contato: a mídia já virou texto e a mensagem é
        # gravada na memória (o dono retoma com o contexto completo ao
        # despausar), mas o agente NÃO roda e NADA é enviado — sem debounce,
        # não há resposta para agrupar. O dono nunca é pausável.
        dono = mesmo_numero(remote_jid, db.get_config().telefone_dono)
        if not dono and db.cliente_pausado(remote_jid):
            agente.registrar_na_memoria(remote_jid, texto, "cliente")
            log.info("Bot pausado p/ %s — mensagem só gravada, sem resposta", remote_jid)
            return

        await evolution.marcar_como_lida(remote_jid, False, msg_id)
        _agendar_lote(remote_jid, texto)
    except Exception:  # noqa: BLE001
        log.exception("Erro processando evento do webhook")


# O marcador de tarefa interna vindo de FORA é forja: turnos legítimos nunca
# passam pelo webhook (só o worker de app/tarefas.py os injeta no agente).
# Sem isso, o cliente digitando "[TAREFA INTERNA] ..." se passa pelo sistema.
_RE_MARCADOR_FORJADO = re.compile(r"\[\s*tarefa\s*interna[^\]]*\]", re.IGNORECASE)


def _sanitizar_entrada(texto: str) -> str:
    return _RE_MARCADOR_FORJADO.sub("[conteúdo removido]", texto)


def _registrar_reacao(reacao: dict, de: str) -> None:
    """Prende a reação na mensagem reagida (`reactionMessage.key.id`).

    Emoji vazio = reação desfeita no aparelho. Alvo desconhecido (mensagem
    anterior ao registro de ids) só vira log: sem a bolha de origem não há
    onde pendurar o emoji.
    """
    alvo = ((reacao.get("key") or {}).get("id")) or ""
    emoji = (reacao.get("text") or "").strip()
    if not alvo:
        return
    if not db.marcar_reacao(alvo, emoji, de):
        log.info("Reação %r para mensagem desconhecida (%s)", emoji or "removida", alvo)


async def _ler_com_ia(coro, tarefa: str) -> str:
    """Roda uma leitura de mídia pela IA (transcrição/visão) e engole a falha.

    Sem provedor configurado — ou com a chave errada, ou fora do ar — a mídia
    tem que chegar ao painel do mesmo jeito: perder a mensagem inteira porque
    a visão não estava configurada é pior que mostrar "[Imagem enviada pelo
    cliente]" sem descrição (foi exatamente o que aconteceu em produção).
    """
    try:
        return (await coro) or ""
    except Exception as e:  # noqa: BLE001
        log.warning("Não deu para %s: %s", tarefa, e)
        return ""


def _jid_do_contato(key: dict) -> str:
    """remoteJid do contato. Endereço @lid (o formato novo do WhatsApp) não é
    telefone: o número real vem em `remoteJidAlt` — sem essa troca a memória
    ficaria indexada por um id que não casa com nenhum cliente."""
    jid = key.get("remoteJid") or ""
    if jid.endswith("@lid"):
        return key.get("remoteJidAlt") or key.get("participantAlt") or jid
    return jid


async def _conteudo_da_mensagem(data: dict, de_nos: bool) -> tuple[str | None, dict | None]:
    """Mensagem → (texto p/ a memória, descritor de mídia p/ o painel).

    O texto é o que o modelo lê; toda mídia vira um marcador em português
    ("[Figurinha]", "[Vídeo enviado pelo cliente] ..."). O descritor leva o
    base64 e os metadados do arquivo, que `_guardar_midia` grava em disco.

    `de_nos` = mensagem saiu do nosso número (dono escrevendo pelo celular):
    aí NÃO gastamos IA analisando a própria mídia — transcrever o áudio ou
    descrever a imagem que o dono acabou de mandar não ajuda ninguém, e o
    painel mostra o arquivo mesmo.
    """
    msg = midia.desembrulhar(data.get("message") or {})
    quem = "por você" if de_nos else "pelo cliente"

    if msg.get("conversation"):
        return msg["conversation"], None
    texto_estendido = (msg.get("extendedTextMessage") or {}).get("text")
    if texto_estendido:
        return texto_estendido, None

    achado = midia.tipo_de(msg)
    if achado:
        chave, detalhe = achado
        tipo, rotulo = midia.TIPOS[chave]
        legenda = (detalhe.get("caption") or "").strip()
        nome = (detalhe.get("fileName") or detalhe.get("title") or "").strip()
        b64, mime = await _base64_da_mensagem(data, chave, detalhe)
        descritor = {
            "tipo": tipo,
            "mime": mime or detalhe.get("mimetype") or "",
            "b64": b64,
            "nome": nome,
            "legenda": legenda,
        }

        if tipo == "audio":
            transcricao = ""
            if b64 and not de_nos:
                transcricao = await _ler_com_ia(
                    ia.transcrever_audio(b64, mime or "audio/ogg"), "transcrever o áudio"
                )
            if transcricao:
                return f"[Áudio transcrito] {transcricao}", descritor
            return (f"[Áudio enviado {quem}]", descritor)

        if tipo == "imagem":
            descricao = ""
            if b64 and not de_nos:
                descricao = await _ler_com_ia(
                    ia.descrever_imagem(b64, mime or "image/jpeg", legenda),
                    "descrever a imagem",
                )
            partes = [f"[Imagem enviada {quem}]{' ' + descricao if descricao else ''}"]
            if legenda:
                partes.append(f"Legenda: {legenda}")
            # cliente: quebra de linha não separa bolha, então o \n é seguro
            return "\n".join(partes), descritor

        if tipo == "figurinha":
            return "[Figurinha]", descritor

        if tipo == "documento":
            texto = f"[Documento {nome}]" if nome else "[Documento]"
            if legenda:
                texto += f" Legenda: {legenda}"
            return texto, descritor

        texto = f"[{rotulo} enviado {quem}]"  # vídeo
        if legenda:
            texto += f" Legenda: {legenda}"
        return texto, descritor

    local = msg.get("locationMessage") or msg.get("liveLocationMessage") or {}
    if local:
        nome = (local.get("name") or local.get("address") or "").strip()
        lat, lon = local.get("degreesLatitude"), local.get("degreesLongitude")
        alvo = nome or (f"{lat}, {lon}" if lat is not None else "")
        return f"[Localização] {alvo}".strip(), None

    contato = msg.get("contactMessage") or {}
    if contato:
        return f"[Contato] {contato.get('displayName') or ''}".strip(), None
    if msg.get("contactsArrayMessage"):
        return "[Contatos compartilhados]", None

    enquete = msg.get("pollCreationMessage") or msg.get("pollCreationMessageV3") or {}
    if enquete:
        return f"[Enquete] {enquete.get('name') or ''}".strip(), None

    return None, None


async def _base64_da_mensagem(
    data: dict, chave: str, detalhe: dict
) -> tuple[str | None, str | None]:
    """Base64 da mídia: do próprio webhook (base64: true) ou buscado na API."""
    msg = data.get("message") or {}
    mime = detalhe.get("mimetype")
    b64 = msg.get("base64") or (msg.get(chave) or {}).get("base64")
    if b64:
        return b64, mime
    try:
        baixada = await evolution.obter_midia_base64((data.get("key") or {}).get("id") or "")
    except Exception as e:  # noqa: BLE001 — sem o arquivo o texto ainda vale
        log.warning("getBase64FromMediaMessage falhou: %s", e)
        return None, mime
    return baixada.get("base64"), baixada.get("mimetype") or mime


def _guardar_midia(
    remote_jid: str, direcao: str, texto: str, descritor: dict | None, msg_id: str
) -> None:
    """Grava o arquivo + a linha da tabela Midia. `texto` é o marcador que foi
    para a memória: é por ele que o painel casa o arquivo com a bolha."""
    if not descritor:
        return
    if msg_id and db.midia_do_msg_id(msg_id):
        return  # reentrega do mesmo evento
    arquivo = midia.guardar(descritor.get("b64"), descritor.get("mime") or "", descritor.get("nome") or "")
    if not arquivo:
        return
    db.salvar_midia(
        telefone=normalizar(remote_jid) or remote_jid,
        momento=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        direcao=direcao,
        tipo=descritor.get("tipo") or "",
        mime=descritor.get("mime") or "",
        arquivo=arquivo,
        nome=descritor.get("nome") or "",
        legenda=descritor.get("legenda") or "",
        texto=texto,
        msg_id=msg_id,
    )


async def _registrar_saida(remote_jid: str, data: dict, key: dict) -> None:
    """Mensagem que saiu do nosso número (evento fromMe).

    Duas origens possíveis:
      - o próprio bot (sendText da API) — já está na memória, ignorar o eco;
      - o dono digitando no WhatsApp do negócio — é conversa real e precisa
        aparecer no painel, sem acionar o agente (ele não responde a si mesmo).
    """
    msg_id = key.get("id") or ""
    if evolution.enviado_por_nos(msg_id):
        return

    texto, descritor = await _conteudo_da_mensagem(data, de_nos=True)
    if texto is None:
        return
    # Rede de segurança para o eco que escapou do registro de ids (reinício do
    # container no meio do envio): se o bot acabou de dizer isso, não repete.
    if agente.foi_dito_pelo_bot(remote_jid, texto):
        return

    db.upsert_cliente(remote_jid)  # sem nome: o pushName aqui é o do DONO
    _guardar_midia(remote_jid, "bot", texto, descritor, msg_id)
    agente.registrar_na_memoria(remote_jid, texto, "bot", origem="aparelho")
    log.info("Mensagem do dono pelo celular registrada p/ %s", remote_jid)


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

    # Espera o turno anterior deste contato terminar. Só DEPOIS o buffer é
    # esvaziado: o que chegou durante aquela resposta entra neste mesmo lote,
    # em vez de virar uma segunda resposta.
    async with _locks.setdefault(remote_jid, asyncio.Lock()):
        mensagens = _buffers.pop(remote_jid, [])
        _timers.pop(remote_jid, None)
        if not mensagens:
            return

        lote = "[quebrar]".join(mensagens)  # paridade com o concat do n8n
        _em_voo[remote_jid] = mensagens
        try:
            await _responder_contato(remote_jid, lote)
        except ia.IANaoConfigurada as e:
            log.warning("%s", e)
        except Exception:  # noqa: BLE001
            log.exception("Erro respondendo %s", remote_jid)
        finally:
            # a memória já tem o turno (ou o erro descartou o lote): o painel
            # volta a ler tudo do banco
            _em_voo.pop(remote_jid, None)


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
    pipeline reativo E pelas ações proativas (app/tarefas.py).

    Cada bolha enviada vira uma linha em `MensagemRef`: é o que permite a
    reação do cliente a UMA bolha específica achar o lugar dela no painel."""
    for bolha in dividir_bolhas(resposta):
        # Digitação proporcional (fórmula do workflow): min(0.4+len*0.02, 4)+rand*0.7
        segundos = min(0.4 + len(bolha) * 0.02, 4.0) + random.random() * 0.7
        msg_id = await evolution.enviar_texto(
            numero, bolha, digitando_ms=int(segundos * 1000)
        )
        db.registrar_mensagem(msg_id, numero, "bot", bolha)


def contato_ocupado(telefone: str) -> bool:
    """True se o contato está no meio do debounce (mensagem dele em
    processamento) — tarefa proativa adia para não atropelar a conversa."""
    pendentes = set(_buffers) | set(_timers) | set(_em_voo)
    return any(mesmo_numero(telefone, jid) for jid in pendentes)


def mensagens_pendentes(telefone: str) -> list[str]:
    """Mensagens do contato já recebidas mas ainda fora da memória: as do
    debounce e as que estão com o agente. O painel as mostra na hora, sem
    esperar o bot responder."""
    fila: list[str] = []
    for mapa in (_em_voo, _buffers):
        for jid, msgs in mapa.items():
            if mesmo_numero(telefone, jid):
                fila.extend(msgs)
    return fila
