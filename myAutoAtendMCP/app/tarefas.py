"""Worker de ações proativas — o bot iniciando conversa (fases 2/3 do plano).

Consome a tabela `Tarefa` (fila persistente em SQLite) num loop asyncio
pendurado no lifespan (app/main.py). Cada tarefa roda o agente com a MESMA
memória do contato (`agente.executar_tarefa`) e envia a resposta em bolhas
(`whatsapp.enviar_bolhas`) — a resposta do cliente cai no pipeline reativo
normal, que continua a conversa com contexto completo.

Guard-rails (Evolution/Baileys é API não-oficial — rajada proativa = risco
de ban):
  - janela de cortesia: só envia entre 08:00 e 20:00 no fuso da Config;
  - contato no meio do debounce (digitando agora) → adia para o próximo tick;
  - rate limit com jitter entre tarefas;
  - IA não configurada → tarefa segue pendente sem queimar tentativa;
  - máx. 3 tentativas; `executando` órfã de um crash volta a pendente no boot.

Instância única (mesmo pressuposto do debounce e do Lock do db).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re

from . import agente, db, ia, whatsapp
from .phone import mesmo_numero
from .tools import _agora_local

log = logging.getLogger("tarefas")

TICK_S = 30.0
JANELA_INICIO = "08:00"  # janela de cortesia p/ mensagens proativas
JANELA_FIM = "20:00"
MAX_TENTATIVAS = 3
PAUSA_ENTRE_TAREFAS_S = 3.0  # + jitter


async def worker() -> None:
    """Loop do lifespan: tick a cada TICK_S, erros nunca derrubam o worker."""
    presas = db.resetar_tarefas_executando()
    if presas:
        log.warning("%d tarefa(s) presas em 'executando' voltaram a pendente", presas)
    log.info("Worker de tarefas iniciado (tick %.0fs)", TICK_S)
    while True:
        try:
            await _tick()
        except Exception:  # noqa: BLE001 — worker é eterno
            log.exception("Erro no tick do worker de tarefas")
        await asyncio.sleep(TICK_S)


async def _tick() -> None:
    agora = _agora_local()
    if not (JANELA_INICIO <= agora.strftime("%H:%M") < JANELA_FIM):
        return  # fora da janela de cortesia — tarefas esperam
    for t in db.tarefas_vencidas(agora.isoformat(timespec="minutes")):
        if whatsapp.contato_ocupado(t.telefone_alvo):
            log.info("Tarefa %d adiada: contato %s no debounce", t.id, t.telefone_alvo)
            continue
        await _executar(t)
        await asyncio.sleep(PAUSA_ENTRE_TAREFAS_S + random.random() * 2)


async def _executar(t: db.Tarefa) -> None:
    db.atualizar_tarefa(t.id, status="executando", tentativas=t.tentativas + 1)
    try:
        instrucao = _montar_instrucao(t)
        if instrucao is None:
            db.atualizar_tarefa(
                t.id, status="concluida", resultado="Tarefa obsoleta — nada a fazer."
            )
            return
        alvo = _chave_contato(t.telefone_alvo)
        resposta = await agente.executar_tarefa(alvo, instrucao)
        await whatsapp.enviar_bolhas(alvo.split("@")[0], resposta)
        db.atualizar_tarefa(t.id, status="concluida", resultado=resposta[:500])
        log.info("Tarefa %d concluída (%s → %s)", t.id, t.tipo, t.telefone_alvo)
    except ia.IANaoConfigurada:
        # Sem chave de IA não há o que fazer — devolve sem queimar tentativa.
        db.atualizar_tarefa(t.id, status="pendente", tentativas=t.tentativas)
        log.warning("Tarefa %d adiada: IA não configurada", t.id)
    except Exception as e:  # noqa: BLE001
        if t.tentativas + 1 >= MAX_TENTATIVAS:
            db.atualizar_tarefa(t.id, status="falhou", resultado=str(e)[:500])
        else:
            db.atualizar_tarefa(t.id, status="pendente")
        log.exception("Tarefa %d falhou (tentativa %d)", t.id, t.tentativas + 1)


def _chave_contato(telefone: str) -> str:
    """remoteJid do contato. Reusa a chave de memória existente se houver —
    o jid real pode diferir do E.164 (nono dígito), e inventar outra chave
    racharia o histórico da conversa."""
    for chave in db.chaves_conversas():
        if mesmo_numero(chave, telefone):
            return chave
    return f"{re.sub(r'[^0-9]', '', telefone)}@s.whatsapp.net"


# ---------------------------------------------------------------------------
# Instruções por tipo de tarefa (texto que o agente recebe como [TAREFA INTERNA])
# ---------------------------------------------------------------------------


def _montar_instrucao(t: db.Tarefa) -> str | None:
    """Instrução para o agente, ou None se a tarefa ficou obsoleta."""
    payload = json.loads(t.payload or "{}")
    if t.tipo == "contatar_cliente":
        return _instrucao_contatar_cliente(payload)
    log.warning("Tarefa %d de tipo desconhecido: %s", t.id, t.tipo)
    return None


def _instrucao_contatar_cliente(payload: dict) -> str | None:
    ag = db.get_agendamento(payload.get("agendamento_id") or 0)
    if not ag:
        return None
    acao = payload.get("acao", "remarcar")
    if acao in ("remarcar", "reagendado") and ag.status != "ativo":
        return None  # cliente já cancelou/resolveu nesse meio tempo
    servico = db.get_servico(ag.servico_id)
    nome_servico = servico.nome if servico else f"serviço #{ag.servico_id}"
    data, hora = (ag.inicio.split("T") + [""])[:2]

    if acao in ("remarcar", "cancelar"):  # remanejar_dia: o dia inteiro fechou
        motivo = payload.get("motivo") or "um imprevisto"
        base = (
            f"O dono teve um imprevisto ({motivo}) e não poderá atender no dia {data}. "
            f"O cliente {ag.nome_cliente} tem \"{nome_servico}\" às {hora} "
            f"(agendamento #{ag.id}). "
        )
        if acao == "cancelar":
            return base + (
                "Esse agendamento JÁ FOI CANCELADO pelo dono. Avise o cliente com "
                "delicadeza, peça desculpas pelo transtorno e ofereça ajuda para "
                "marcar uma nova data se ele quiser."
            )
        return base + (
            "Avise o cliente, peça desculpas pelo transtorno e ofereça remarcação "
            "para outra data (a data original está fechada — use "
            "consultar_horarios_disponiveis para sugerir opções). Se o cliente "
            "preferir cancelar, cancele com a ferramenta."
        )

    # Ação individual do dono/admin (painel ou tool com avisar_cliente=True).
    if acao == "cancelado":
        return (
            f"O dono cancelou o agendamento #{ag.id} de {ag.nome_cliente}: "
            f"\"{nome_servico}\" do dia {data} às {hora}. O cancelamento JÁ FOI "
            "FEITO — não tente desfazer nem cancelar de novo. Avise o cliente "
            "com delicadeza, peça desculpas pelo transtorno e ofereça ajuda "
            "para marcar uma nova data se ele quiser."
        )
    if acao == "reagendado":
        antes = payload.get("inicio_anterior") or ""
        data_ant, hora_ant = (antes.split("T") + [""])[:2]
        de = f"que era no dia {data_ant} às {hora_ant} " if antes else ""
        return (
            f"O dono remarcou o agendamento #{ag.id} de {ag.nome_cliente} "
            f"(\"{nome_servico}\") {de}para o dia {data} às {hora}. A mudança "
            "JÁ ESTÁ FEITA. Avise o cliente, peça desculpas pelo transtorno e "
            "pergunte se o novo horário funciona para ele. Se não funcionar, "
            "ofereça outros horários (consultar_horarios_disponiveis) e "
            "remarque, ou cancele se ele preferir."
        )
    log.warning("Ação desconhecida em contatar_cliente: %s", acao)
    return None
