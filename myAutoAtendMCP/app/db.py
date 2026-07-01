"""Persistência real em SQLite via SQLModel.

Mantém as MESMAS assinaturas de função do esqueleto mockado — as tools e o
painel não precisam saber que houve troca de backend.

Concorrência: `criar_agendamento`/`reagendar_agendamento` rodam o par
"checar conflito + gravar" sob um `Lock` de processo. SQLite serializa
escritas por natureza; o lock fecha a janela de corrida entre o SELECT de
conflito e o INSERT dentro do mesmo processo (instância única).
"""

from __future__ import annotations

import json
from datetime import datetime
from threading import Lock
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import settings
from .phone import mesmo_numero

# ---------------------------------------------------------------------------
# Modelos (tabelas)
# ---------------------------------------------------------------------------


class Config(SQLModel, table=True):
    # Nota de migração: bancos antigos têm colunas órfãs (`instrucoes_gerais`,
    # `abertura`, `fechamento`, `duracao_slot_min` — funcionamento migrou para
    # a tabela HorarioFuncionamento; slot nunca foi lido). SQLite ignora
    # colunas fora do modelo — sem migração.
    id: int = Field(default=1, primary_key=True)
    telefone_dono: str = settings.owner_phone
    fuso: str = settings.timezone
    avisar_dono: bool = True  # aviso no WhatsApp do dono a cada ação do bot


class HorarioFuncionamento(SQLModel, table=True):
    """Intervalo de atendimento de um dia da semana (0=segunda … 6=domingo).

    Várias linhas por dia = vários intervalos (ex.: manhã e tarde).
    Dia sem linha nenhuma = fechado. Tabela vazia = tudo fechado (estado
    legítimo via "Apagar tudo" no painel — por isso o seed do padrão só
    acontece quando a tabela é criada, nunca quando está vazia).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    dia_semana: int  # 0=segunda … 6=domingo (convenção do datetime.weekday())
    inicio: str  # "HH:MM"
    fim: str  # "HH:MM"


class Prompt(SQLModel, table=True):
    """Partes do system prompt do agente editadas pelo painel.

    Chaves usadas: "geral" (instrução principal), "mcp_dono" e "mcp_cliente"
    (bloco de ferramentas — uma versão por perfil de remetente). A chave antiga
    "mcp" (bloco único) foi aposentada: `_migrar_prompts` copia um eventual
    texto customizado dela para as duas novas na primeira subida. Fonte de
    verdade do painel — o agente lê daqui a cada mensagem (app/agente.py),
    então salvar aplica na hora. Tabela separada da Config: ambientes antigos
    ganham a tabela nova no create_all sem precisar de migração de coluna.
    """

    chave: str = Field(primary_key=True)
    texto: str


class ProvedorIA(SQLModel, table=True):
    """Config de provedor de IA por uso (texto / audio / imagem).

    Substitui as credenciais que viviam no n8n. A chave fica no SQLite local
    (stack 127.0.0.1); nenhuma rota do painel devolve a chave de volta.
    """

    alvo: str = Field(primary_key=True)  # "texto" | "audio" | "imagem"
    api_key: str
    base_url: str
    modelo: str = ""
    atualizado_em: str = ""


class Conversa(SQLModel, table=True):
    """Histórico de conversa do agente por contato (remoteJid normalizado).

    `historico` é o JSON serializado das mensagens do pydantic-ai
    (ModelMessagesTypeAdapter), já aparado na janela — tamanho limitado.
    """

    telefone: str = Field(primary_key=True)
    historico: str
    atualizado_em: str = ""


class Servico(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nome: str
    descricao: str
    valor: float
    duracao_min: int
    ativo: bool = True


class Bloqueio(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    data: str  # "YYYY-MM-DD"
    data_fim: Optional[str] = None  # "YYYY-MM-DD" ou None p/ um dia só
    inicio: Optional[str] = None  # "HH:MM" ou None para o dia inteiro
    fim: Optional[str] = None
    motivo: str = ""


class Tarefa(SQLModel, table=True):
    """Fila persistente de ações proativas do bot (worker em app/tarefas.py).

    Qualquer feature que precise do bot iniciando conversa agenda uma linha
    aqui; o worker do lifespan consome respeitando janela de cortesia, rate
    limit e debounce ativo do contato. `telefone_alvo` em formato livre
    (E.164 ou jid) — o worker resolve a chave de memória do contato.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tipo: str  # "contatar_cliente" (remanejo de dia / aviso de ação do dono)
    telefone_alvo: str
    payload: str = "{}"  # JSON com os dados do tipo
    status: str = "pendente"  # pendente | executando | concluida | falhou
    agendado_para: str  # ISO local "YYYY-MM-DDTHH:MM" (fuso da Config)
    tentativas: int = 0
    criado_em: str = ""
    resultado: str = ""  # última resposta enviada ou erro


