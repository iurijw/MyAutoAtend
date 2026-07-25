"""Painel web de configuração (/admin).

Autenticação: HTTP Basic, credenciais vindas de variáveis de ambiente
(ADMIN_USER / ADMIN_PASS). Controla parâmetros críticos (telefone do dono,
instruções, serviços) — NUNCA deixe o painel exposto sem credencial forte.

PARA EVOLUÇÃO FUTURA: trocar Basic por login de sessão com senha em hash
(passlib/bcrypt) e cookie seguro.
"""

import re
import secrets
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import agente, db, evolution, ficha, ia, tarefas
from .config import settings
from .phone import formatar_internacional, mesmo_numero, normalizar
from .tools import _agora_local

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
security = HTTPBasic()


def autenticar(cred: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(cred.username, settings.admin_user)
    ok_pass = secrets.compare_digest(cred.password, settings.admin_pass)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return cred.username


def _fichas_clientes(agendamentos: list) -> list[dict]:
    """Agenda de contatos da seção Clientes: quem já falou com o bot (tabela
    Cliente) somado a quem só tem agendamento (cadastro manual antigo, feito
    antes do upsert). Nome do agendamento serve de reserva quando o contato
    ainda não trouxe pushName."""
    marcados: dict[str, int] = {}
    nome_do_agendamento: dict[str, str] = {}
    for a in agendamentos:
        tel = normalizar(a.telefone_cliente) or a.telefone_cliente
        marcados[tel] = marcados.get(tel, 0) + 1
        if a.nome_cliente and tel not in nome_do_agendamento:
            nome_do_agendamento[tel] = a.nome_cliente

    dono = db.get_config().telefone_dono
    preenchidos = db.preenchimento_fichas()
    fichas: dict[str, dict] = {}
    for c in db.listar_clientes():
        fichas[c.telefone] = {
            "telefone": c.telefone,
            "nome": c.nome or nome_do_agendamento.get(c.telefone, ""),
            "pausado": bool(c.bot_pausado),
        }
    for tel, nome in nome_do_agendamento.items():
        fichas.setdefault(tel, {"telefone": tel, "nome": nome, "pausado": False})

    lista = []
    for tel, f in fichas.items():
        f["telefone_fmt"] = formatar_internacional(tel) or tel
        f["agendamentos"] = marcados.get(tel, 0)
        f["dono"] = mesmo_numero(tel, dono)
        f["ficha_preenchidos"] = preenchidos.get(tel, 0)
        lista.append(f)
    # Com nome primeiro (ordem alfabética); os sem nome fecham a lista.
    lista.sort(key=lambda f: (not f["nome"], (f["nome"] or f["telefone"]).lower()))
    return lista


@router.get("/admin", response_class=HTMLResponse)
def painel(request: Request, _: str = Depends(autenticar)):
    servicos = db.listar_todos_servicos()
    nome_por_id = {s.id: s.nome for s in servicos}
    agendamentos = sorted(db.listar_agendamentos(), key=lambda a: a.inicio)
    bloqueios = sorted(db.listar_bloqueios(), key=lambda b: (b.data, b.inicio or ""))
    horarios = db.listar_horarios()
    horarios_por_dia: dict[int, list] = {d: [] for d in range(7)}
    for h in horarios:
        if 0 <= h.dia_semana <= 6:
            horarios_por_dia[h.dia_semana].append(h)
    clientes = _fichas_clientes(agendamentos)
    campos_ficha = db.listar_campos_ficha()
    instrucao_ficha = db.get_prompt("ficha")
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "config": db.get_config(),
            "servicos": servicos,
            "bloqueios": bloqueios,
            "agendamentos": agendamentos,
            "clientes": clientes,
            "servico_nome": nome_por_id,
            "n_ativos": sum(1 for s in servicos if s.ativo),
            "horarios_por_dia": horarios_por_dia,
            "n_horarios": len(horarios),
            "evolution_url": settings.evolution_external_url,
            "provedores_ia": ia.PROVEDORES,
            "campos_ficha": campos_ficha,
            "ficha_tipos": ficha.TIPOS,
            "ficha_total": sum(1 for c in campos_ficha if c.ativo),
            "ficha_instrucao": (
                instrucao_ficha
                if instrucao_ficha is not None
                else agente.PROMPT_FICHA_INSTRUCAO_PADRAO
            ),
        },
    )


