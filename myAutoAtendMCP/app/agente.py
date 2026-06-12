"""Agente de IA do WhatsApp — pydantic-ai + tools locais + memória no SQLite.

Substitui o node "Agente IA" do n8n. As tools são as MESMAS funções de
app/tools.py (o decorator do FastMCP devolve a função original): o pydantic-ai
gera o schema a partir das assinaturas, e a autorização continua na camada
auth — o pipeline grava o remetente no contextvar `solicitante_ctx` antes de
rodar o agente, então `auth.requester()` ignora o que o modelo inventar em
`telefone_solicitante` (mesma regra de ouro de antes).

Memória por contato: histórico serializado (ModelMessagesTypeAdapter) na
tabela Conversa, janela de 50 mensagens (paridade com o Redis Chat Memory).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    SystemPromptPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from . import db, ia, tools

# Janela de memória (nº de mensagens do modelo) — paridade com o n8n (50).
JANELA_MEMORIA = 50

_DIAS_SEMANA = [
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
]

# Cabeçalhos usados p/ separar o prompt em partes editáveis pelo painel.
_SECAO_MCP = "## Ferramentas (MCP Agendamentos)"
_SECAO_FORMATACAO = "## Formatação"

# Bloco "infra" do prompt: tools + formatação das mensagens. Editável pelo
# painel (seção avançada, com aviso), restaurável a este padrão. Deve refletir
# as tools de app/tools.py — atualizar os dois juntos. A parte de formatação
# está amarrada ao split de bolhas em app/whatsapp.py.
PROMPT_MCP_PADRAO = f"""{_SECAO_MCP}
Use SEMPRE as ferramentas para qualquer dado real — nunca invente serviços, preços, horários ou agendamentos.
- listar_servicos: catálogo com nome, descrição, valor e duração.
- consultar_horarios_disponiveis(data, servico_id): horários livres numa data (YYYY-MM-DD). Respeita o horário de funcionamento por dia da semana; dia fechado volta vazio com aviso — ofereça outra data.
- agendar(servico_id, nome_cliente, inicio, observacoes): cria agendamento; inicio = YYYY-MM-DDTHH:MM; observacoes é opcional (detalhes que o cliente mencionar — não pergunte por elas).
- meus_agendamentos: agendamentos do próprio cliente.
- reagendar(agendamento_id, novo_inicio) e cancelar(agendamento_id).
- NÃO peça nem use telefone: cliente E dono são identificados automaticamente pelo número do WhatsApp. NUNCA peça telefone para confirmar identidade. Não preencha o campo telefone_solicitante.

{_SECAO_FORMATACAO} (quebra de linha)
- O texto é dividido em bolhas de WhatsApp. Use [quebrar] OU Enter para separar bolhas. Máximo 2-3 bolhas por resposta. No máximo *negrito* do WhatsApp."""

# Instrução geral padrão (antes vivia na âncora x-agent-prompt do compose).
PROMPT_GERAL_PADRAO = """Você é o assistente virtual do estabelecimento, atendendo clientes pelo WhatsApp.

## Regras
- Antes de agendar, reagendar ou cancelar, CONFIRME com o cliente o serviço, a data e o horário.
- Converta datas relativas (amanhã, sexta) para YYYY-MM-DD usando a data atual informada no início.
- Se faltar o nome do cliente para agendar, pergunte.
- Mostre valores em reais e durações em minutos.
- Gestão (fechar/abrir data ou período de datas, bloquear horário, criar/editar serviço, ver agenda completa) é restrita ao dono.
- Se uma ferramenta retornar erro, explique de forma simples e ofereça alternativa.