class Agendamento(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    servico_id: int
    telefone_cliente: str
    nome_cliente: str
    inicio: str  # ISO "YYYY-MM-DDTHH:MM"
    fim: str
    status: str = "ativo"  # ativo | cancelado
    observacoes: str = ""  # campo livre, opcional


# ---------------------------------------------------------------------------
# Engine + sessão
# ---------------------------------------------------------------------------

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)

# Serializa o par checar-conflito + gravar.
_lock = Lock()


def _session() -> Session:
    # expire_on_commit=False: objetos seguem legíveis após o commit/close.
    return Session(engine, expire_on_commit=False)


def init_db() -> None:
    # Seed do funcionamento: só quando a tabela ainda não existe (primeiro
    # boot ou upgrade). Tabela vazia ≠ tabela nova — "Apagar tudo" no painel
    # esvazia de propósito e não pode ser revertido por um restart.
    with engine.connect() as conn:
        tinha_horarios = bool(
            conn.exec_driver_sql("PRAGMA table_info(horariofuncionamento)").fetchall()
        )
    SQLModel.metadata.create_all(engine)
    _migrar()
    _migrar_prompts()
    with _session() as s:
        if s.get(Config, 1) is None:
            s.add(Config(id=1))
            s.commit()
    if not tinha_horarios:
        restaurar_horarios_padrao()


def _migrar() -> None:
    """Colunas adicionadas após a criação da tabela — o create_all não altera
    tabela existente, então o ALTER é manual (bloqueio.data_fim e
    agendamento.observacoes)."""
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(bloqueio)")}
        if cols and "data_fim" not in cols:
            conn.exec_driver_sql("ALTER TABLE bloqueio ADD COLUMN data_fim VARCHAR")
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(agendamento)")}
        if cols and "observacoes" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE agendamento ADD COLUMN observacoes VARCHAR NOT NULL DEFAULT ''"
            )
            conn.commit()
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(config)")}
        if cols and "avisar_dono" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE config ADD COLUMN avisar_dono BOOLEAN NOT NULL DEFAULT 1"
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_config() -> Config:
    with _session() as s:
        return s.get(Config, 1)


def update_config(**campos) -> Config:
    with _lock, _session() as s:
        cfg = s.get(Config, 1)
        for k, v in campos.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        s.add(cfg)
        s.commit()
        return cfg


# ---------------------------------------------------------------------------
# Horários de funcionamento (grade semanal de atendimento)
# ---------------------------------------------------------------------------

# Padrão: segunda a sexta, 08:00–12:00 e 13:30–18:00.
HORARIOS_PADRAO: list[tuple[int, str, str]] = [
    (dia, inicio, fim)
    for dia in range(5)
    for inicio, fim in (("08:00", "12:00"), ("13:30", "18:00"))
]


def listar_horarios() -> list[HorarioFuncionamento]:
    with _session() as s:
        stmt = select(HorarioFuncionamento).order_by(
            HorarioFuncionamento.dia_semana, HorarioFuncionamento.inicio
        )
        return list(s.exec(stmt).all())