# ---------------------------------------------------------------------------
# WhatsApp (pareamento via Evolution API) — consumido por JS no painel
# ---------------------------------------------------------------------------


@router.get("/admin/whatsapp/estado")
def whatsapp_estado(_: str = Depends(autenticar)):
    try:
        return evolution.estado()
    except Exception as e:  # noqa: BLE001 — superfície de erro p/ o painel
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/whatsapp/qr")
def whatsapp_qr(_: str = Depends(autenticar)):
    try:
        return evolution.conectar()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/whatsapp/desconectar")
def whatsapp_desconectar(_: str = Depends(autenticar)):
    try:
        return evolution.desconectar()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/whatsapp/foto")
def whatsapp_foto(numero: str, _: str = Depends(autenticar)):
    """Foto de perfil do WhatsApp de um número — avatar na lista de agendamentos."""
    try:
        return {"url": evolution.foto_perfil(numero)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/whatsapp/checar")
def whatsapp_checar(numero: str, _: str = Depends(autenticar)):
    """Confere se um número tem WhatsApp — cortesia dos campos de telefone.

    Devolve o número canônico do `jid` retornado pela Evolution (resolve o
    nono dígito: o jid é a forma que o WhatsApp reconhece) e a foto de perfil
    (tolerante a falha → None). Instância desconectada / erro na Evolution → 502
    (o front trata como checagem indisponível, sem barrar o cadastro)."""
    try:
        item = evolution.checar_numero(numero)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {
                "erro": "Não foi possível checar o número no WhatsApp. "
                "Confira se a conexão está ativa no card Conexão WhatsApp."
            },
            status_code=502,
        )
    existe = bool(item and item.get("exists"))
    jid = (item or {}).get("jid") or ""
    canon = normalizar(jid) or normalizar(numero)
    foto = None
    if existe and canon:
        try:
            foto = evolution.foto_perfil(canon)
        except Exception:  # noqa: BLE001 — foto é opcional
            foto = None
    return {
        "existe": existe,
        "numero": canon,
        "numero_fmt": formatar_internacional(canon or numero),
        "foto": foto,
    }


# ---------------------------------------------------------------------------
# Provedores de IA (config local no SQLite) — consumido por JS no painel.
# Fluxo unidirecional: a chave entra pelo form e é gravada via app/ia.py;
# nenhuma rota devolve segredo (nem mascarado).
# ---------------------------------------------------------------------------


@router.get("/admin/ia/estado")
def ia_estado(_: str = Depends(autenticar)):
    try:
        return ia.estado()
    except Exception as e:  # noqa: BLE001 — superfície de erro p/ o painel
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.get("/admin/ia/modelos")
def ia_modelos(alvo: str, _: str = Depends(autenticar)):
    if alvo not in ia.ALVOS_COM_MODELO:
        raise HTTPException(status_code=400, detail="Alvo inválido.")
    try:
        return {"modelos": ia.listar_modelos(alvo)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/ia/modelos-preview")
def ia_modelos_preview(
    _: str = Depends(autenticar),
    provedor: str = Form(...),
    api_key: str = Form(...),
    base_url: str = Form(""),
):
    """Lista modelos do provedor com a chave recém-digitada (antes de salvar).

    A chave é usada numa única chamada ao provedor e descartada — não é
    gravada nem ecoada na resposta.
    """
    preset = ia.PROVEDORES.get(provedor)
    if not preset:
        raise HTTPException(status_code=400, detail="Provedor desconhecido.")
    url = (preset["base_url"] or base_url).strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Provedor personalizado exige a base URL.")
    chave = api_key.strip()
    if not chave:
        raise HTTPException(status_code=400, detail="Informe a chave de API.")
    try:
        return {"modelos": ia.listar_modelos_do_provedor(url, chave)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/ia/credencial")
def ia_credencial(
    _: str = Depends(autenticar),
    alvo: str = Form(...),
    provedor: str = Form(...),
    api_key: str = Form(...),
    base_url: str = Form(""),
):
    if alvo not in ia.CRED_POR_ALVO:
        raise HTTPException(status_code=400, detail="Alvo inválido.")
    preset = ia.PROVEDORES.get(provedor)
    if not preset:
        raise HTTPException(status_code=400, detail="Provedor desconhecido.")
    if not preset[alvo]:
        raise HTTPException(
            status_code=400,
            detail=f"{preset['nome']} não suporta o uso '{alvo}' "
            "(áudio exige API compatível com Whisper/whisper-1).",
        )
    url = (preset["base_url"] or base_url).strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="Provedor personalizado exige a base URL.")
    chave = api_key.strip()
    if not chave:
        raise HTTPException(status_code=400, detail="Informe a chave de API.")
    try:
        return ia.atualizar_chave(alvo, chave, url)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


@router.post("/admin/ia/modelo")
def ia_modelo(
    _: str = Depends(autenticar),
    alvo: str = Form(...),
    modelo: str = Form(...),
):
    if alvo not in ia.ALVOS_COM_MODELO:
        raise HTTPException(
            status_code=400,
            detail="Alvo inválido (áudio tem modelo fixo whisper-1).",
        )
    if not modelo.strip():
        raise HTTPException(status_code=400, detail="Informe o nome do modelo.")
    try:
        return ia.atualizar_modelo(alvo, modelo.strip())
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"erro": str(e)}, status_code=502)


