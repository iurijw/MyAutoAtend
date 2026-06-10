# CLAUDE.md — Memória do Projeto

## Visão Geral

Stack Docker para automação de WhatsApp com agente de IA. Tudo é provisionado automaticamente no primeiro `docker compose up -d`.

**Serviços:** Evolution API · PostgreSQL 15 · Redis · n8n · MCP Agendamentos (`mcp_agendamentos`)

---

## Arquivos Principais

| Arquivo | Função |
|---|---|
| `docker-compose.yml` | Orquestração de todos os serviços |
| `.env` | Todas as senhas e chaves (não commitar chaves reais) |
| `init-n8n.sh` | Script de boot do n8n: instala node, cria credenciais, importa e publica workflow, cria instância Evolution |
| `agente_whatsapp.json` | Workflow n8n do agente WhatsApp (exportado do n8n) |
| `myAutoAtendMCP/` | Servidor MCP de agendamentos (FastMCP + SQLite). Tools consumidas pelo nó "Agendamentos (MCP)" no workflow |

---

## O que o init-n8n.sh faz (ordem de execução)

1. Instala `n8n-nodes-evolution-api` via npm
2. Sobe n8n em background, aguarda `/healthz`
3. Se banco limpo → cria owner; senão → faz login (cookie de sessão)
4. Cria credenciais: **Evolution API**, **OpenAI**, **Redis** — captura os IDs reais retornados
5. Grava marker `/home/node/.n8n/.credentials_initialized` (idempotência)
6. Lê `agente_whatsapp.json`, substitui IDs hardcoded pelos IDs reais, importa via `POST /rest/workflows` e **publica** via `POST /rest/workflows/{id}/activate` com `{ versionId }`
7. Aguarda Evolution API, cria instância `evo_n8n` com webhook → `http://n8n:5678/webhook/whatsapp/receberMensagem`

---

## n8n v2.x — API interna (autenticação por cookie)

| Ação | Endpoint correto |
|---|---|
| Login | `POST /rest/login` |
| Criar credencial | `POST /rest/credentials` |
| Criar workflow | `POST /rest/workflows` |
| **Publicar workflow** | `POST /rest/workflows/{id}/activate` com body `{ "versionId": "..." }` |
| Atualizar workflow | `PATCH /rest/workflows/{id}` |

> **Atenção n8n v2:** `POST .../activate` exige `versionId` no body — sem ele retorna HTTP 400.
> `PATCH` com `{ active: true }` retorna 200 mas **não ativa** o workflow de fato.
> O `versionId` vem na resposta da criação do workflow (`body.data.versionId`).

---

## Credenciais hardcoded no agente_whatsapp.json (IDs do ambiente de origem)

Esses IDs são substituídos automaticamente pelo `init-n8n.sh` pelos IDs reais do novo ambiente:

| Tipo | Credencial | ID original |
|---|---|---|
| `evolutionApi` | Evolution API | `oCZwvvYltMxJIzmA` |
| `openAiApi` | **IA - Texto** (node LLM) | `MU6adeGic3RPMvdM` |
| `openAiApi` | **IA - Áudio** (Whisper) | `AUDIOcredID00001` |
| `openAiApi` | **IA - Imagem** (OCR/visão) | `IMAGEcredID00001` |
| `redis` | Redis | `trFJWRaDpUKn5nf8` |

> As três credenciais de IA são separadas de propósito: o painel `/admin` do MCP
> permite provedor diferente para cada uso (compatibilidade difere: áudio só
> OpenAI/custom; imagem aceita OpenRouter, Groq, Gemini etc.).

---

## Workflow Agente Whatsapp