def horarios_do_dia(dia_semana: int) -> list[HorarioFuncionamento]:
    with _session() as s:
        stmt = (
            select(HorarioFuncionamento)
            .where(HorarioFuncionamento.dia_semana == dia_semana)
            .order_by(HorarioFuncionamento.inicio)
        )
        return list(s.exec(stmt).all())


def substituir_horarios(intervalos: list[tuple[int, str, str]]) -> None:
    """Troca a grade inteira (apaga tudo + grava) numa única transação."""
    with _lock, _session() as s:
        for h in s.exec(select(HorarioFuncionamento)).all():
            s.delete(h)
        for dia, inicio, fim in intervalos:
            s.add(HorarioFuncionamento(dia_semana=dia, inicio=inicio, fim=fim))
        s.commit()


def restaurar_horarios_padrao() -> None:
    substituir_horarios(HORARIOS_PADRAO)


def limpar_horarios() -> None:
    substituir_horarios([])


def dentro_do_funcionamento(inicio: datetime, fim: datetime) -> bool:
    """True se [inicio, fim] cabe inteiro num intervalo de funcionamento do dia."""
    dia = inicio.date()
    for h in horarios_do_dia(dia.weekday()):
        h_ini = datetime.fromisoformat(f"{dia}T{h.inicio}")
        h_fim = datetime.fromisoformat(f"{dia}T{h.fim}")
        if inicio >= h_ini and fim <= h_fim:
            return True
    return False


# ---------------------------------------------------------------------------
# Prompts do agente (system prompt editado pelo painel)
# ---------------------------------------------------------------------------


def get_prompt(chave: str) -> str | None:
    with _session() as s:
        p = s.get(Prompt, chave)
        return p.texto if p else None


def set_prompt(chave: str, texto: str) -> None:
    with _lock, _session() as s:
        p = s.get(Prompt, chave)
        if p:
            p.texto = texto
        else:
            p = Prompt(chave=chave, texto=texto)
        s.add(p)
        s.commit()


def _migrar_prompts() -> None:
    """Migração suave da chave `mcp` (bloco único) → `mcp_dono` + `mcp_cliente`.

    Roda só enquanto nenhuma das duas novas chaves existe. Se o dono tinha um
    texto customizado no `mcp` legado, semeia AMBAS com ele (uma vez) para não
    perder a personalização — cabe ao dono depois enxugar a versão do cliente
    no painel. Sem valor customizado, não cria linha: o agente usa os defaults
    de app/agente.py. A chave `mcp` deixa de ser lida."""
    if get_prompt("mcp_dono") is not None or get_prompt("mcp_cliente") is not None:
        return
    legado = get_prompt("mcp")
    if legado is None:
        return
    set_prompt("mcp_dono", legado)
    set_prompt("mcp_cliente", legado)


# ---------------------------------------------------------------------------
# Provedores de IA (config local — antes vivia nas credenciais do n8n)
# ---------------------------------------------------------------------------


def get_provedor_ia(alvo: str) -> ProvedorIA | None:
    with _session() as s:
        return s.get(ProvedorIA, alvo)


def set_provedor_ia(
    alvo: str,
    api_key: str | None = None,
    base_url: str | None = None,
    modelo: str | None = None,
) -> ProvedorIA:
    """Cria/atualiza só os campos passados (None = mantém)."""
    with _lock, _session() as s:
        p = s.get(ProvedorIA, alvo)
        if p is None:
            p = ProvedorIA(alvo=alvo, api_key="", base_url="")
        if api_key is not None:
            p.api_key = api_key
        if base_url is not None:
            p.base_url = base_url
        if modelo is not None:
            p.modelo = modelo
        p.atualizado_em = datetime.now().isoformat(timespec="seconds")
        s.add(p)
        s.commit()
        return p


# ---------------------------------------------------------------------------
# Tarefas (fila de ações proativas — consumida pelo worker de app/tarefas.py)
# ---------------------------------------------------------------------------


