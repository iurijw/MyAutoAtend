"""Painel web de configuração (/admin).

Autenticação: HTTP Basic, credenciais vindas de variáveis de ambiente
(ADMIN_USER / ADMIN_PASS). Controla parâmetros críticos (telefone do dono,
instruções, serviços) — NUNCA deixe o painel exposto sem credencial forte.

PARA EVOLUÇÃO FUTURA: trocar Basic por login de sessão com senha em hash
(passlib/bcrypt) e cookie seguro.
"""

import secrets
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import agente, db, evolution, ia
from .config import settings

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
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "config": db.get_config(),
            "servicos": servicos,
            "bloqueios": bloqueios,
            "agendamentos": agendamentos,
            "servico_nome": nome_por_id,
            "n_ativos": sum(1 for s in servicos if s.ativo),
            "horarios_por_dia": horarios_por_dia,
            "n_horarios": len(horarios),
            "evolution_url": settings.evolution_external_url,
            "provedores_ia": ia.PROVEDORES,
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
    mcp = db.get_prompt("mcp")
    return {
        "fonte": "painel" if geral is not None else "padrao",
        "geral": geral if geral is not None else agente.seed_prompt_geral(settings.agent_system_prompt),
        "mcp": mcp if mcp is not None else agente.PROMPT_MCP_PADRAO,
        "mcp_padrao": agente.PROMPT_MCP_PADRAO,
    }


@router.post("/admin/agente/prompt")
def agente_prompt_salvar(
    _: str = Depends(autenticar),
    geral: str = Form(...),
    mcp: str = Form(...),
):
    if not geral.strip():
        raise HTTPException(status_code=400, detail="A instrução geral não pode ficar vazia.")
    db.set_prompt("geral", geral.strip())
    db.set_prompt("mcp", mcp.strip())
    return {"ok": True}


@router.post("/admin/config")
def salvar_config(
    _: str = Depends(autenticar),
    telefone_dono: str = Form(...),
    fuso: str = Form(...),
):
    db.update_config(telefone_dono=telefone_dono, fuso=fuso)
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


@router.post("/admin/agendamento/{agendamento_id}/cancelar")
def cancelar_agendamento(agendamento_id: int, _: str = Depends(autenticar)):
    db.cancelar_agendamento(agendamento_id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/agendamento/{agendamento_id}/reagendar")
def reagendar_agendamento(
    agendamento_id: int,
    _: str = Depends(autenticar),
    novo_inicio: str = Form(...),
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
    dur = servico.duracao_min if servico else 30
    novo_fim = (dt_inicio + timedelta(minutes=dur)).isoformat(timespec="minutes")
    db.reagendar_agendamento(
        agendamento_id, dt_inicio.isoformat(timespec="minutes"), novo_fim
    )
    return RedirectResponse("/admin", status_code=303)


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