- Webhook: `POST /webhook/whatsapp/receberMensagem`
- Suporta: texto, áudio (Whisper), imagem (GPT-4o OCR)
- Debounce de mensagens via Redis (aguarda 13s antes de processar)
- Memória de conversa por contato via Redis Chat Memory
- Modelo: GPT-5.1 (alterar no node "OpenAI - Modelo LLM")
- System prompt do agente: **editável pelo painel `/admin`** (card "Instruções do Agente", duas partes: instrução geral + bloco MCP avançado). O primeiro save substitui a referência `{{ $env.AGENT_SYSTEM_PROMPT }}` no node "Agente IA" por texto literal e republica o workflow — aplica na hora, sem recriar container. Antes do primeiro save, vale o `.env` (`AGENT_SYSTEM_PROMPT`), que também serve de seed do painel (valor em aspas duplas; `\n` viram quebras reais; não usar `"` nem `$` no texto). A data/hora atual é sempre injetada no início pelo node (prefixo fixo, fora do texto editável).
- Simula digitação proporcional ao tamanho da resposta

---

## Instância Evolution API

- Nome: `evo_n8n`
- Criada automaticamente pelo init se não existir
- Webhook configurado para `MESSAGES_UPSERT` com base64 ativado
- Após subir: escanear QR Code em `http://localhost:9090`

---

## Servidor MCP de Agendamentos (`myAutoAtendMCP/`)

- FastMCP (streamable-http) em `/mcp/` + painel `/admin` (HTTP Basic) no mesmo processo.
- Persistência **SQLite** (SQLModel), volume `mcp_data` → `/data/agendamentos.db`.
- Telefone normalizado para E.164 (`phonenumbers`); fuso aplicado; bloqueia agendar no passado.
- **Segurança:** o solicitante NÃO vem do modelo. O n8n injeta o remetente do webhook na URL
  do endpoint (`?solicitante=<remoteJid>`); middleware ASGI grava em contextvar e `auth.requester()`
  sempre prefere esse valor. Autorização (dono/próprio) decidida em `app/auth.py`, em código.
- No workflow: nó **"Agendamentos (MCP)"** (`mcpClientTool`, HTTP Streamable) → `Agente IA` (ai_tool).
- Env: `MCP_OWNER_PHONE`, `MCP_ADMIN_USER`, `MCP_ADMIN_PASS` (no `.env`).
- **Pareamento WhatsApp no painel** (`app/evolution.py`): o `/admin` fala direto com a
  Evolution API pela rede docker (`EVOLUTION_API_URL`/`EVOLUTION_API_KEY`/`EVOLUTION_INSTANCE`)
  e mostra o QR Code dentro do próprio painel. Rotas: `GET /admin/whatsapp/estado`,
  `GET /admin/whatsapp/qr`, `POST /admin/whatsapp/desconectar`.
- **Avatar do cliente nos agendamentos**: tabela do painel mostra foto de perfil do
  WhatsApp + número. JS busca `GET /admin/whatsapp/foto?numero=...` (uma vez por
  número único); backend usa `POST /chat/fetchProfilePictureUrl/{instance}` da
  Evolution (`evolution.foto_perfil`, timeout 5s, cache em memória: 1h com foto,
  5min vazio). Sem foto/privada/instância desconectada → fallback de inicial do nome.
- **Atalhos** no header do painel p/ n8n e Evolution manager (`N8N_EXTERNAL_URL`,
  `EVOLUTION_EXTERNAL_URL` — URLs do host, abrem em nova aba).