def criar_tarefa(
    tipo: str, telefone_alvo: str, payload: dict, agendado_para: str
) -> Tarefa:
    with _lock, _session() as s:
        t = Tarefa(
            tipo=tipo,
            telefone_alvo=telefone_alvo,
            payload=json.dumps(payload, ensure_ascii=False),
            agendado_para=agendado_para,
            criado_em=datetime.now().isoformat(timespec="seconds"),
        )
        s.add(t)
        s.commit()
        return t


def criar_aviso_cliente(
    ag: Agendamento,
    acao: str,
    agendado_para: str,
    inicio_anterior: str | None = None,
) -> Tarefa:
    """Enfileira aviso proativo da IA ao cliente sobre ação do dono/admin no
    agendamento — `acao` "reagendado" ou "cancelado" (instruções por ação em
    app/tarefas.py).

    Avisos pendentes do mesmo agendamento são substituídos: ao cliente só
    interessa o estado final. Reagendamento em cima de outro ainda não avisado
    herda o `inicio_anterior` do aviso substituído — o único horário que o
    cliente conhece.
    """
    payload: dict = {"agendamento_id": ag.id, "acao": acao}
    if inicio_anterior:
        payload["inicio_anterior"] = inicio_anterior
    for antigo in _obsoletar_avisos_pendentes(ag.id):
        if (
            acao == "reagendado"
            and antigo.get("acao") == "reagendado"
            and antigo.get("inicio_anterior")
        ):
            payload["inicio_anterior"] = antigo["inicio_anterior"]
            break
    return criar_tarefa("contatar_cliente", ag.telefone_cliente, payload, agendado_para)


def _obsoletar_avisos_pendentes(agendamento_id: int) -> list[dict]:
    """Conclui os `contatar_cliente` pendentes do agendamento; retorna os
    payloads substituídos na ordem de criação."""
    with _lock, _session() as s:
        stmt = (
            select(Tarefa)
            .where(Tarefa.tipo == "contatar_cliente", Tarefa.status == "pendente")
            .order_by(Tarefa.id)
        )
        substituidos: list[dict] = []
        for t in s.exec(stmt):
            payload = json.loads(t.payload or "{}")
            if payload.get("agendamento_id") != agendamento_id:
                continue
            t.status = "concluida"
            t.resultado = "Substituída por aviso mais recente."
            s.add(t)
            substituidos.append(payload)
        s.commit()
        return substituidos


def tarefas_vencidas(agora: str) -> list[Tarefa]:
    """Pendentes com hora de disparo alcançada, na ordem de criação."""
    with _session() as s:
        stmt = (
            select(Tarefa)
            .where(Tarefa.status == "pendente", Tarefa.agendado_para <= agora)
            .order_by(Tarefa.id)
        )
        return list(s.exec(stmt).all())


def atualizar_tarefa(tarefa_id: int, **campos) -> None:
    with _lock, _session() as s:
        t = s.get(Tarefa, tarefa_id)
        if not t:
            return
        for k, v in campos.items():
            if hasattr(t, k):
                setattr(t, k, v)
        s.add(t)
        s.commit()


def resetar_tarefas_executando() -> int:
    """Volta `executando` → `pendente` (retomada após crash/restart).
    Pode causar um reenvio — aceitável, tentativas são limitadas."""
    with _lock, _session() as s:
        presas = s.exec(select(Tarefa).where(Tarefa.status == "executando")).all()
        for t in presas:
            t.status = "pendente"
            s.add(t)
        s.commit()
        return len(presas)


def listar_tarefas_painel(limite_falhadas: int = 20) -> list[Tarefa]:
    """Fila visível no painel: todas pendente/executando + as últimas N falhadas.
    Concluídas e canceladas ficam de fora. Ordem: ativas por `agendado_para`
    (próxima a disparar primeiro), falhadas por id desc (mais recente no topo)."""
    with _session() as s:
        ativas = list(
            s.exec(
                select(Tarefa)
                .where(Tarefa.status.in_(["pendente", "executando"]))
                .order_by(Tarefa.agendado_para, Tarefa.id)
            ).all()
        )
        falhadas = list(
            s.exec(
                select(Tarefa)
                .where(Tarefa.status == "falhou")
                .order_by(Tarefa.id.desc())
                .limit(limite_falhadas)
            ).all()
        )
        return ativas + falhadas


