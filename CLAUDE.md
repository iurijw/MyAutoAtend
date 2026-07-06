# CLAUDE.md — Memória do Projeto

## Visão Geral

Stack Docker para automação de WhatsApp com agente de IA **100% programático**
(sem n8n — removido na branch `feat/sem-n8n`; o caminho da mensagem inteiro
vive em Python no container MCP). Tudo é provisionado automaticamente no
primeiro `docker compose up -d`.

**Serviços:** Evolution API · PostgreSQL 15 · Redis · MCP Agendamentos (`mcp_agendamentos`)

---

## Arquivos Principais

| Arquivo | Função |
|---|---|
| `docker-compose.yml` | Orquestração de todos os serviços |
| `.env` | Só `LOGIN` e `SENHA` (ver seção Variáveis de Ambiente) |
| `myAutoAtendMCP/app/whatsapp.py` | Pipeline do agente: webhook → mídia → debounce → agente → bolhas → envio |
| `myAutoAtendMCP/app/agente.py` | Agente pydantic-ai: tools, memória (SQLite), system prompt |
| `myAutoAtendMCP/app/ia.py` | Provedores de IA (config no SQLite), transcrição, visão, listagem de modelos |
| `myAutoAtendMCP/app/evolution.py` | Cliente Evolution: painel (sync), pipeline (async), bootstrap da instância |
| `myAutoAtendMCP/app/tools.py` | 14 tools de agendamento (FastMCP) — usadas pelo agente E expostas em `/mcp` |
| `myAutoAtendMCP/app/tarefas.py` | Worker de ações proativas (fila `Tarefa`): bot inicia conversa (ex.: remanejar dia) |
| `myAutoAtendMCP/app/templates/admin.html` | Shell do painel: head (tema antes do paint), header, includes dos partials, ponte `window.__ADMIN__`; CSS com cache-bust `?v=N` (subir ao mexer no admin.css) |
| `myAutoAtendMCP/app/templates/partials/` | Um arquivo por card: `whatsapp` · `ia` · `prompt` · `agendamentos` · `conversas` · `proatividade` · `catalogo` · `horarios` · `config` |
| `myAutoAtendMCP/app/static/admin/` | `admin.css` (estilo todo, tokens em `:root` + dark em `html[data-theme="dark"]`) + `js/` (ES modules, 1 por feature; entrada `js/admin.js`) + `vendor/` (Gridstack v11) |

---

## Pipeline da mensagem (app/whatsapp.py)

1. `POST /webhook/whatsapp/receberMensagem` — Evolution entrega `MESSAGES_UPSERT`
   (webhook configurado no startup pelo `evolution.garantir_instancia`).
2. Filtra `fromMe`; upsert do contato na tabela `Cliente` (pushName). Se
   `bot_pausado` p/ o contato (e não é o dono): mídia ainda vira texto, a
   mensagem é gravada na memória (`agente.registrar_na_memoria`) e o fluxo
   PARA — sem marcar lida, sem debounce, sem resposta. Senão, marca como lida
   (falha não interrompe).
3. Mídia → texto: áudio = `POST {base}/audio/transcriptions` (whisper-1,
   multipart OpenAI); imagem = chat completions com `image_url` (data URL).
   Base64 vem do próprio webhook (`message.base64`, instância criada com
   `base64: true`) ou de `getBase64FromMediaMessage`.
4. **Debounce 6s** por contato: buffer em memória + `asyncio.Task`; mensagem
   nova cancela o timer e abre outro; o lote é concatenado com `[quebrar]`.
5. Agente (`agente.responder`): o remoteJid é gravado no contextvar
   `auth.solicitante_ctx` ANTES do run — `auth.requester()` ignora o que o
   modelo passar em `telefone_solicitante` (mesma regra de ouro de sempre).
6. Resposta passa por `agente.limpar_raciocinio` (modelos "reasoning" que
   vazam `<think>`/`</answer>` no conteúdo) e é dividida em bolhas
   (`[quebrar]`, `[quebra]` e `\n+`, com dedupe de bolhas consecutivas
   idênticas); cada bolha enviada via `sendText` com `delay` = digitação
   proporcional (`min(0.4 + len*0.02, 4) + rand*0.7` s).

## Agente (app/agente.py)

- **pydantic-ai** (`pydantic-ai-slim[openai]`): `OpenAIChatModel` +
  `OpenAIProvider(base_url, api_key)` — qualquer provedor compatível.