- **Provedores de IA no painel** (`app/n8n.py`): card "Provedores de IA" com 3 blocos
  (texto/áudio/imagem) atualiza chave/base URL das credenciais **IA - Texto**,
  **IA - Áudio** e **IA - Imagem** no n8n e troca o modelo dos nodes ("OpenAI -
  Modelo LLM" / "OpenAI - Descrever Imagem"), republicando o workflow. Áudio não tem
  modelo configurável (node fixa `whisper-1`) → só OpenAI/custom; imagem aceita
  OpenRouter/Groq/Gemini etc. (visão via chat completions). **Fluxo unidirecional**:
  a chave só ENTRA no n8n (PATCH `/rest/credentials/{id}`); nenhuma rota devolve
  segredo — o n8n redige `apiKey` na leitura, e o painel só lê a `url`
  (via `?includeData=true`) p/ deduzir o provedor atual. **Migração automática**:
  ambiente antigo (1 ou 2 credenciais) → ao salvar a chave de um alvo sem credencial
  própria, ela é criada e o node religado (`_religar_node`). Listagem de modelos sem
  expor chave: via n8n (`/rest/dynamic-node-parameters/resource-locator-results`,
  `modelSearch`) p/ chave salva, ou `GET {base_url}/models` transiente no preview
  (chave recém-digitada). Login no n8n com `N8N_OWNER_EMAIL`/`N8N_OWNER_PASSWORD`
  (envs `N8N_API_URL` etc. no compose); cookie cacheado (login tem rate limit 429).
  Rotas: `GET /admin/ia/estado`, `GET /admin/ia/modelos`, `POST /admin/ia/modelos-preview`,
  `POST /admin/ia/credencial`, `POST /admin/ia/modelo`.
- **Instruções do agente no painel** (`app/n8n.py` + tabela `Prompt` no SQLite): card
  "Instruções do Agente" edita o system prompt do node "Agente IA" em duas partes —
  **instrução geral** (livre) e **bloco MCP** (seção avançada retrátil, com aviso
  "não recomendado" e botão restaurar padrão). O bloco MCP (`PROMPT_MCP_PADRAO` em
  `n8n.py` — manter em sincronia com `app/tools.py`) inclui também a seção
  `## Formatação` (divisão em bolhas/[quebrar], amarrada ao node "Code - Dividir
  Resposta"). Save: n8n primeiro (PATCH no `systemMessage` + republicação), só então
  persiste no SQLite (chaves `geral`/`mcp`). Seed pré-primeiro-save: env
  `AGENT_SYSTEM_PROMPT` (repassada ao container MCP no compose) com as seções
  `## Ferramentas (MCP Agendamentos)` e `## Formatação` removidas + bloco MCP padrão.
  Prefixo de data/hora é fixo (`PREFIXO_DATA`); `{{` do usuário vira `{ {` p/ evitar
  injeção de expressão n8n. Rotas: `GET/POST /admin/agente/prompt`.
  (A antiga textarea "Instruções gerais" da Configuração geral e a tool MCP
  `instrucoes_gerais` foram removidas — coluna `instrucoes_gerais` fica órfã em
  bancos antigos, sem migração.)
- Após mudar essas envs/código: `docker compose up -d --build mcp-agendamentos`.

## Comandos Úteis

```bash
# Subir tudo
docker compose up -d

# Resetar SOMENTE o n8n (mantém Evolution, Postgres, Redis)
docker compose stop n8n && docker compose rm -f n8n && docker volume rm fast-n8n-evolutionapi-redis_n8n_data && docker compose up -d n8n

# Reforçar re-inicialização sem apagar dados
docker exec n8n rm -f /home/node/.n8n/.credentials_initialized && docker compose restart n8n

# Acompanhar logs de init
docker logs -f n8n

# Conferir estado de um workflow via API
curl -s -c /tmp/n8n_cookies.txt -X POST http://localhost:5678/rest/login \
  -H "Content-Type: application/json" \
  -d '{"emailOrLdapLoginId":"EMAIL","password":"SENHA"}' > /dev/null
curl -s -b /tmp/n8n_cookies.txt http://localhost:5678/rest/workflows/WORKFLOW_ID | \
  node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const w=(JSON.parse(d).data||JSON.parse(d));console.log('active:',w.active,'activeVersionId:',w.activeVersionId);})"
```

---

## Variáveis de Ambiente (.env)

```env
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
AUTHENTICATION_API_KEY=   # chave da Evolution API
N8N_OWNER_EMAIL=
N8N_OWNER_PASSWORD=
OPENAI_API_KEY=           # obrigatório para o agente funcionar
AGENT_SYSTEM_PROMPT=      # system prompt do agente (aspas duplas, \n = quebra; sem " nem $)
```

---

## Convenções de Commit

Usar tags convencionais: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
