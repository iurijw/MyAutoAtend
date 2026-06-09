"""Camada de autorização — o núcleo de segurança.

Regra de ouro: quem é o solicitante NÃO é decidido pelo modelo. O n8n injeta
o telefone do remetente do webhook na requisição MCP (query `?solicitante=` ou
header `X-Solicitante-Telefone`); um middleware ASGI grava esse valor no
contextvar `solicitante_ctx`. As tools até recebem um argumento
`telefone_solicitante`, mas `requester()` SEMPRE prefere o valor injetado —
então o modelo não consegue se passar por outro número.

A decisão de "pode ou não pode" acontece aqui, em código, nunca no modelo.
"""

from __future__ import annotations

import contextvars

from . import db
from .phone import mesmo_numero

# Preenchido por requisição pelo middleware (ver app/main.py).
solicitante_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "solicitante", default=None
)


def requester(telefone_arg: str | None = None) -> str | None:
    """Telefone efetivo do solicitante: injetado pelo n8n tem prioridade."""
    return solicitante_ctx.get() or telefone_arg


def eh_dono(telefone_solicitante: str | None = None) -> bool:
    return mesmo_numero(requester(telefone_solicitante), db.get_config().telefone_dono)


def pode_mexer_no_agendamento(telefone_solicitante: str | None, agendamento_id: int) -> bool:
    """Dono pode tudo; cliente só mexe no próprio agendamento."""
    tel = requester(telefone_solicitante)
    if mesmo_numero(tel, db.get_config().telefone_dono):
        return True
    ag = db.get_agendamento(agendamento_id)
    if not ag:
        return False
    return mesmo_numero(ag.telefone_cliente, tel)


# Resposta padronizada de negação — útil para a IA explicar ao cliente.
NEGADO_DONO = {"erro": "Apenas o dono pode executar esta ação."}
NEGADO_PROPRIO = {
    "erro": "Você só pode alterar agendamentos feitos com o seu próprio número."
}
NEGADO_SEM_SOLICITANTE = {
    "erro": "Não foi possível identificar o telefone do solicitante."
}