def cancelar_tarefa(tarefa_id: int) -> bool:
    """Remove uma tarefa da fila (marca `cancelada`). Só pendente — `executando`
    está em voo e `tarefas_vencidas` só pega `pendente`, então o worker ignora."""
    with _lock, _session() as s:
        t = s.get(Tarefa, tarefa_id)
        if not t or t.status != "pendente":
            return False
        t.status = "cancelada"
        s.add(t)
        s.commit()
    return True


def chaves_conversas() -> list[str]:
    """Chaves (remoteJid) com memória existente — p/ o worker reusar a chave
    do contato em vez de inventar outra (nono dígito muda o jid)."""
    with _session() as s:
        return [c.telefone for c in s.exec(select(Conversa)).all()]


# ---------------------------------------------------------------------------
# Conversas do agente (memória por contato)
# ---------------------------------------------------------------------------


def get_conversa(telefone: str) -> str | None:
    with _session() as s:
        c = s.get(Conversa, telefone)
        return c.historico if c else None


def set_conversa(telefone: str, historico: str) -> None:
    with _lock, _session() as s:
        c = s.get(Conversa, telefone)
        if c:
            c.historico = historico
        else:
            c = Conversa(telefone=telefone, historico=historico)
        c.atualizado_em = datetime.now().isoformat(timespec="seconds")
        s.add(c)
        s.commit()


# ---------------------------------------------------------------------------
# Serviços
# ---------------------------------------------------------------------------


def listar_servicos_ativos() -> list[Servico]:
    with _session() as s:
        return list(s.exec(select(Servico).where(Servico.ativo == True)).all())  # noqa: E712


def listar_todos_servicos() -> list[Servico]:
    with _session() as s:
        return list(s.exec(select(Servico)).all())


def get_servico(servico_id: int) -> Servico | None:
    with _session() as s:
        return s.get(Servico, servico_id)


def criar_servico(nome: str, descricao: str, valor: float, duracao_min: int) -> Servico:
    with _lock, _session() as s:
        novo = Servico(nome=nome, descricao=descricao, valor=valor, duracao_min=duracao_min)
        s.add(novo)
        s.commit()
        return novo


def editar_servico(servico_id: int, **campos) -> Servico | None:
    with _lock, _session() as s:
        srv = s.get(Servico, servico_id)
        if not srv:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(srv, k):
                setattr(srv, k, v)
        s.add(srv)
        s.commit()
        return srv


def deletar_servico(servico_id: int) -> bool:
    with _lock, _session() as s:
        srv = s.get(Servico, servico_id)
        if not srv:
            return False
        s.delete(srv)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Bloqueios
# ---------------------------------------------------------------------------


def listar_bloqueios() -> list[Bloqueio]:
    with _session() as s:
        return list(s.exec(select(Bloqueio)).all())


def criar_bloqueio(
    data: str,
    inicio: str | None,
    fim: str | None,
    motivo: str = "",
    data_fim: str | None = None,
) -> Bloqueio:
    """`data_fim` (exclusivo p/ período) cobre todos os dias de data até data_fim."""
    with _lock, _session() as s:
        if data_fim == data:
            data_fim = None
        b = Bloqueio(data=data, data_fim=data_fim, inicio=inicio, fim=fim, motivo=motivo)
        s.add(b)
        s.commit()
        return b


def remover_bloqueio(bloqueio_id: int) -> bool:
    with _lock, _session() as s:
        b = s.get(Bloqueio, bloqueio_id)
        if not b:
            return False
        s.delete(b)
        s.commit()
    return True