- Tools = funções originais de `app/tools.py` (o decorator FastMCP devolve a
  função intacta). **Toolset por remetente** (defesa em profundidade; o auth
  fino continua em `auth.py`): `_TOOLS_CLIENTE` (6: listar_servicos, consultar,
  agendar, meus_agendamentos, reagendar, cancelar) e `_TOOLS_DONO` (14 = as 6 +
  gestão: fechar/abrir_data, bloquear_horario, remanejar_dia, criar/editar_
  servico, ver_agenda_completa, pausar_bot). Agent montado a cada mensagem.
- **Memória por contato**: tabela `Conversa` (SQLite), histórico serializado
  com `ModelMessagesTypeAdapter`, janela de 50 mensagens com corte só em
  fronteira de turno do usuário (não quebra par tool-call/tool-return).
  `registrar_na_memoria(telefone, texto, papel)` anexa turno SEM rodar o
  agente (pausa + envio manual do painel); `historico_para_bolhas` desserializa
  p/ o modal de conversas; `limpar_raciocinio` remove `<think>`/`<answer>`
  vazados por modelos reasoning (saída e leitura).
- System prompt: prefixo de data/hora (gerado em Python, fuso da Config) +
  instrução geral + bloco MCP **por perfil** — tabela `Prompt`, chaves `geral`,
  `mcp_dono`, `mcp_cliente` (defaults `PROMPT_GERAL_PADRAO`/`PROMPT_MCP_DONO_
  PADRAO`/`PROMPT_MCP_CLIENTE_PADRAO` em `agente.py`; a versão cliente não
  menciona ações de gestão; chave legada `mcp` migrada por `db._migrar_prompts`).
  Lido A CADA mensagem → salvar no painel aplica na hora.

## Provedores de IA (app/ia.py)

- Config por uso (texto/áudio/imagem) na tabela `ProvedorIA` (SQLite):
  api_key, base_url, modelo. **Fluxo unidirecional**: chave entra pelo painel,
  nenhuma rota devolve (nem mascarada).
- Compatibilidade: texto/imagem = qualquer chat completions compatível;
  áudio = multipart OpenAI com whisper-1 (OpenRouter usa JSON base64 — futuro).
- Anthropic entra pela camada OpenAI-compatível (`https://api.anthropic.com/v1`);
  só o `GET /models` usa headers nativos (`x-api-key` + `anthropic-version`).
- Defaults de modelo: texto `gpt-5.1`, imagem `gpt-4o`, áudio `whisper-1`.
- Sem chave salva → `IANaoConfigurada`; o agente não responde até configurar
  no painel (card "Provedores de IA").

## Instância Evolution API

- Nome: `evo_bot` (env `EVOLUTION_INSTANCE`).
- Criada no startup do MCP (`evolution.garantir_instancia`, task no lifespan):
  espera a Evolution, cria se faltar, e **(re)configura o webhook sempre** →
  `http://mcp_agendamentos:8000/webhook/whatsapp/receberMensagem`
  (`MESSAGES_UPSERT`, base64 ativado).
- Pareamento: QR Code no card "Conexão WhatsApp" do `/admin`.

## Servidor MCP (`myAutoAtendMCP/`)

- FastAPI único: pipeline do WhatsApp + painel `/admin` (HTTP Basic) +
  endpoint `/mcp/` (streamable-http) mantido p/ clients MCP externos
  (Claude etc.) — o agente interno NÃO passa por ele (chama as tools direto).
- Persistência SQLite (SQLModel), volume `mcp_data` → `/data/agendamentos.db`.
  Tabelas: Config, Prompt, ProvedorIA, Conversa, Cliente (telefone E.164 PK,
  nome do pushName, bot_pausado), Servico, Bloqueio, Agendamento,
  HorarioFuncionamento, Tarefa.
- Telefone E.164 (`phonenumbers`); autorização dono/próprio em `app/auth.py`.
- Clients MCP externos identificam o solicitante via `?solicitante=` ou header
  `X-Solicitante-Telefone` (middleware em `main.py`).
- **Pareamento WhatsApp no painel**: `GET /admin/whatsapp/estado` (conectado →
  agrega `perfil`: número canônico/nome/foto via `fetchInstances`),
  `GET /admin/whatsapp/qr`, `POST /admin/whatsapp/desconectar`.
- **Avatar do cliente nos agendamentos**: `GET /admin/whatsapp/foto?numero=...`
  (`evolution.foto_perfil`, timeout 5s, cache 1h/5min vazio; fallback inicial).