# ---------------------------------------------------------------------------
# Instruções do agente (system prompt). Fonte de verdade: SQLite (tabela
# Prompt), lida pelo agente A CADA mensagem — salvar aqui aplica na hora.
# Antes do primeiro save, o painel mostra os padrões de app/agente.py.
# ---------------------------------------------------------------------------


@router.get("/admin/agente/prompt")
def agente_prompt(_: str = Depends(autenticar)):
    geral = db.get_prompt("geral")
    mcp_dono = db.get_prompt("mcp_dono")
    mcp_cliente = db.get_prompt("mcp_cliente")
    return {
        "fonte": "painel" if geral is not None else "padrao",
        "geral": geral if geral is not None else agente.seed_prompt_geral(settings.agent_system_prompt),
        "mcp_dono": mcp_dono if mcp_dono is not None else agente.PROMPT_MCP_DONO_PADRAO,
        "mcp_cliente": mcp_cliente if mcp_cliente is not None else agente.PROMPT_MCP_CLIENTE_PADRAO,
        "mcp_dono_padrao": agente.PROMPT_MCP_DONO_PADRAO,
        "mcp_cliente_padrao": agente.PROMPT_MCP_CLIENTE_PADRAO,
    }


@router.post("/admin/agente/prompt")
def agente_prompt_salvar(
    _: str = Depends(autenticar),
    geral: str = Form(...),
    mcp_dono: str = Form(...),
    mcp_cliente: str = Form(...),
):
    if not geral.strip():
        raise HTTPException(status_code=400, detail="A instrução geral não pode ficar vazia.")
    db.set_prompt("geral", geral.strip())
    db.set_prompt("mcp_dono", mcp_dono.strip())
    db.set_prompt("mcp_cliente", mcp_cliente.strip())
    return {"ok": True}


@router.post("/admin/config")
def salvar_config(
    _: str = Depends(autenticar),
    telefone_dono: str = Form(...),
    fuso: str = Form(...),
    avisar_dono: bool = Form(False),  # checkbox desmarcado não envia o campo
):
    db.update_config(telefone_dono=telefone_dono, fuso=fuso, avisar_dono=avisar_dono)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Horários de funcionamento (grade semanal). O form do card envia a grade
# inteira como listas paralelas (dia / inicio / fim, uma trinca por intervalo)
# e a gravação é um replace-all — o que está na tela vira a verdade.
# ---------------------------------------------------------------------------


@router.post("/admin/horarios")
def salvar_horarios(
    _: str = Depends(autenticar),
    dia: list[int] = Form([]),
    inicio: list[str] = Form([]),
    fim: list[str] = Form([]),
):
    if not (len(dia) == len(inicio) == len(fim)):
        raise HTTPException(status_code=400, detail="Linhas de horário inconsistentes.")

    intervalos: list[tuple[int, str, str]] = []
    for d, ini, f in zip(dia, inicio, fim):
        if not 0 <= d <= 6:
            raise HTTPException(status_code=400, detail="Dia da semana inválido.")
        try:
            time.fromisoformat(ini), time.fromisoformat(f)
        except ValueError:
            raise HTTPException(status_code=400, detail="Horário inválido (use HH:MM).")
        if f <= ini:  # "HH:MM" zero-padded → comparação lexicográfica vale
            raise HTTPException(
                status_code=400,
                detail=f"Intervalo termina antes de começar ({ini}–{f}).",
            )
        intervalos.append((d, ini, f))

    por_dia: dict[int, list[tuple[str, str]]] = {}
    for d, ini, f in intervalos:
        por_dia.setdefault(d, []).append((ini, f))
    for faixas in por_dia.values():
        faixas.sort()
        for (_i1, f1), (i2, _f2) in zip(faixas, faixas[1:]):
            if i2 < f1:
                raise HTTPException(
                    status_code=400,
                    detail=f"Intervalos sobrepostos no mesmo dia ({f1} × {i2}).",
                )

    db.substituir_horarios(intervalos)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/horarios/restaurar")
