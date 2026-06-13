"""Definição das ferramentas (tools) MCP.

Agrupadas por nível de permissão:
  - Abertas:           qualquer cliente
  - Dono ou próprio:   remarcar / cancelar
  - Dono:              gestão e visão completa

`telefone_solicitante` aparece na assinatura (opcional, último parâmetro),
mas o telefone EFETIVO vem do pipeline (contextvar via `auth.requester()`) —
o modelo não escolhe o número e não precisa preencher o campo.
Ver app/auth.py e app/whatsapp.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import auth, db, notificacoes
from .phone import mesmo_numero, normalizar

# A porta MCP só é exposta em localhost e na rede interna do Docker (clients
# alcançam via hostname `mcp_agendamentos:8000`). A proteção anti-DNS-rebinding
# do SDK validaria o Host header e rejeitaria esse hostname (HTTP 421
# "Invalid Host header"), então é desligada neste contexto fechado.
mcp = FastMCP(
    "agendamentos",
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)


def _agora_local() -> datetime:
    """Datetime atual no fuso da config, naïve (compatível com os ISO salvos)."""
    cfg = db.get_config()
    try:
        tz = ZoneInfo(cfg.fuso)
    except Exception:
        tz = ZoneInfo("America/Sao_Paulo")
    return datetime.now(tz).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Tools ABERTAS
# ---------------------------------------------------------------------------


@mcp.tool()
def listar_servicos() -> list[dict]:
    """Lista os serviços disponíveis com descrição, valor e duração."""
    return [db.como_dict(s) for s in db.listar_servicos_ativos()]


@mcp.tool()
def consultar_horarios_disponiveis(data: str, servico_id: int) -> dict:
    """Lista horários livres em uma data (formato YYYY-MM-DD) para um serviço.

    Gera slots dentro dos intervalos de funcionamento do dia da semana
    (grade configurada no painel) usando a duração do serviço. Dias sem
    expediente retornam lista vazia com aviso. Horários que já passaram
    (no fuso configurado) são omitidos.
    """
    servico = db.get_servico(servico_id)
    if not servico:
        return {"erro": "Serviço não encontrado."}

    try:
        dia = date.fromisoformat(data)
    except ValueError:
        return {"erro": "Data inválida. Use o formato YYYY-MM-DD."}

    intervalos = db.horarios_do_dia(dia.weekday())
    if not intervalos:
        return {
            "data": data,
            "servico": servico.nome,
            "horarios": [],
            "aviso": "Sem expediente neste dia (fechado).",
        }

    agora = _agora_local()
    livres: list[str] = []
    passo = timedelta(minutes=servico.duracao_min)
    for janela in intervalos:
        atual = datetime.fromisoformat(f"{data}T{janela.inicio}")
        limite = datetime.fromisoformat(f"{data}T{janela.fim}")
        while atual + passo <= limite:
            ini = atual.isoformat(timespec="minutes")
            fim = (atual + passo).isoformat(timespec="minutes")
            if atual >= agora and db.horario_disponivel(ini, fim):
                livres.append(atual.strftime("%H:%M"))
            atual += passo

    return {"data": data, "servico": servico.nome, "horarios": livres}


@mcp.tool()
def agendar(
    servico_id: int,
    nome_cliente: str,
    inicio: str,
    observacoes: str = "",
    telefone_solicitante: str | None = None,
) -> dict:
    """Cria um agendamento. `inicio` no formato YYYY-MM-DDTHH:MM.

    `observacoes` é OPCIONAL: preferências ou detalhes que o cliente mencionar
    (ex.: "portão azul", "alergia a perfume"). Não pergunte por observações;
    preencha só se o cliente comentar algo relevante.
    O telefone do cliente é o do solicitante (injetado pelo pipeline).
    """
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return auth.NEGADO_SEM_SOLICITANTE

    servico = db.get_servico(servico_id)
    if not servico:
        return {"erro": "Serviço não encontrado."}

    try:
        dt_inicio = datetime.fromisoformat(inicio)
    except ValueError:
        return {"erro": "Horário inválido. Use YYYY-MM-DDTHH:MM."}

    if dt_inicio < _agora_local():
        return {"erro": "Não é possível agendar em um horário no passado."}

    dt_fim = dt_inicio + timedelta(minutes=servico.duracao_min)
    if not db.dentro_do_funcionamento(dt_inicio, dt_fim):
        return {
            "erro": "Fora do horário de funcionamento. "
            "Consulte os horários disponíveis da data."
        }
    fim = dt_fim.isoformat(timespec="minutes")

    ag = db.criar_agendamento(
        servico_id=servico_id,
        telefone_cliente=normalizar(tel) or tel,
        nome_cliente=nome_cliente,
        inicio=dt_inicio.isoformat(timespec="minutes"),
        fim=fim,
        observacoes=observacoes,
    )
    if not ag:
        return {"erro": "Horário indisponível. Escolha outro."}
    notificacoes.notificar_dono("agendado", ag, tel)
    return {"ok": True, "agendamento": db.como_dict(ag)}


@mcp.tool()
def meus_agendamentos(telefone_solicitante: str | None = None) -> list[dict]:
    """Lista os agendamentos ativos do próprio solicitante."""
    tel = auth.requester(telefone_solicitante)
    if not tel:
        return [auth.NEGADO_SEM_SOLICITANTE]
    return [db.como_dict(a) for a in db.agendamentos_do_telefone(tel)]


# ---------------------------------------------------------------------------
# Tools DONO ou PRÓPRIO CLIENTE
# ---------------------------------------------------------------------------


def _enfileirar_aviso_cliente(
    ag, acao: str, telefone_solicitante: str | None, inicio_anterior: str | None = None
) -> bool:
    """Aviso proativo da IA ao cliente sobre ação do dono — só quando o DONO
    mexe em agendamento de OUTRA pessoa (avisar o próprio dono não faz sentido)."""
    if not auth.eh_dono(telefone_solicitante):
        return False
    if mesmo_numero(ag.telefone_cliente, db.get_config().telefone_dono):
        return False
    db.criar_aviso_cliente(
        ag,
        acao,
        _agora_local().isoformat(timespec="minutes"),
        inicio_anterior=inicio_anterior,
    )
    return True


@mcp.tool()
def reagendar(
    agendamento_id: int,
    novo_inicio: str,
    avisar_cliente: bool = False,
    telefone_solicitante: str | None = None,
) -> dict:
    """Remarca um agendamento. Cliente só remarca o próprio; dono remarca qualquer um.

    `avisar_cliente` [SÓ DONO]: quando o dono remarca o horário de um cliente,
    True faz a IA contatar o cliente em segundo plano avisando a mudança.
    Pergunte ao dono se ele quer avisar; só passe True com o aval explícito
    dele. Ignorado para clientes e para agendamentos do próprio dono.
    """
    if not auth.pode_mexer_no_agendamento(telefone_solicitante, agendamento_id):
        return auth.NEGADO_PROPRIO

    ag = db.get_agendamento(agendamento_id)
    if not ag:
        return {"erro": "Agendamento não encontrado."}
    inicio_anterior = ag.inicio

    servico = db.get_servico(ag.servico_id)
    try:
        dt_inicio = datetime.fromisoformat(novo_inicio)
    except ValueError:
        return {"erro": "Horário inválido. Use YYYY-MM-DDTHH:MM."}

    if dt_inicio < _agora_local():
        return {"erro": "Não é possível remarcar para um horário no passado."}

    dt_fim = dt_inicio + timedelta(minutes=servico.duracao_min)
    if not db.dentro_do_funcionamento(dt_inicio, dt_fim):
        return {
            "erro": "Fora do horário de funcionamento. "
            "Consulte os horários disponíveis da data."
        }
    novo_fim = dt_fim.isoformat(timespec="minutes")

    if not db.reagendar_agendamento(
        agendamento_id, dt_inicio.isoformat(timespec="minutes"), novo_fim
    ):
        return {"erro": "Novo horário indisponível."}
    atualizado = db.get_agendamento(agendamento_id)
    notificacoes.notificar_dono(
        "reagendado", atualizado, auth.requester(telefone_solicitante)
    )
    resultado = {"ok": True, "agendamento": db.como_dict(atualizado)}
    if avisar_cliente and _enfileirar_aviso_cliente(
        atualizado, "reagendado", telefone_solicitante, inicio_anterior=inicio_anterior
    ):
        resultado["cliente_sera_avisado"] = True
    return resultado


@mcp.tool()
def cancelar(
    agendamento_id: int,
    avisar_cliente: bool = False,
    telefone_solicitante: str | None = None,
) -> dict:
    """Cancela um agendamento. Cliente só cancela o próprio; dono cancela qualquer um.

    `avisar_cliente` [SÓ DONO]: quando o dono cancela o horário de um cliente,
    True faz a IA contatar o cliente em segundo plano avisando o cancelamento.
    Pergunte ao dono se ele quer avisar; só passe True com o aval explícito
    dele. Ignorado para clientes e para agendamentos do próprio dono.
    """
    if not auth.pode_mexer_no_agendamento(telefone_solicitante, agendamento_id):
        return auth.NEGADO_PROPRIO
    ag = db.get_agendamento(agendamento_id)  # dados p/ o aviso, antes de cancelar
    if not db.cancelar_agendamento(agendamento_id):
        return {"erro": "Agendamento não encontrado ou já cancelado."}
    notificacoes.notificar_dono("cancelado", ag, auth.requester(telefone_solicitante))
    resultado = {"ok": True}
    if avisar_cliente and _enfileirar_aviso_cliente(
        ag, "cancelado", telefone_solicitante
    ):
        resultado["cliente_sera_avisado"] = True
    return resultado


# ---------------------------------------------------------------------------
# Tools DONO
# ---------------------------------------------------------------------------


def _validar_periodo(data: str, data_fim: str | None) -> dict | None:
    """None se ok; dict de erro se datas inválidas ou fim antes do início."""
    try:
        ini = date.fromisoformat(data)
        fim = date.fromisoformat(data_fim) if data_fim else ini
    except ValueError:
        return {"erro": "Data inválida. Use o formato YYYY-MM-DD."}
    if fim < ini:
        return {"erro": "A data final é anterior à inicial."}
    return None


@mcp.tool()
def fechar_data(
    data: str,
    data_fim: str | None = None,
    motivo: str = "",
    telefone_solicitante: str | None = None,
) -> dict:
    """[DONO] Fecha um dia inteiro ou um período de dias (datas YYYY-MM-DD).

    Sem `data_fim` fecha só o dia `data`; com `data_fim` fecha todos os dias
    de `data` até `data_fim` (inclusive) — ex.: férias, reforma.
    """
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if erro := _validar_periodo(data, data_fim):
        return erro
    b = db.criar_bloqueio(
        data=data, inicio=None, fim=None, motivo=motivo, data_fim=data_fim
    )
    return {"ok": True, "bloqueio": db.como_dict(b)}


@mcp.tool()
def abrir_data(
    data: str, data_fim: str | None = None, telefone_solicitante: str | None = None
) -> dict:
    """[DONO] Reabre dia ou período fechado (remove bloqueios que toquem o intervalo).

    Atenção: um bloqueio de período é removido por inteiro — reabrir um dia no
    meio de férias reabre as férias todas. Avise o dono quando for o caso.
    """
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if erro := _validar_periodo(data, data_fim):
        return erro
    removidos = db.remover_bloqueio_por_data(data, data_fim)
    return {"ok": True, "removidos": removidos}


@mcp.tool()
def bloquear_horario(
    data: str,
    inicio: str,
    fim: str,
    motivo: str = "",
    data_fim: str | None = None,
    telefone_solicitante: str | None = None,
) -> dict:
    """[DONO] Bloqueia um intervalo de horas. inicio/fim no formato HH:MM.

    Com `data_fim`, a janela vale para CADA dia de `data` até `data_fim`
    (ex.: almoço bloqueado a semana inteira).
    """
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if erro := _validar_periodo(data, data_fim):
        return erro
    b = db.criar_bloqueio(
        data=data, inicio=inicio, fim=fim, motivo=motivo, data_fim=data_fim
    )
    return {"ok": True, "bloqueio": db.como_dict(b)}


@mcp.tool()
def remanejar_dia(
    data: str,
    acao: str = "remarcar",
    motivo: str = "",
    telefone_solicitante: str | None = None,
) -> dict:
    """[DONO] Imprevisto do dono: fecha o dia (YYYY-MM-DD) e aciona o bot para
    contatar cada cliente agendado, um a um, em segundo plano.

    `acao` = "remarcar": agendamentos seguem ativos e o bot oferece remarcação
    para outra data. `acao` = "cancelar": agendamentos são cancelados na hora
    e o bot avisa cada cliente. O contato respeita janela de cortesia e fila —
    pode levar alguns minutos.
    """
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    if acao not in ("remarcar", "cancelar"):
        return {"erro": 'Ação inválida: use "remarcar" ou "cancelar".'}
    if erro := _validar_periodo(data, None):
        return erro

    db.criar_bloqueio(
        data=data, inicio=None, fim=None,
        motivo=motivo or "imprevisto do dono", data_fim=None,
    )

    dono = db.get_config().telefone_dono
    afetados = [a for a in db.listar_agendamentos() if a.inicio.startswith(data)]
    agora = _agora_local().isoformat(timespec="minutes")
    contatados = 0
    for a in afetados:
        if acao == "cancelar":
            db.cancelar_agendamento(a.id)
        if mesmo_numero(a.telefone_cliente, dono):
            continue  # horário do próprio dono — não faz sentido contatá-lo
        db.criar_tarefa(
            tipo="contatar_cliente",
            telefone_alvo=a.telefone_cliente,
            payload={"agendamento_id": a.id, "acao": acao, "motivo": motivo},
            agendado_para=agora,
        )
        contatados += 1

    return {
        "ok": True,
        "data": data,
        "acao": acao,
        "dia_fechado": True,
        "agendamentos_afetados": len(afetados),
        "clientes_a_contatar": contatados,
    }


@mcp.tool()
def criar_servico(
    nome: str,
    descricao: str,
    valor: float,
    duracao_min: int,
    telefone_solicitante: str | None = None,
) -> dict:
    """[DONO] Cria um novo serviço no catálogo."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    s = db.criar_servico(nome, descricao, valor, duracao_min)
    return {"ok": True, "servico": db.como_dict(s)}


@mcp.tool()
def editar_servico(
    servico_id: int,
    nome: str | None = None,
    descricao: str | None = None,
    valor: float | None = None,
    duracao_min: int | None = None,
    ativo: bool | None = None,
    telefone_solicitante: str | None = None,
) -> dict:
    """[DONO] Edita um serviço existente."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    s = db.editar_servico(
        servico_id,
        nome=nome,
        descricao=descricao,
        valor=valor,
        duracao_min=duracao_min,
        ativo=ativo,
    )
    if not s:
        return {"erro": "Serviço não encontrado."}
    return {"ok": True, "servico": db.como_dict(s)}


@mcp.tool()
def ver_agenda_completa(telefone_solicitante: str | None = None) -> dict:
    """[DONO] Retorna todos os agendamentos ativos e bloqueios."""
    if not auth.eh_dono(telefone_solicitante):
        return auth.NEGADO_DONO
    return {
        "agendamentos": [db.como_dict(a) for a in db.listar_agendamentos()],
        "bloqueios": [db.como_dict(b) for b in db.listar_bloqueios()],
    }
