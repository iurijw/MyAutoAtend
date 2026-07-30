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
import re
from datetime import datetime
from threading import Lock
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import settings
from .phone import mesmo_numero, normalizar

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
    ficha_ativa: bool = False  # ficha de cadastro do cliente (feature opcional)


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


class Cliente(SQLModel, table=True):
    """Contato que já apareceu no WhatsApp do bot (telefone E.164 como PK).

    Criada sob demanda: upsert no pipeline do webhook (aproveita o pushName
    para o nome) e ao pausar pelo painel. Nasce só com nome + estado de pausa,
    mas vai crescer (ficha de cadastro, memória por cliente) — por isso os
    acessos passam pelos helpers get/upsert. Tabela nova: o create_all cobre,
    sem ALTER. A chave é o E.164 normalizado; a memória (Conversa) continua
    indexada pelo remoteJid bruto — `resolver_chave_conversa` faz a ponte.
    """

    telefone: str = Field(primary_key=True)  # E.164 normalizado
    nome: str = ""
    bot_pausado: bool = False


class CampoFicha(SQLModel, table=True):
    """Campo da ficha de cadastro do cliente — definido pelo dono no painel.

    `chave` é o identificador estável usado pelo agente (slug do rótulo na
    criação); renomear o rótulo não muda a chave, então valores já gravados
    continuam válidos. `tipo` decide a validação (ver app/ficha.py) e o input
    do painel. `descricao` é a dica que o agente lê para saber o que coletar.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    chave: str = Field(index=True)
    rotulo: str
    tipo: str = "texto"  # ver ficha.TIPOS
    opcoes: str = ""  # tipo "selecao": alternativas separadas por ;
    descricao: str = ""  # dica p/ o agente ("como o cliente prefere ser chamado")
    obrigatorio: bool = False
    ordem: int = 0
    ativo: bool = True


class ValorFicha(SQLModel, table=True):
    """Valor de um campo da ficha para um contato (telefone E.164 + campo).

    Guardado sempre como texto já normalizado pelo tipo (app/ficha.py) — a
    conversão para exibição/uso fica na borda. `origem` registra quem
    preencheu: "agente" (durante a conversa) ou "painel" (o dono).
    """

    telefone: str = Field(primary_key=True)
    campo_id: int = Field(primary_key=True)
    valor: str = ""
    origem: str = "painel"
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
        if cols and "ficha_ativa" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE config ADD COLUMN ficha_ativa BOOLEAN NOT NULL DEFAULT 0"
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
    motivo: str = "",
) -> Tarefa:
    """Enfileira aviso proativo da IA ao cliente sobre ação do dono/admin no
    agendamento — `acao` "reagendado"/"cancelado" (ação individual) ou
    "remarcar"/"cancelar" (dia remanejado, com `motivo`). Instruções por ação
    em app/tarefas.py.

    Avisos pendentes do mesmo agendamento são substituídos: ao cliente só
    interessa o estado final. O aviso novo herda o `inicio_anterior` do aviso
    substituído — é o único horário que o cliente conhece, tanto para remarcar
    de novo quanto para cancelar em cima de uma remarcação não avisada.
    """
    payload: dict = {"agendamento_id": ag.id, "acao": acao}
    if inicio_anterior:
        payload["inicio_anterior"] = inicio_anterior
    if motivo:
        payload["motivo"] = motivo
    herdeiros = _obsoletar_avisos_pendentes(ag.id)
    # Cancelamento chega aqui DEPOIS de db.cancelar_agendamento, que já
    # descartou o aviso pendente — o horário que o cliente conhece só existe
    # no aviso obsoleto, então ele também conta como fonte.
    herdeiros += _avisos_obsoletos(ag.id)
    for antigo in herdeiros:
        if antigo.get("acao") in ("reagendado", "remarcar") and antigo.get(
            "inicio_anterior"
        ):
            payload.setdefault("inicio_anterior", antigo["inicio_anterior"])
            break
    return criar_tarefa("contatar_cliente", ag.telefone_cliente, payload, agendado_para)


# Status de aviso que morreu antes de ser enviado (substituído por um mais
# novo ou descartado com o agendamento). Fora dos filtros do painel (que só
# olham pendente/executando/falhou) e do worker (só pendente); separado de
# "concluida" porque concluída significa MENSAGEM ENVIADA — e é isso que decide
# se o cliente já conhece o horário novo.
STATUS_AVISO_OBSOLETO = "obsoleto"


def _obsoletar_avisos_pendentes(
    agendamento_id: int, motivo: str = "Substituída por aviso mais recente."
) -> list[dict]:
    """Tira da fila os `contatar_cliente` pendentes do agendamento; retorna os
    payloads descartados na ordem de criação.

    NÃO pega o `_lock` de quem chama: use fora de um `with _lock` (o Lock é
    simples, não reentrante).
    """
    with _lock, _session() as s:
        stmt = (
            select(Tarefa)
            .where(Tarefa.tipo == "contatar_cliente", Tarefa.status == "pendente")
            .order_by(Tarefa.id)
        )
        descartados: list[dict] = []
        for t in s.exec(stmt):
            payload = json.loads(t.payload or "{}")
            if payload.get("agendamento_id") != agendamento_id:
                continue
            t.status = STATUS_AVISO_OBSOLETO
            t.resultado = motivo
            s.add(t)
            descartados.append(payload)
        s.commit()
        return descartados


def limpar_avisos_orfaos() -> int:
    """Tira da fila avisos pendentes que perderam o sentido (chamada no boot).

    Rede de segurança para o que ficou na fila antes de `cancelar_agendamento`
    passar a descartar aviso pendente, e para agendamento apagado por fora. Só
    mexe em aviso que PRESSUPÕE agendamento ativo ("reagendado"/"remarcar") ou
    cujo agendamento não existe mais — aviso de cancelamento fala justamente de
    um agendamento cancelado e continua válido.
    """
    with _lock, _session() as s:
        stmt = select(Tarefa).where(
            Tarefa.tipo == "contatar_cliente", Tarefa.status == "pendente"
        )
        limpos = 0
        for t in s.exec(stmt):
            payload = json.loads(t.payload or "{}")
            ag = s.get(Agendamento, payload.get("agendamento_id") or 0)
            presume_ativo = payload.get("acao") in ("reagendado", "remarcar")
            if ag and not (presume_ativo and ag.status != "ativo"):
                continue
            t.status = STATUS_AVISO_OBSOLETO
            t.resultado = "Agendamento cancelado — aviso descartado."
            s.add(t)
            limpos += 1
        s.commit()
        return limpos


def _avisos_obsoletos(agendamento_id: int) -> list[dict]:
    """Payloads dos avisos do agendamento que nunca chegaram ao cliente, do mais
    recente para o mais antigo."""
    with _session() as s:
        stmt = (
            select(Tarefa)
            .where(
                Tarefa.tipo == "contatar_cliente",
                Tarefa.status == STATUS_AVISO_OBSOLETO,
            )
            .order_by(Tarefa.id.desc())
        )
        achados: list[dict] = []
        for t in s.exec(stmt):
            payload = json.loads(t.payload or "{}")
            if payload.get("agendamento_id") == agendamento_id:
                achados.append(payload)
        return achados


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


def listar_conversas() -> list[Conversa]:
    """Todas as conversas com memória (fonte da lista de conversas do painel)."""
    with _session() as s:
        return list(s.exec(select(Conversa)).all())


def resolver_chave_conversa(telefone: str) -> str:
    """remoteJid da memória de um contato a partir de um telefone qualquer.

    Reusa a chave existente se o número bater (o jid real pode diferir do
    E.164 pelo nono dígito, e inventar outra chave racharia o histórico);
    sem conversa ainda, constrói o jid a partir dos dígitos.
    """
    for chave in chaves_conversas():
        if mesmo_numero(chave, telefone):
            return chave
    return f"{re.sub(r'[^0-9]', '', telefone or '')}@s.whatsapp.net"


# ---------------------------------------------------------------------------
# Clientes (contatos conhecidos: nome + pausa do bot — cresce depois)
# ---------------------------------------------------------------------------


def get_cliente(telefone: str) -> Cliente | None:
    """Contato pelo telefone (normalizado para E.164). None se nunca falou."""
    with _session() as s:
        return s.get(Cliente, normalizar(telefone) or telefone)


def listar_clientes() -> list[Cliente]:
    with _session() as s:
        return list(s.exec(select(Cliente)).all())


def upsert_cliente(telefone: str, nome: str | None = None) -> Cliente:
    """Cria ou atualiza um contato. `nome` só sobrescreve quando vem
    preenchido — um pushName vazio não apaga o nome já salvo."""
    chave = normalizar(telefone) or telefone
    with _lock, _session() as s:
        c = s.get(Cliente, chave)
        if c is None:
            c = Cliente(telefone=chave)
        if nome and nome.strip():
            c.nome = nome.strip()
        s.add(c)
        s.commit()
        return c


def set_pausa_cliente(telefone: str, pausado: bool) -> Cliente:
    """Liga/desliga a pausa do bot para um contato (upsert)."""
    chave = normalizar(telefone) or telefone
    with _lock, _session() as s:
        c = s.get(Cliente, chave)
        if c is None:
            c = Cliente(telefone=chave)
        c.bot_pausado = pausado
        s.add(c)
        s.commit()
        return c


def cliente_pausado(telefone: str) -> bool:
    """True se o bot está pausado para este contato."""
    c = get_cliente(telefone)
    return bool(c and c.bot_pausado)


def renomear_cliente(telefone: str, nome: str) -> Cliente:
    """Muda o nome do contato E o nome gravado nos agendamentos dele.

    O `nome_cliente` do agendamento é uma foto do momento da marcação (é o que
    a lista da agenda mostra); corrigir só a tabela Cliente deixaria a agenda
    exibindo o nome errado para sempre.
    """
    chave = normalizar(telefone) or telefone
    limpo = (nome or "").strip()
    cliente = upsert_cliente(chave, limpo)
    if not limpo:
        return cliente
    with _lock, _session() as s:
        for a in s.exec(select(Agendamento)).all():
            if mesmo_numero(a.telefone_cliente, chave) and a.nome_cliente != limpo:
                a.nome_cliente = limpo
                s.add(a)
        s.commit()
    return cliente


def contato_tem_rastro(telefone: str) -> bool:
    """Já existe algo gravado sob este número? (contato, memória ou agenda)

    Usado antes de mover um contato de número: destino com rastro é OUTRA
    pessoa (ou o mesmo cliente já cadastrado duas vezes) — juntar os dois é
    decisão do dono, não efeito colateral de uma correção de telefone.
    """
    chave = normalizar(telefone) or telefone
    if get_cliente(chave):
        return True
    if agendamentos_do_telefone(chave):
        return True
    with _session() as s:
        for c in s.exec(select(Conversa)).all():
            if mesmo_numero(c.telefone, chave):
                return True
        for v in s.exec(select(ValorFicha)).all():
            if mesmo_numero(v.telefone, chave):
                return True
    return False


def mover_contato(antigo: str, novo: str, nome: str | None = None) -> dict:
    """Troca o telefone de um contato levando TUDO que é indexado por ele.

    O telefone é chave em várias tabelas (Cliente PK, ValorFicha PK composta,
    Conversa pelo remoteJid, Agendamento.telefone_cliente, Tarefa.telefone_alvo)
    — corrigir o número num lugar só deixaria a ficha órfã e o bot escrevendo
    para o número errado. Move linha por linha, numa transação: destino recebe
    cópia, origem é apagada. `nome`, quando vem, é aplicado no contato e nos
    agendamentos dele (o nome do agendamento é uma foto do momento da marcação).

    Chame só depois de checar `contato_tem_rastro(novo)` — aqui não há merge.
    """
    origem = normalizar(antigo) or antigo
    destino = normalizar(novo) or novo
    movidos = {"ficha": 0, "agendamentos": 0, "tarefas": 0, "conversas": 0}
    if origem == destino:
        return movidos

    jid_destino = f"{re.sub(r'[^0-9]', '', destino)}@s.whatsapp.net"
    with _lock, _session() as s:
        # Cliente: PK muda, então é cópia + delete (nome/pausa preservados).
        antigo_cliente = s.get(Cliente, origem)
        novo_cliente = Cliente(
            telefone=destino,
            nome=(nome.strip() if nome and nome.strip() else "")
            or (antigo_cliente.nome if antigo_cliente else ""),
            bot_pausado=bool(antigo_cliente.bot_pausado) if antigo_cliente else False,
        )
        if antigo_cliente:
            s.delete(antigo_cliente)
            s.flush()
        s.add(novo_cliente)

        # Ficha: o valor da origem manda (o destino não deveria ter rastro).
        for v in s.exec(select(ValorFicha).where(ValorFicha.telefone == origem)).all():
            ocupado = s.get(ValorFicha, (destino, v.campo_id))
            if ocupado:
                s.delete(ocupado)
            valor, orig, quando = v.valor, v.origem, v.atualizado_em
            campo_id = v.campo_id
            s.delete(v)
            s.flush()
            s.add(
                ValorFicha(
                    telefone=destino,
                    campo_id=campo_id,
                    valor=valor,
                    origem=orig,
                    atualizado_em=quando,
                )
            )
            movidos["ficha"] += 1

        # Memória: a chave é o remoteJid, comparado de forma tolerante.
        for c in s.exec(select(Conversa)).all():
            if not mesmo_numero(c.telefone, origem):
                continue
            historico, quando = c.historico, c.atualizado_em
            s.delete(c)
            s.flush()
            existente = s.get(Conversa, jid_destino)
            if existente:
                existente.historico = historico
                existente.atualizado_em = quando
                s.add(existente)
            else:
                s.add(
                    Conversa(
                        telefone=jid_destino, historico=historico, atualizado_em=quando
                    )
                )
            movidos["conversas"] += 1

        # Agendamentos (inclusive cancelados: o histórico segue o contato).
        for a in s.exec(select(Agendamento)).all():
            if not mesmo_numero(a.telefone_cliente, origem):
                continue
            a.telefone_cliente = destino
            if nome and nome.strip():
                a.nome_cliente = nome.strip()
            s.add(a)
            movidos["agendamentos"] += 1

        # Avisos proativos ainda na fila: senão o bot escreve pro número velho.
        for t in s.exec(select(Tarefa)).all():
            if t.status not in ("pendente", "executando"):
                continue
            if not mesmo_numero(t.telefone_alvo, origem):
                continue
            t.telefone_alvo = destino
            s.add(t)
            movidos["tarefas"] += 1

        s.commit()
    return movidos


# ---------------------------------------------------------------------------
# Ficha de cadastro (campos definidos pelo dono + valores por contato)
# ---------------------------------------------------------------------------


def listar_campos_ficha(apenas_ativos: bool = False) -> list[CampoFicha]:
    with _session() as s:
        campos = list(s.exec(select(CampoFicha)).all())
    if apenas_ativos:
        campos = [c for c in campos if c.ativo]
    campos.sort(key=lambda c: (c.ordem, c.id or 0))
    return campos


def get_campo_ficha(campo_id: int) -> CampoFicha | None:
    with _session() as s:
        return s.get(CampoFicha, campo_id)


def get_campo_ficha_por_chave(chave: str) -> CampoFicha | None:
    alvo = (chave or "").strip().lower()
    if not alvo:
        return None
    with _session() as s:
        campos = list(s.exec(select(CampoFicha).where(CampoFicha.chave == alvo)).all())
    return campos[0] if campos else None


def criar_campo_ficha(
    chave: str,
    rotulo: str,
    tipo: str,
    opcoes: str = "",
    descricao: str = "",
    obrigatorio: bool = False,
) -> CampoFicha:
    """Cria um campo no fim da ordem. A chave já vem única (ver app/ficha.py)."""
    with _lock, _session() as s:
        ultima = max(
            (c.ordem for c in s.exec(select(CampoFicha)).all()), default=-1
        )
        c = CampoFicha(
            chave=chave,
            rotulo=rotulo,
            tipo=tipo,
            opcoes=opcoes,
            descricao=descricao,
            obrigatorio=obrigatorio,
            ordem=ultima + 1,
        )
        s.add(c)
        s.commit()
        return c


def editar_campo_ficha(campo_id: int, **campos) -> CampoFicha | None:
    with _lock, _session() as s:
        c = s.get(CampoFicha, campo_id)
        if not c:
            return None
        for k, v in campos.items():
            if v is not None and hasattr(c, k):
                setattr(c, k, v)
        s.add(c)
        s.commit()
        return c


def deletar_campo_ficha(campo_id: int) -> bool:
    """Remove o campo E os valores já preenchidos dele (não sobra órfão)."""
    with _lock, _session() as s:
        c = s.get(CampoFicha, campo_id)
        if not c:
            return False
        for v in s.exec(select(ValorFicha).where(ValorFicha.campo_id == campo_id)).all():
            s.delete(v)
        s.delete(c)
        s.commit()
        return True


def mover_campo_ficha(campo_id: int, para_cima: bool) -> bool:
    """Troca a ordem com o vizinho — reordenação do painel (setas ↑ ↓)."""
    with _lock, _session() as s:
        campos = sorted(
            s.exec(select(CampoFicha)).all(), key=lambda c: (c.ordem, c.id or 0)
        )
        pos = next((i for i, c in enumerate(campos) if c.id == campo_id), None)
        if pos is None:
            return False
        vizinho = pos - 1 if para_cima else pos + 1
        if not 0 <= vizinho < len(campos):
            return False
        # Reescreve a ordem inteira: bancos antigos podem ter empates em 0.
        campos[pos], campos[vizinho] = campos[vizinho], campos[pos]
        for i, c in enumerate(campos):
            c.ordem = i
            s.add(c)
        s.commit()
        return True


def valores_ficha(telefone: str) -> dict[int, ValorFicha]:
    """Valores de um contato indexados por campo_id."""
    chave = normalizar(telefone) or telefone
    with _session() as s:
        linhas = s.exec(select(ValorFicha).where(ValorFicha.telefone == chave)).all()
    return {v.campo_id: v for v in linhas}


def set_valor_ficha(
    telefone: str, campo_id: int, valor: str, origem: str = "painel"
) -> ValorFicha | None:
    """Grava (ou apaga, se `valor` vier vazio) o valor de um campo do contato."""
    chave = normalizar(telefone) or telefone
    with _lock, _session() as s:
        atual = s.get(ValorFicha, (chave, campo_id))
        if not (valor or "").strip():
            if atual:
                s.delete(atual)
                s.commit()
            return None
        if atual is None:
            atual = ValorFicha(telefone=chave, campo_id=campo_id)
        atual.valor = valor
        atual.origem = origem
        atual.atualizado_em = datetime.now().isoformat(timespec="seconds")
        s.add(atual)
        s.commit()
        return atual


def preenchimento_fichas() -> dict[str, int]:
    """Quantos campos cada contato tem preenchidos — coluna da lista de clientes."""
    with _session() as s:
        linhas = s.exec(select(ValorFicha)).all()
    ativos = {c.id for c in listar_campos_ficha(apenas_ativos=True)}
    contagem: dict[str, int] = {}
    for v in linhas:
        if v.campo_id in ativos and (v.valor or "").strip():
            contagem[v.telefone] = contagem.get(v.telefone, 0) + 1
    return contagem


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
    # Aviso proativo pendente deste agendamento morre com ele: avisar uma
    # remarcação de horário que já foi cancelado é pior que não avisar nada.
    # Se o dono quiser avisar o cancelamento, o caller enfileira o aviso
    # "cancelado" DEPOIS desta chamada (que herda o inicio_anterior daqui).
    # Fora do `with _lock` — _obsoletar_avisos_pendentes pega o mesmo Lock.
    _obsoletar_avisos_pendentes(
        agendamento_id, "Agendamento cancelado — aviso descartado."
    )
    return True


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------


def como_dict(obj) -> dict:
    return obj.model_dump()


init_db()