def restaurar_horarios(_: str = Depends(autenticar)):
    """Volta ao padrão: segunda a sexta, 08:00–12:00 e 13:30–18:00."""
    db.restaurar_horarios_padrao()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/horarios/limpar")
def limpar_horarios(_: str = Depends(autenticar)):
    """Apaga a grade inteira — todos os dias ficam sem expediente."""
    db.limpar_horarios()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/servico")
def novo_servico(
    _: str = Depends(autenticar),
    nome: str = Form(...),
    descricao: str = Form(...),
    valor: float = Form(...),
    duracao_min: int = Form(...),
):
    db.criar_servico(nome, descricao, valor, duracao_min)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/servico/{servico_id}/toggle")
def alternar_servico(servico_id: int, _: str = Depends(autenticar)):
    srv = db.get_servico(servico_id)
    if srv:
        db.editar_servico(servico_id, ativo=not srv.ativo)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/servico/{servico_id}/excluir")
def excluir_servico(servico_id: int, _: str = Depends(autenticar)):
    db.deletar_servico(servico_id)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Agendamentos
# ---------------------------------------------------------------------------


@router.get("/admin/agenda/slots")
def agenda_slots(data: str, servico_id: int, _: str = Depends(autenticar)):
    """Grade de horários de um dia para um serviço (seletor visual do painel).

    Mesma lógica da tool `consultar_horarios_disponiveis`: varre os intervalos
    de funcionamento do dia da semana com passo = duração do serviço.
    `estado` = "ocupado" quando o slot colide com agendamento ativo ou bloqueio
    (via `db.horario_disponivel`); no dia de hoje, horários que já passaram
    também entram como "ocupado". Dias futuros não filtram passado.
    `fechado` = True quando o dia não tem expediente na grade."""
    servico = db.get_servico(servico_id)
    if not servico:
        raise HTTPException(status_code=404, detail="Serviço não encontrado.")
    try:
        dia = date.fromisoformat(data)
    except ValueError:
        raise HTTPException(status_code=400, detail="Data inválida. Use YYYY-MM-DD.")

    intervalos = db.horarios_do_dia(dia.weekday())
    if not intervalos:
        return {"slots": [], "fechado": True}

    agora = _agora_local()
    passo = timedelta(minutes=servico.duracao_min)
    slots: list[dict] = []
    for janela in intervalos:
        atual = datetime.fromisoformat(f"{data}T{janela.inicio}")
        limite = datetime.fromisoformat(f"{data}T{janela.fim}")
        while atual + passo <= limite:
            ini = atual.isoformat(timespec="minutes")
            fim = (atual + passo).isoformat(timespec="minutes")
            livre = atual >= agora and db.horario_disponivel(ini, fim)
            slots.append(
                {
                    "inicio": atual.strftime("%H:%M"),
                    "estado": "livre" if livre else "ocupado",
                }
            )
            atual += passo
    return {"slots": slots, "fechado": False}


@router.post("/admin/agendamento")
def novo_agendamento(
    _: str = Depends(autenticar),
    servico_id: int = Form(...),
    nome_cliente: str = Form(...),
    telefone_cliente: str = Form(...),
    inicio: str = Form(...),
    observacoes: str = Form(""),
):
    """Cadastro manual pelo painel. O dono pode marcar fora do horário de
    funcionamento (override consciente, como no reagendar do painel) — só
    conflito com agendamento/bloqueio é recusado."""
    servico = db.get_servico(servico_id)
    if not servico:
        raise HTTPException(status_code=404, detail="Serviço não encontrado.")
    nome = nome_cliente.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do cliente.")
    tel = telefone_cliente.strip()
    if not tel:
        raise HTTPException(status_code=400, detail="Informe o telefone do cliente.")
    try:
        dt_inicio = datetime.fromisoformat(inicio)
    except ValueError:
        raise HTTPException(status_code=400, detail="Horário inválido.")
    fim = (dt_inicio + timedelta(minutes=servico.duracao_min)).isoformat(
        timespec="minutes"
    )
    ag = db.criar_agendamento(
        servico_id=servico_id,
        telefone_cliente=normalizar(tel) or tel,
        nome_cliente=nome,
        inicio=dt_inicio.isoformat(timespec="minutes"),
        fim=fim,
        observacoes=observacoes.strip(),
    )
    if not ag:
        raise HTTPException(
            status_code=409,
            detail="Horário indisponível (conflita com agendamento ou bloqueio).",
        )
    # O contato entra na agenda de clientes já no cadastro manual — sem isso ele
    # só apareceria depois de mandar a primeira mensagem no WhatsApp.
    db.upsert_cliente(normalizar(tel) or tel, nome)
    return RedirectResponse("/admin", status_code=303)