- **Checagem de número**: `GET /admin/whatsapp/checar?numero=` →
  `evolution.checar_numero` (POST whatsappNumbers, cache 10min) devolve
  {existe, numero (E.164 do jid — resolve o nono dígito), numero_fmt, foto};
  usado pela máscara de telefone (`js/telefone.js`, inputs `data-telefone` no
  modal de agendamento e no telefone do dono; envio sempre em dígitos canônicos).
- **Conversas no painel** (card + modal, `js/conversas.js`): `GET
  /admin/conversas` (lista com preview), `GET /admin/conversas/{tel}` (bolhas
  cliente/bot/sistema), `POST /admin/conversas/{tel}/enviar` (manual, sem IA;
  só grava na memória após sucesso; falha → 502), `POST
  /admin/conversas/{tel}/pausa` (bot_pausado; dono nunca pausável — também na
  tool `pausar_bot` [DONO]). Botão "Conversa" na listagem de agendamentos abre
  o modal (`window.abrirConversa`).
- **Provedores de IA no painel**: `GET /admin/ia/estado`, `GET /admin/ia/modelos`,
  `POST /admin/ia/modelos-preview` (chave transiente), `POST /admin/ia/credencial`,
  `POST /admin/ia/modelo`.
- **Instruções do agente**: `GET/POST /admin/agente/prompt` (SQLite direto).
- **Cadastro manual de agendamento**: botão "+ Novo agendamento" no card abre
  MODAL (`js/agendamento.js`) com seletor de horário em quadrados — `GET
  /admin/agenda/slots?data=&servico_id=` (mesma lógica da tool de consulta;
  passo = duração do serviço; ocupado = conflito ou horário passado hoje) —
  e telefone com máscara/checagem. Submissão → `POST /admin/agendamento`
  (telefone normalizado E.164; slots do modal respeitam o expediente, mas o
  endpoint em si não valida horário de funcionamento — override do dono, como
  o reagendar do painel; conflito com agendamento/bloqueio → 409).
- **Aviso ao dono** (`app/notificacoes.py`): WhatsApp do dono recebe template
  fixo (sem IA) quando o BOT agenda/reagenda/cancela. Liga/desliga só pelo
  painel (checkbox na Configuração geral → `Config.avisar_dono`, ALTER em
  `_migrar`). Suprimido se ação é do próprio dono, telefone placeholder ou
  flag off; falha de envio nunca quebra a tool (`enviar_texto_sync`, 5s).
- **Aviso ao cliente (aval do dono)**: reagendar/cancelar individual pelo dono
  pode disparar aviso proativo da IA ao cliente — painel (checkbox no form de
  reagendar; 2º `confirm` no cancelar) e tools `reagendar`/`cancelar` (param
  `avisar_cliente`, honrado só p/ dono em agendamento de terceiro; o prompt MCP
  manda pedir o aval antes). `db.criar_aviso_cliente` enfileira `Tarefa`
  `contatar_cliente` (acoes `reagendado`/`cancelado`, instruções em
  `tarefas.py`); avisos pendentes do mesmo agendamento são substituídos
  (reagendos encadeados herdam o `inicio_anterior` original).
- **Endurecimento contra injeção**: webhook exige `?token=` (hash da SENHA,
  `settings.webhook_token`; Evolution configurada com ele no bootstrap —
  forja local de remoteJid → 403); `[TAREFA INTERNA]` vindo do webhook é
  neutralizado (`whatsapp._sanitizar_entrada` — só o worker injeta o marcador
  legítimo); prompt MCP instrui que tool results são dados, não instruções;
  `groupsIgnore: true` aplicado a cada boot (grupos compartilhariam memória).
- **Ações proativas** (`app/tarefas.py`): fila persistente `Tarefa` + worker
  asyncio no lifespan (tick 30s). Cada tarefa roda `agente.executar_tarefa`
  (mesma memória do contato, input prefixado `[TAREFA INTERNA]`) e envia via
  `whatsapp.enviar_bolhas` — a resposta do cliente segue no pipeline reativo.
  Guard-rails: janela de cortesia 08–20h (fuso da Config), adia se o contato
  está no debounce, rate limit com jitter, máx. 3 tentativas, `executando`
  órfã volta a pendente no boot, `IANaoConfigurada` não queima tentativa.
  Caso âncora: tool `remanejar_dia(data, acao, motivo)` [DONO] — fecha o dia,
  (se acao="cancelar") cancela os agendamentos, e cria uma tarefa
  `contatar_cliente` por cliente afetado.
