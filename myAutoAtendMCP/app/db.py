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
    id: int = Field(default=1, primary_key=True)
    telefone_dono: str = settings.owner_phone
    instrucoes_gerais: str = (
        "Você é o assistente de atendimento do estabelecimento. Seja cordial e "
        "confirme sempre data e horário antes de finalizar um agendamento."
    )
    fuso: str = settings.timezone
    abertura: str = "09:00"
    fechamento: str = "18:00"
    duracao_slot_min: int = 30


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
    with _session() as s:
        if s.get(Config, 1) is None:
            s.add(Config(id=1))
            s.commit()


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


def criar_bloqueio(data: str, inicio: str | None, fim: str | None, motivo: str = "") -> Bloqueio:
    with _lock, _session() as s:
        b = Bloqueio(data=data, inicio=inicio, fim=fim, motivo=motivo)
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


def remover_bloqueio_por_data(data: str) -> int:
    with _lock, _session() as s:
        achados = s.exec(select(Bloqueio).where(Bloqueio.data == data)).all()
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

    for b in s.exec(select(Bloqueio).where(Bloqueio.data == dia)).all():
        if b.inicio is None:  # dia inteiro fechado
            return True
        b_ini = datetime.fromisoformat(f"{b.data}T{b.inicio}")
        b_fim = datetime.fromisoformat(f"{b.data}T{b.fim}")
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