def _avisar_cliente_permitido(ag: db.Agendamento) -> bool:
    """Aviso proativo só faz sentido para agendamento de terceiro — o painel
    É o dono, avisar o próprio número viraria o bot falando com o dono."""
    return not mesmo_numero(ag.telefone_cliente, db.get_config().telefone_dono)


@router.post("/admin/agendamento/{agendamento_id}/cancelar")
def cancelar_agendamento(
    agendamento_id: int,
    _: str = Depends(autenticar),
    avisar_cliente: str = Form(""),
):
    ag = db.get_agendamento(agendamento_id)  # dados p/ o aviso, antes de cancelar
    cancelou = db.cancelar_agendamento(agendamento_id)
    if cancelou and avisar_cliente and ag and _avisar_cliente_permitido(ag):
        db.criar_aviso_cliente(
            ag, "cancelado", _agora_local().isoformat(timespec="minutes")
        )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/agendamento/{agendamento_id}/reagendar")
def reagendar_agendamento(
    agendamento_id: int,
    _: str = Depends(autenticar),
    novo_inicio: str = Form(...),
    avisar_cliente: str = Form(""),
):
    """Remarca um agendamento. `novo_inicio` no formato YYYY-MM-DDTHH:MM.

    O dono opera pelo painel (já autenticado por Basic), então não passa pela
    autorização das tools MCP. O fim é recalculado pela duração do serviço.
    """
    ag = db.get_agendamento(agendamento_id)
    if not ag:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado.")
    servico = db.get_servico(ag.servico_id)
    try:
        dt_inicio = datetime.fromisoformat(novo_inicio)
    except ValueError:
        raise HTTPException(status_code=400, detail="Horário inválido.")
    inicio_anterior = ag.inicio
    dur = servico.duracao_min if servico else 30
    novo_fim = (dt_inicio + timedelta(minutes=dur)).isoformat(timespec="minutes")
    ok = db.reagendar_agendamento(
        agendamento_id, dt_inicio.isoformat(timespec="minutes"), novo_fim
    )
    if ok and avisar_cliente and _avisar_cliente_permitido(ag):
        db.criar_aviso_cliente(
            db.get_agendamento(agendamento_id),
            "reagendado",
            _agora_local().isoformat(timespec="minutes"),
            inicio_anterior=inicio_anterior,
        )
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Conversas (histórico do bot por contato + pausa + envio manual) — consumido
# por JS no painel. A memória (tabela Conversa) é indexada pelo remoteJid; a
# ficha do contato (tabela Cliente: nome + pausa) pelo E.164 normalizado. O
# identificador que trafega com o front é o telefone normalizado; a chave da
# memória é reencontrada por `db.resolver_chave_conversa`.
# ---------------------------------------------------------------------------