- **Proatividade no painel**: card "Proatividade Pendente" (`partials/
  proatividade.html` + `js/proatividade.js`, poll 20s) mostra a fila ao vivo —
  pendente/executando + últimas falhadas (concluídas/canceladas fora).
  `GET /admin/tarefas/estado` (`db.listar_tarefas_painel` +
  `tarefas.descrever_tarefa` p/ resumo legível) e
  `POST /admin/tarefas/{id}/cancelar` (`db.cancelar_tarefa` — só pendente vira
  `cancelada`, status string sem migração; executando/falhou → 409).
- **Horários de funcionamento**: card próprio no painel; grade semanal na
  tabela `HorarioFuncionamento` (N intervalos por `dia_semana` 0–6; dia sem
  linha = fechado). `POST /admin/horarios` (replace-all da grade),
  `/admin/horarios/restaurar` (padrão seg–sex 08:00–12:00 + 13:30–18:00),
  `/admin/horarios/limpar`. Seed do padrão SÓ na criação da tabela (vazia ≠
  nova). Tools `consultar_horarios_disponiveis`/`agendar`/`reagendar`
  respeitam a grade; `Config.abertura/fechamento` viraram colunas órfãs.
- **UI do painel**: grade Gridstack (`js/grade.js`, vendor local) — cards
  ocupam a janela, drag pelo grip ⠿ + resize; layout por navegador em
  localStorage `admin-grade-v1` (posição efetiva = salvo > `PADRAO` > linha
  cheia; salva só em dragstop/resizestop + 1 save de ponto fixo pós-montagem;
  card oculto NÃO participa da montagem — gear.js emite `admin:cards-ocultos`).
  Tema claro/escuro por tokens (`data-theme`, default escuro, `js/tema.js`).
  Toasts (`js/toast.js`) + interceptação de forms POST (`js/forms.js`,
  opt-out `data-nativo`) + validação (`js/validar.js`) + máscara/checagem de
  telefone (`js/telefone.js`). Cards sempre abertos (sem recolher). Modais
  (conversas, agendamento) vivem FORA dos cards, realocados pro `body`.
- Após mudar código: `docker compose up -d --build mcp-agendamentos`.

---

## Comandos Úteis

```bash
# Subir tudo
docker compose up -d

# Rebuild só do MCP (código novo)
docker compose up -d --build mcp-agendamentos

# Logs do agente (init, pipeline, erros)
docker logs -f mcp_agendamentos

# Simular mensagem recebida (teste sem WhatsApp pareado)
# ATENÇÃO: body precisa ser UTF-8 (PowerShell 5.1 manda Latin-1 por padrão)
# O webhook exige ?token= (hash da SENHA) — pegue assim:
TOKEN=$(docker exec mcp_agendamentos python -c "from app.config import settings; print(settings.webhook_token)")
curl -s -X POST "http://localhost:8000/webhook/whatsapp/receberMensagem?token=$TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"instance":"evo_bot","data":{"key":{"remoteJid":"5599999999999@s.whatsapp.net","fromMe":false,"id":"TEST1"},"pushName":"Teste","messageType":"conversation","message":{"conversation":"Oi"}}}'

# Reset de fábrica (apaga tudo, inclusive pareamento)
docker compose down -v && docker compose up -d
```

---

## Variáveis de Ambiente (.env)

O `.env` tem SÓ duas variáveis:

```env
LOGIN=   # e-mail: usuário do painel /admin
SENHA=   # senha do /admin + apikey da Evolution (AUTHENTICATION_API_KEY)
```

> Guards `${VAR:?}` no compose fazem o `up` falhar com mensagem se o `.env`
> faltar. Trocar a SENHA depois: basta `docker compose up -d` de novo
> (Evolution e painel leem do env a cada boot — sem n8n não há senha gravada
> em banco).

O que é config pós-boot (pelo painel `/admin`):
- **Chave de IA** → card "Provedores de IA". Agente não responde até isso.
- **Telefone do dono** → "Configuração geral" (placeholder `5500000000000`
  no compose até lá).
- **System prompt** → card "Instruções do Agente" (defaults em `app/agente.py`;
  env `AGENT_SYSTEM_PROMPT` ainda é aceita como seed legado, mas não vem no
  compose).

Postgres é interno (sem porta no host): credenciais constantes hardcoded no
compose (`evolution` / `evolution_db_interno` / `evolution_api_db`) — inclusive
embutidas na `DATABASE_CONNECTION_URI` da Evolution.

---

## Convenções de Commit

Usar tags convencionais: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