def remover_bloqueio_por_data(data: str, data_fim: str | None = None) -> int:
    """Remove bloqueios que intersectam o período [data, data_fim].

    Um bloqueio de período é removido por inteiro (sem split): reabrir um dia
    no meio de férias reabre as férias todas — o chamador deve avisar isso.
    """
    ate = data_fim or data
    with _lock, _session() as s:
        achados = [
            b
            for b in s.exec(select(Bloqueio).where(Bloqueio.data <= ate)).all()
            if (b.data_fim or b.data) >= data
        ]
        for b in achados:
            s.delete(b)
        s.commit()
        return len(achados)


# ---------------------------------------------------------------------------
# Agendamentos
# ---------------------------------------------------------------------------


def listar_agendamentos(apenas_ativos: bool = True) -> list[Agendamento]:
    with _session() as s:
        stmt = select(Agendamento)
        if apenas_ativos:
            stmt = stmt.where(Agendamento.status == "ativo")
        return list(s.exec(stmt).all())


def agendamentos_do_telefone(telefone: str) -> list[Agendamento]:
    with _session() as s:
        ativos = s.exec(select(Agendamento).where(Agendamento.status == "ativo")).all()
    return [a for a in ativos if mesmo_numero(a.telefone_cliente, telefone)]


def get_agendamento(agendamento_id: int) -> Agendamento | None:
    with _session() as s:
        return s.get(Agendamento, agendamento_id)


def _conflita(s: Session, inicio: str, fim: str, ignorar_id: int | None = None) -> bool:
    """Checa sobreposição com agendamentos ativos e bloqueios (usa a sessão dada)."""
    ini = datetime.fromisoformat(inicio)
    f = datetime.fromisoformat(fim)
    dia = ini.date().isoformat()

    for b in s.exec(select(Bloqueio).where(Bloqueio.data <= dia)).all():
        if (b.data_fim or b.data) < dia:  # período não alcança o dia
            continue
        if b.inicio is None:  # dia(s) inteiro(s) fechado(s)
            return True
        # janela de horário vale para cada dia do período
        b_ini = datetime.fromisoformat(f"{dia}T{b.inicio}")
        b_fim = datetime.fromisoformat(f"{dia}T{b.fim}")
        if ini < b_fim and f > b_ini:
            return True

    for a in s.exec(select(Agendamento).where(Agendamento.status == "ativo")).all():
        if a.id == ignorar_id:
            continue
        a_ini = datetime.fromisoformat(a.inicio)
        a_fim = datetime.fromisoformat(a.fim)
        if ini < a_fim and f > a_ini:
            return True
    return False


def horario_disponivel(inicio: str, fim: str, ignorar_id: int | None = None) -> bool:
    with _session() as s:
        return not _conflita(s, inicio, fim, ignorar_id)


def criar_agendamento(
    servico_id: int,
    telefone_cliente: str,
    nome_cliente: str,
    inicio: str,
    fim: str,
    observacoes: str = "",
) -> Agendamento | None:
    """Checa conflito e grava em uma única seção crítica (lock + SQLite serial)."""
    with _lock, _session() as s:
        if _conflita(s, inicio, fim):
            return None
        a = Agendamento(
            servico_id=servico_id,
            telefone_cliente=telefone_cliente,
            nome_cliente=nome_cliente,
            inicio=inicio,
            fim=fim,
            observacoes=observacoes.strip(),
        )
        s.add(a)
        s.commit()
        return a


def reagendar_agendamento(agendamento_id: int, novo_inicio: str, novo_fim: str) -> bool:
    with _lock, _session() as s:
        a = s.get(Agendamento, agendamento_id)
        if not a or a.status != "ativo":
            return False
        if _conflita(s, novo_inicio, novo_fim, ignorar_id=agendamento_id):
            return False
        a.inicio = novo_inicio
        a.fim = novo_fim
        s.add(a)
        s.commit()
    return True


def cancelar_agendamento(agendamento_id: int) -> bool:
    with _lock, _session() as s:
        a = s.get(Agendamento, agendamento_id)
        if not a or a.status != "ativo":
            return False
        a.status = "cancelado"
        s.add(a)
        s.commit()
    return True


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------


def como_dict(obj) -> dict:
    return obj.model_dump()


init_db()