def _resumo_conversas() -> list[dict]:
    """Lista de conversas p/ o card: uma linha por contato, mais recente no
    topo. Junta a memória (última mensagem, timestamp) com a ficha do contato
    (nome, pausa). Inclui também contatos sem conversa ainda (ex.: pausados
    manualmente) para que a pausa fique visível e reversível."""
    clientes = {c.telefone: c for c in db.listar_clientes()}
    itens: list[dict] = []
    vistos: set[str] = set()
    for conv in db.listar_conversas():
        norm = normalizar(conv.telefone) or conv.telefone
        vistos.add(norm)
        bolhas = agente.historico_para_bolhas(conv.historico)
        ultima = bolhas[-1] if bolhas else None
        cli = clientes.get(norm)
        itens.append(
            {
                "telefone": norm,
                "nome": cli.nome if cli and cli.nome else "",
                "pausado": bool(cli and cli.bot_pausado),
                "preview": (ultima["texto"][:90] if ultima else ""),
                "quem": ultima["quem"] if ultima else "",
                "hora": ultima["hora"] if ultima else "",
                "_ordem": conv.atualizado_em or "",
            }
        )
    for tel, cli in clientes.items():
        if tel in vistos:
            continue
        itens.append(
            {
                "telefone": tel,
                "nome": cli.nome or "",
                "pausado": bool(cli.bot_pausado),
                "preview": "",
                "quem": "",
                "hora": "",
                "_ordem": "",
            }
        )
    itens.sort(key=lambda x: x["_ordem"], reverse=True)
    for it in itens:
        it.pop("_ordem", None)
    return itens


@router.get("/admin/conversas")
def listar_conversas(_: str = Depends(autenticar)):
    return {"conversas": _resumo_conversas()}


@router.get("/admin/conversas/{telefone}")
def conversa_detalhe(telefone: str, _: str = Depends(autenticar)):
    norm = normalizar(telefone) or telefone
    cli = db.get_cliente(norm)
    bruto = db.get_conversa(db.resolver_chave_conversa(telefone))
    return {
        "telefone": norm,
        "nome": cli.nome if cli and cli.nome else "",
        "pausado": bool(cli and cli.bot_pausado),
        "mensagens": agente.historico_para_bolhas(bruto),
    }


@router.post("/admin/conversas/{telefone}/pausa")
def conversa_pausa(
    telefone: str,
    _: str = Depends(autenticar),
    pausar: bool = Form(...),
):
    if mesmo_numero(telefone, db.get_config().telefone_dono):
        raise HTTPException(
            status_code=400,
            detail="O dono não pode ser pausado — é a interface de gestão.",
        )
    c = db.set_pausa_cliente(telefone, pausar)
    return {"ok": True, "telefone": c.telefone, "bot_pausado": c.bot_pausado}


@router.post("/admin/conversas/{telefone}/enviar")
async def conversa_enviar(
    telefone: str,
    _: str = Depends(autenticar),
    texto: str = Form(...),
):
    """Envia UMA mensagem manual pelo WhatsApp (sem IA) e só grava na memória
    como fala do bot APÓS o envio dar certo. Falha da Evolution → 502 (o caso
    comum é o WhatsApp não estar pareado)."""
    msg = texto.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Escreva uma mensagem antes de enviar.")
    numero = re.sub(r"\D", "", telefone)
    if not numero:
        raise HTTPException(status_code=400, detail="Telefone inválido.")
    # Digitação curta e proporcional (bem menor que o pipeline reativo).
    digitando_ms = int(min(0.3 + len(msg) * 0.012, 1.8) * 1000)
    try:
        # Timeout curto: sem WhatsApp pareado o sendText trava — o painel
        # precisa cair no 502 rápido para o toast aparecer.
        await evolution.enviar_texto(numero, msg, digitando_ms=digitando_ms, timeout=8.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail="Não foi possível enviar pelo WhatsApp. Confira se o número "
            "está conectado no card Conexão WhatsApp.",
        ) from e
    agente.registrar_na_memoria(db.resolver_chave_conversa(telefone), msg, "bot")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Ficha de cadastro (feature opcional). O dono define os campos aqui; o agente
# preenche durante a conversa (tools ver_ficha / preencher_ficha) e o painel
# preenche pelo modal. Validação de valor mora em app/ficha.py — mesma para
# os dois caminhos.
# ---------------------------------------------------------------------------


