# Gerenciador de Agendamentos — Servidor MCP

Servidor MCP para gerenciamento de agendamentos, consumido por um agente de IA
no **n8n** (WhatsApp via **Evolution API** + **Redis**). Persistência real em
**SQLite**, autorização por telefone feita em código, painel web de configuração.

## O que faz

- Servidor MCP (FastMCP, transporte streamable-http) em `/mcp/`
- 13 tools com 3 níveis de permissão (aberto / dono ou próprio / dono)
- Persistência **SQLite** (SQLModel) — sobrevive a reinício, em volume Docker
- Concorrência: checagem de conflito + gravação em seção crítica (lock + SQLite)
- Telefone normalizado para **E.164** com `phonenumbers`
- Fuso horário aplicado (validação de agendamento no passado, slots passados omitidos)
- Painel web `/admin` (HTTP Basic) para editar config e serviços

## Como rodar (local)

```bash
uv run --with-requirements requirements.txt uvicorn app.main:app --reload --port 8000
```

- Painel: http://localhost:8000/admin  (usuário/senha: `ADMIN_USER`/`ADMIN_PASS`, default `admin`/`admin123`)
- Endpoint MCP: http://localhost:8000/mcp/
- Health: http://localhost:8000/health

## Como rodar (Docker — já integrado no compose do projeto)

O serviço `mcp-agendamentos` está no `docker-compose.yml` da raiz. Sobe junto
com o resto:

```bash
docker compose up -d --build
```

Container: `mcp_agendamentos` na rede `evolution-net`. O n8n alcança em
`http://mcp_agendamentos:8000`. Banco em volume `mcp_data` (`/data/agendamentos.db`).

## Variáveis de ambiente

| Var | Default | Função |
|---|---|---|
| `MCP_DB_PATH` | `agendamentos.db` (`/data/...` no Docker) | caminho do SQLite |
| `OWNER_PHONE` / `MCP_OWNER_PHONE` | `5545999990000` | telefone do dono (autoriza ações restritas) |
| `MCP_TZ` | `America/Sao_Paulo` | fuso das conversões de data/hora |
| `ADMIN_USER` / `ADMIN_PASS` | `admin` / `admin123` | credenciais do painel |

## Estrutura

```
app/
  main.py      # FastAPI: monta /mcp (atrás do middleware de solicitante) e /admin
  tools.py     # 13 tools MCP (FastMCP) + validação de fuso/passado
  auth.py      # autorização (dono / próprio cliente) + contextvar do solicitante
  db.py        # SQLite via SQLModel (mesmas assinaturas do esqueleto)
  phone.py     # normalização E.164 (phonenumbers)
  admin.py     # rotas do painel web
  config.py    # settings via env
  templates/admin.html
Dockerfile · requirements.txt
```

## Tools expostas

Abertas: `listar_servicos`, `consultar_horarios_disponiveis`, `agendar`,
`meus_agendamentos`, `instrucoes_gerais`.

Dono ou próprio cliente: `reagendar`, `cancelar`.

Apenas dono: `fechar_data`, `abrir_data`, `bloquear_horario`, `criar_servico`,
`editar_servico`, `ver_agenda_completa`.

## Modelo de segurança (importante)

Quem é o solicitante **não** é decidido pelo modelo. O n8n injeta o telefone do
remetente do webhook na requisição MCP, via query string no endpoint:

```
http://mcp_agendamentos:8000/mcp/?solicitante={{ $('Webhook - Receber Mensagem').item.json.body.data.key.remoteJid }}
```

O `remoteJid` vem com sufixo (ex.: `554599307290@s.whatsapp.net`); o
`@s.whatsapp.net` é descartado em `phone.normalizar()` antes de comparar.

(ou pelo header `X-Solicitante-Telefone`). Um middleware ASGI lê esse valor e o
grava em um contextvar; `auth.requester()` **sempre prefere** o valor injetado
ao argumento `telefone_solicitante` que aparece nas tools. Resultado: o modelo
não consegue se passar por outro número. A decisão de autorização acontece em
`app/auth.py`, em código.

No workflow (`agente_whatsapp.json`) já existe o nó **"Agendamentos (MCP)"**
(`mcpClientTool`, transporte HTTP Streamable) ligado ao **Agente IA** com esse
endpoint configurado.

## Evolução futura (não essencial)

- Painel: trocar HTTP Basic por login de sessão com senha em hash (bcrypt) + cookie.
- Migrar SQLite → Postgres se precisar de múltiplas instâncias do servidor
  (aí a unicidade de horário deve virar constraint/transação no banco, não lock de processo).
- Validações extras: antecedência mínima/máxima, duração mínima, horário comercial por dia da semana.
```
