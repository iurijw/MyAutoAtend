"""Persistência real em SQLite via SQLModel.

Mantém as MESMAS assinaturas de função do esqueleto mockado — as tools e o
painel não precisam saber que houve troca de backend.

Concorrência: `criar_agendamento`/`reagendar_agendamento` rodam o par
"checar conflito + gravar" sob um `Lock` de processo. SQLite serializa
escritas por natureza; o lock fecha a janela de corrida entre o SELECT de
conflito e o INSERT dentro do mesmo processo (instância única).
"""

from __future__ import annotations

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
    # Nota de migração: bancos antigos têm a coluna `instrucoes_gerais` órfã
    # (o contexto da IA passou a viver no system prompt, card "Instruções do
    # Agente"). SQLite ignora colunas fora do modelo — sem migração.
    id: int = Field(default=1, primary_key=True)
    telefone_dono: str = settings.owner_phone
    fuso: str = settings.timezone
    abertura: str = "09:00"
    fechamento: str = "18:00"
    duracao_slot_min: int = 30


class Prompt(SQLModel, table=True):
    """Partes do system prompt do agente editadas pelo painel.

    Chaves usadas: "geral" (instrução principal) e "mcp" (bloco que ensina o
    agente a usar as ferramentas). Fonte de verdade do painel — o agente lê
    daqui a cada mensagem (app/agente.py), então salvar aplica na hora.
    Tabela separada da Config: ambientes antigos ganham a tabela nova no
    create_all sem precisar de migração de coluna.
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


class Agendamento(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    servico_id: int
    telefone_cliente: str
    nome_cliente: str
    inicio: str  # ISO "YYYY-MM-DDTHH:MM"
    fim: str
    status: str = "ativo"  # ativo | cancelado


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
    SQLModel.metadata.create_all(engine)
    _migrar()
    with _session() as s:
        if s.get(Config, 1) is None:
            s.add(Config(id=1))
            s.commit()


def _migrar() -> None:
    """Bancos criados antes do bloqueio por período não têm `data_fim` —
    o create_all não altera tabela existente, então o ALTER é manual."""
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(bloqueio)")}
        if cols and "data_fim" not in cols:
            conn.exec_driver_sql("ALTER TABLE bloqueio ADD COLUMN data_fim VARCHAR")
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
    servico_id: int, telefone_cliente: str, nome_cliente: str, inicio: str, fim: str
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