@router.post("/admin/ficha/ajustes")
def ficha_ajustes(
    _: str = Depends(autenticar),
    ativa: bool = Form(False),  # checkbox desmarcado não envia o campo
    instrucao: str = Form(""),
):
    db.update_config(ficha_ativa=ativa)
    db.set_prompt("ficha", instrucao.strip())
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/ficha/campo")
def ficha_novo_campo(
    _: str = Depends(autenticar),
    rotulo: str = Form(...),
    tipo: str = Form(...),
    opcoes: str = Form(""),
    descricao: str = Form(""),
    obrigatorio: bool = Form(False),
):
    nome = rotulo.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Informe o nome do campo.")
    if tipo not in ficha.TIPOS:
        raise HTTPException(status_code=400, detail="Tipo de campo inválido.")
    alternativas = [o.strip() for o in opcoes.replace("\n", ";").split(";") if o.strip()]
    if tipo == "selecao" and len(alternativas) < 2:
        raise HTTPException(
            status_code=400,
            detail="Campo de seleção precisa de pelo menos duas opções "
            "(separe por ponto e vírgula).",
        )
    db.criar_campo_ficha(
        chave=ficha.chave_livre(nome),
        rotulo=nome,
        tipo=tipo,
        opcoes=";".join(alternativas),
        descricao=descricao.strip(),
        obrigatorio=obrigatorio,
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/ficha/campo/{campo_id}/toggle")
def ficha_alternar_campo(campo_id: int, _: str = Depends(autenticar)):
    campo = db.get_campo_ficha(campo_id)
    if not campo:
        raise HTTPException(status_code=404, detail="Campo não encontrado.")
    db.editar_campo_ficha(campo_id, ativo=not campo.ativo)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/ficha/campo/{campo_id}/excluir")
def ficha_excluir_campo(campo_id: int, _: str = Depends(autenticar)):
    """Apaga o campo e TODOS os valores já preenchidos dele."""
    if not db.deletar_campo_ficha(campo_id):
        raise HTTPException(status_code=404, detail="Campo não encontrado.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/ficha/campo/{campo_id}/mover")
def ficha_mover_campo(
    campo_id: int, _: str = Depends(autenticar), direcao: str = Form(...)
):
    if direcao not in ("cima", "baixo"):
        raise HTTPException(status_code=400, detail="Direção inválida.")
    db.mover_campo_ficha(campo_id, para_cima=direcao == "cima")
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/ficha/cliente/{telefone}")
def ficha_do_cliente(telefone: str, _: str = Depends(autenticar)):
    """Ficha de um contato para o modal (mesma montagem que o agente enxerga)."""
    dados = ficha.ficha_de(telefone)
    dados["ativa"] = ficha.ativa()
    return dados


@router.post("/admin/ficha/cliente/{telefone}")
async def ficha_salvar_cliente(
    request: Request, telefone: str, _: str = Depends(autenticar)
):
    """Salva a ficha pelo painel. Campos chegam como `campo_<chave>`; valor
    fora do formato do tipo volta em `erros` (o modal marca o campo)."""
    form = await request.form()
    dados = {
        k[len("campo_"):]: str(v)
        for k, v in form.items()
        if k.startswith("campo_")
    }
    if not dados:
        raise HTTPException(status_code=400, detail="Nenhum campo enviado.")
    resultado = ficha.preencher(telefone, dados, origem="painel")
    if resultado["erros"]:
        return JSONResponse(
            {"ok": False, "erros": resultado["erros"]}, status_code=400
        )
    return {"ok": True, **ficha.ficha_de(telefone)}


# ---------------------------------------------------------------------------
# Proatividade (fila de tarefas do bot) — consumido por JS no painel (poll)
# ---------------------------------------------------------------------------


@router.get("/admin/tarefas/estado")
def tarefas_estado(_: str = Depends(autenticar)):
    """Fila de ações proativas para o card 'Proatividade Pendente'."""
    return {"tarefas": [tarefas.descrever_tarefa(t) for t in db.listar_tarefas_painel()]}


@router.post("/admin/tarefas/{tarefa_id}/cancelar")
def tarefa_cancelar(tarefa_id: int, _: str = Depends(autenticar)):
    if not db.cancelar_tarefa(tarefa_id):
        raise HTTPException(
            status_code=409, detail="Tarefa não está mais pendente na fila."
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Bloqueios
# ---------------------------------------------------------------------------


@router.post("/admin/bloqueio")
def novo_bloqueio(
    _: str = Depends(autenticar),
    data: str = Form(...),
    data_fim: str = Form(""),
    inicio: str = Form(""),
    fim: str = Form(""),
    motivo: str = Form(""),
):
    if data_fim and data_fim < data:
        raise HTTPException(status_code=400, detail="Data final anterior à inicial.")
    db.criar_bloqueio(
        data=data,
        inicio=inicio or None,
        fim=fim or None,
        motivo=motivo,
        data_fim=data_fim or None,
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/bloqueio/{bloqueio_id}/excluir")
def excluir_bloqueio(bloqueio_id: int, _: str = Depends(autenticar)):
    db.remover_bloqueio(bloqueio_id)
    return RedirectResponse("/admin", status_code=303)