## Persona
- Fale como gente: tom cordial, brasileiro, informal e direto. Use contrações. Emojis com moderação.
- Seja breve, como numa conversa real de WhatsApp. Evite listas formais e linguagem corporativa."""

# Tools expostas ao agente — funções originais de app/tools.py.
_TOOLS = [
    tools.listar_servicos,
    tools.consultar_horarios_disponiveis,
    tools.agendar,
    tools.meus_agendamentos,
    tools.reagendar,
    tools.cancelar,
    tools.fechar_data,
    tools.abrir_data,
    tools.bloquear_horario,
    tools.criar_servico,
    tools.editar_servico,
    tools.ver_agenda_completa,
]


# ---------------------------------------------------------------------------
# Prompt (mesmas regras do painel de antes)
# ---------------------------------------------------------------------------


def _remover_secao(texto: str, cabecalho: str) -> str:
    """Remove uma seção markdown (do cabeçalho até o próximo `## ` ou o fim)."""
    ini = texto.find(cabecalho)
    if ini == -1:
        return texto
    fim = texto.find("\n## ", ini + len(cabecalho))
    resto = texto[fim + 1 :] if fim != -1 else ""
    return (texto[:ini].rstrip() + "\n\n" + resto).strip()


def seed_prompt_geral(prompt_env: str) -> str:
    """Instrução geral inicial: env AGENT_SYSTEM_PROMPT (legado) ou padrão."""
    texto = (prompt_env or "").strip()
    if not texto:
        return PROMPT_GERAL_PADRAO
    for cabecalho in (_SECAO_MCP, _SECAO_FORMATACAO):
        texto = _remover_secao(texto, cabecalho)
    return texto or PROMPT_GERAL_PADRAO


def prompt_atual() -> tuple[str, str]:
    """(geral, mcp) — SQLite se já salvo pelo painel, senão seeds."""
    from .config import settings

    geral = db.get_prompt("geral")
    mcp = db.get_prompt("mcp")
    if geral is None:
        geral = seed_prompt_geral(settings.agent_system_prompt)
    if mcp is None:
        mcp = PROMPT_MCP_PADRAO
    return geral, mcp


def _system_prompt() -> str:
    cfg = db.get_config()
    try:
        tz = ZoneInfo(cfg.fuso)
    except Exception:
        tz = ZoneInfo("America/Sao_Paulo")
    agora = datetime.now(tz)
    prefixo = (
        f"Data e hora atuais ({cfg.fuso}): {agora.strftime('%Y-%m-%d %H:%M')} "
        f"({_DIAS_SEMANA[agora.weekday()]})."
    )
    geral, mcp = prompt_atual()
    partes = [prefixo, geral.strip()]
    if mcp.strip():
        partes.append(mcp.strip())
    return "\n\n".join(p for p in partes if p)


# Registrado como system prompt DINÂMICO: o pydantic-ai reavalia o part pelo
# dynamic_ref (= __qualname__) a cada run, mesmo com message_history.
_REF_PROMPT_DINAMICO = _system_prompt.__qualname__


# ---------------------------------------------------------------------------
# Memória (janela sem quebrar pares tool-call/return)
# ---------------------------------------------------------------------------


def _carregar_memoria(telefone: str) -> list[ModelMessage]:
    bruto = db.get_conversa(telefone)
    if not bruto:
        return []
    try:
        return ModelMessagesTypeAdapter.validate_json(bruto)
    except Exception:
        return []  # histórico de versão incompatível → recomeça


def _renovar_system_prompt(msgs: list[ModelMessage]) -> list[ModelMessage]:
    """Garante um único SystemPromptPart dinâmico no primeiro request.

    Sem isso o system prompt congela: com message_history o pydantic-ai NÃO
    injeta o prompt de novo — reusa o part gravado na 1ª mensagem da conversa
    (data/hora e edições do painel ficam presas no primeiro contato), e o
    corte da janela (_aparar) pode descartar o part por inteiro. Aqui os parts
    antigos saem e entra um placeholder com dynamic_ref, que o pydantic-ai
    substitui pelo _system_prompt() atual a cada run.
    """
    if not msgs:
        return msgs
    for m in msgs:
        if isinstance(m, ModelRequest):
            m.parts = [p for p in m.parts if not isinstance(p, SystemPromptPart)]
    primeiro = msgs[0]
    if isinstance(primeiro, ModelRequest):
        primeiro.parts = [
            SystemPromptPart(content="", dynamic_ref=_REF_PROMPT_DINAMICO),
            *primeiro.parts,
        ]
    return msgs


def _aparar(msgs: list[ModelMessage]) -> list[ModelMessage]:
    """Mantém as últimas JANELA_MEMORIA mensagens, cortando apenas em fronteira
    de turno do usuário (request com UserPromptPart) — cortar no meio de um
    par tool-call/tool-return quebraria a validação dos provedores."""
    if len(msgs) <= JANELA_MEMORIA:
        return msgs
    inicio = len(msgs) - JANELA_MEMORIA
    while inicio < len(msgs):
        m = msgs[inicio]
        if isinstance(m, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in m.parts
        ):
            break
        inicio += 1
    return msgs[inicio:]


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


async def responder(telefone: str, mensagem: str) -> str:
    """Roda o agente p/ uma mensagem do contato e persiste a memória.

    Pré-condição: `auth.solicitante_ctx` já setado pelo pipeline (whatsapp.py)
    com o remoteJid do remetente.
    """
    cfg = ia._config("texto")  # IANaoConfigurada se sem chave
    model = OpenAIChatModel(
        cfg.modelo or ia.MODELO_PADRAO["texto"],
        provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key),
    )
    agent = Agent(model, tools=_TOOLS, retries=2)
    agent.system_prompt(dynamic=True)(_system_prompt)

    historico = _renovar_system_prompt(_carregar_memoria(telefone))
    result = await agent.run(mensagem, message_history=historico)

    msgs = _aparar(list(result.all_messages()))
    db.set_conversa(telefone, ModelMessagesTypeAdapter.dump_json(msgs).decode())
    return result.output
