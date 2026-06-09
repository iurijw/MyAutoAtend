"""Cliente da API interna do n8n — gestão UNIDIRECIONAL dos provedores de IA.

O painel /admin grava a chave/base URL dos provedores direto nas credenciais
do n8n (`PATCH /rest/credentials/{id}`) e troca o modelo nos nodes do workflow.
A chave nunca faz o caminho de volta: este módulo só escreve segredos, e a
própria API do n8n redige campos sensíveis na leitura (`includeData` devolve
o `apiKey` como sentinela em branco — usamos isso apenas para ler a `url`,
que não é segredo, e deduzir o provedor atual).

Autenticação igual à do init-n8n.sh: login de owner via `POST /rest/login`
(cookie de sessão). Login é refeito a cada operação — uso do painel é raro.

Envs (injetadas pelo compose): N8N_API_URL · N8N_OWNER_EMAIL · N8N_OWNER_PASSWORD.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import httpx

from .config import settings

# Nomes criados pelo init-n8n.sh. Fallback p/ ambientes antigos com 1 credencial só.
CRED_POR_ALVO = {"texto": "IA - Texto", "midia": "IA - Mídia"}
CRED_LEGADA = "OpenAi account"

WORKFLOW_NAME = "Agente Whatsapp"
# Nodes cujo modelo é editável. Áudio (Whisper) não expõe modelo no node do n8n
# (fixo em whisper-1) — por isso "mídia" só aceita provedores compatíveis com ele.
NODE_POR_ALVO = {"texto": "OpenAI - Modelo LLM", "midia": "OpenAI - Descrever Imagem"}

# Provedores com API compatível OpenAI. `midia: False` = não serve para a
# credencial de mídia porque o node de áudio do n8n manda model=whisper-1 fixo.
PROVEDORES: dict[str, dict[str, Any]] = {
    "openai": {"nome": "OpenAI", "base_url": "https://api.openai.com/v1", "texto": True, "midia": True},
    "groq": {"nome": "Groq", "base_url": "https://api.groq.com/openai/v1", "texto": True, "midia": False},
    "openrouter": {"nome": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "texto": True, "midia": False},
    "mistral": {"nome": "Mistral", "base_url": "https://api.mistral.ai/v1", "texto": True, "midia": False},
    "xai": {"nome": "xAI (Grok)", "base_url": "https://api.x.ai/v1", "texto": True, "midia": False},
    "gemini": {"nome": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "texto": True, "midia": False},
    "deepseek": {"nome": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "texto": True, "midia": False},
    "custom": {"nome": "Personalizado (URL própria)", "base_url": "", "texto": True, "midia": True},
}


def _data(r: httpx.Response) -> Any:
    """n8n embrulha respostas em {"data": ...} — desembrulha quando presente."""
    j = r.json()
    return j.get("data", j) if isinstance(j, dict) else j


# Cookie de sessão cacheado entre chamadas: o POST /rest/login do n8n tem rate
# limit (429 com logins repetidos) e o painel faz várias chamadas em sequência.
_cookie_cache: str | None = None


@contextmanager
def _sessao() -> Iterator[httpx.Client]:
    """Abre cliente já logado no n8n.

    O cookie `n8n-auth` vem com flag `Secure`, e o tráfego interno é http://,
    então o jar do httpx o descartaria — copiamos o header na mão, como o
    init-n8n.sh faz. Cookie reutilizado enquanto válido (GET /rest/login é o
    "whoami" do n8n); refeito o login quando expira.
    """
    global _cookie_cache
    with httpx.Client(base_url=settings.n8n_api_url.rstrip("/"), timeout=30.0) as c:
        if _cookie_cache:
            c.headers["Cookie"] = _cookie_cache
            if c.get("/rest/login").status_code == 200:
                yield c
                return
            del c.headers["Cookie"]
            _cookie_cache = None

        r = c.post(
            "/rest/login",
            json={
                "emailOrLdapLoginId": settings.n8n_owner_email,
                "password": settings.n8n_owner_password,
            },
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Login no n8n falhou (HTTP {r.status_code}). "
                "Confira N8N_OWNER_EMAIL/N8N_OWNER_PASSWORD no .env."
            )
        auth = next(
            (
                v.split(";")[0].strip()
                for v in r.headers.get_list("set-cookie")
                if "n8n-auth=" in v
            ),
            None,
        )
        if not auth:
            raise RuntimeError("Login no n8n não retornou cookie de sessão.")
        _cookie_cache = auth
        c.headers["Cookie"] = auth
        yield c


# ---------------------------------------------------------------------------
# Credenciais
# ---------------------------------------------------------------------------


def _achar_credencial(c: httpx.Client, alvo: str) -> dict:
    r = c.get("/rest/credentials")
    r.raise_for_status()
    creds = _data(r)
    if isinstance(creds, dict):  # algumas versões paginam: {count, data}
        creds = creds.get("data", [])
    nome = CRED_POR_ALVO[alvo]
    openai_creds = [x for x in creds if x.get("type") == "openAiApi"]
    for x in openai_creds:
        if x.get("name") == nome:
            return x
    # Ambiente antigo (uma credencial única) — usa a que existir.
    for x in openai_creds:
        if x.get("name") == CRED_LEGADA:
            return x
    if len(openai_creds) == 1:
        return openai_creds[0]
    raise RuntimeError(
        f'Credencial "{nome}" não encontrada no n8n. '
        "Refaça o init (remova o marker .credentials_initialized) ou crie-a manualmente."
    )


def atualizar_chave(alvo: str, api_key: str, base_url: str) -> dict:
    """Grava chave + base URL na credencial do alvo. Não retorna segredo algum."""
    with _sessao() as c:
        cred = _achar_credencial(c, alvo)
        r = c.patch(
            f"/rest/credentials/{cred['id']}",
            json={
                "name": cred["name"],
                "type": cred["type"],
                "data": {"apiKey": api_key, "url": base_url},
            },
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"n8n recusou a atualização (HTTP {r.status_code}): {r.text[:200]}")
        return {"ok": True, "credencial": cred["name"]}


def _url_da_credencial(c: httpx.Client, cred_id: str) -> str | None:
    """Lê só a `url` (não-segredo) da credencial; apiKey volta redigido pelo n8n."""
    try:
        r = c.get(f"/rest/credentials/{cred_id}", params={"includeData": "true"})
        if r.status_code == 200:
            return (_data(r).get("data") or {}).get("url")
    except Exception:
        pass
    return None


def _provedor_da_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.rstrip("/")
    for chave, p in PROVEDORES.items():
        if p["base_url"] and p["base_url"].rstrip("/") == u:
            return chave
    return "custom"


def listar_modelos(alvo: str) -> list[dict]:
    """Modelos disponíveis no provedor do alvo, SEM expor a chave.

    Usa o endpoint interno do n8n que resolve `listSearch` de nodes
    (`modelSearch` chama o `GET /models` do provedor com a credencial
    armazenada). A chave fica do lado do n8n o tempo todo.
    """
    with _sessao() as c:
        cred = _achar_credencial(c, alvo)
        r = c.post(
            "/rest/dynamic-node-parameters/resource-locator-results",
            json={
                "nodeTypeAndVersion": {
                    "name": "@n8n/n8n-nodes-langchain.openAi",
                    "version": 1.8,
                },
                "path": "parameters.modelId",
                "methodName": "modelSearch",
                "currentNodeParameters": {"resource": "image", "operation": "analyze"},
                "credentials": {"openAiApi": {"id": cred["id"], "name": cred["name"]}},
            },
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"n8n não conseguiu listar os modelos (HTTP {r.status_code}). "
                "Confira se a chave do provedor é válida."
            )
        resultados = (_data(r) or {}).get("results", [])
        return [{"valor": x["value"]} for x in resultados if x.get("value")]


def listar_modelos_do_provedor(base_url: str, api_key: str) -> list[dict]:
    """Lista modelos direto no provedor, com chave recém-digitada no painel.

    Uso transiente (preview antes de salvar): a chave acabou de ser digitada
    pelo usuário, é usada numa única chamada `GET /models` e descartada —
    não é armazenada nem aparece em resposta alguma. Depois de salva, a
    listagem passa a ser feita via n8n (`listar_modelos`).
    """
    r = httpx.get(
        base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20.0,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Provedor respondeu HTTP {r.status_code} ao listar modelos — confira a chave."
        )
    data = r.json().get("data", [])
    modelos = sorted(m["id"] for m in data if m.get("id"))
    return [{"valor": m} for m in modelos]


# ---------------------------------------------------------------------------
# Workflow (modelo dos nodes)
# ---------------------------------------------------------------------------


def _achar_workflow(c: httpx.Client) -> dict:
    r = c.get("/rest/workflows")
    r.raise_for_status()
    wfs = _data(r)
    if isinstance(wfs, dict):
        wfs = wfs.get("data", [])
    for w in wfs:
        if w.get("name") == WORKFLOW_NAME:
            rr = c.get(f"/rest/workflows/{w['id']}")
            rr.raise_for_status()
            return _data(rr)
    raise RuntimeError(f'Workflow "{WORKFLOW_NAME}" não encontrado no n8n.')


def _modelo_do_node(node: dict) -> str | None:
    params = node.get("parameters", {})
    if isinstance(params.get("model"), str):  # lmChatOpenAi
        return params["model"]
    model_id = params.get("modelId")  # node openAi (resource locator)
    if isinstance(model_id, dict):
        return model_id.get("value")
    return None


def atualizar_modelo(alvo: str, modelo: str) -> dict:
    """Troca o modelo do node do alvo e republica o workflow (regra do versionId)."""
    with _sessao() as c:
        wf = _achar_workflow(c)
        node_nome = NODE_POR_ALVO[alvo]
        node = next((n for n in wf["nodes"] if n["name"] == node_nome), None)
        if node is None:
            raise RuntimeError(f'Node "{node_nome}" não encontrado no workflow.')

        if "model" in node.get("parameters", {}):
            node["parameters"]["model"] = modelo
        else:
            node["parameters"]["modelId"] = {"__rl": True, "value": modelo, "mode": "id"}

        estava_ativo = bool(wf.get("active"))
        r = c.patch(
            f"/rest/workflows/{wf['id']}",
            json={
                "name": wf["name"],
                "nodes": wf["nodes"],
                "connections": wf["connections"],
                "settings": wf.get("settings") or {},
                "versionId": wf["versionId"],
            },
        )
        if r.status_code != 200:
            raise RuntimeError(f"n8n recusou o PATCH do workflow (HTTP {r.status_code}): {r.text[:200]}")
        novo = _data(r)

        # n8n v2: PATCH não (re)ativa — se caiu, republica com o versionId novo.
        if estava_ativo and not novo.get("active"):
            ar = c.post(
                f"/rest/workflows/{wf['id']}/activate",
                json={"versionId": novo["versionId"]},
            )
            if ar.status_code != 200:
                raise RuntimeError(
                    f"Modelo salvo, mas falhou ao republicar o workflow (HTTP {ar.status_code}). "
                    "Publique manualmente no n8n."
                )
        return {"ok": True, "modelo": modelo}


# ---------------------------------------------------------------------------
# Estado para o painel (nunca inclui segredos)
# ---------------------------------------------------------------------------


def estado() -> dict:
    """Snapshot p/ o painel: provedor (deduzido da URL), modelo e última atualização."""
    out: dict[str, Any] = {}
    with _sessao() as c:
        wf = None
        try:
            wf = _achar_workflow(c)
        except Exception:
            pass
        for alvo in ("texto", "midia"):
            info: dict[str, Any] = {"provedor": None, "modelo": None, "atualizado_em": None}
            try:
                cred = _achar_credencial(c, alvo)
                info["atualizado_em"] = cred.get("updatedAt")
                info["provedor"] = _provedor_da_url(_url_da_credencial(c, cred["id"]))
            except Exception as e:
                info["erro"] = str(e)
            if wf:
                node = next(
                    (n for n in wf["nodes"] if n["name"] == NODE_POR_ALVO[alvo]), None
                )
                if node:
                    info["modelo"] = _modelo_do_node(node)
            out[alvo] = info
    return out
