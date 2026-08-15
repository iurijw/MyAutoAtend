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
| `myAutoAtendMCP/app/tools.py` | 16 tools (FastMCP): 14 de agendamento + 2 da ficha de cadastro — usadas pelo agente E expostas em `/mcp` |
| `myAutoAtendMCP/app/tarefas.py` | Worker de ações proativas (fila `Tarefa`): bot inicia conversa (ex.: remanejar dia) |
| `myAutoAtendMCP/app/ficha.py` | Ficha de cadastro: tipos de campo, validação/normalização por tipo, montagem da ficha de um contato |
| `myAutoAtendMCP/app/sessao.py` | Sessão do painel: JWT HS256 (só stdlib) em cookie httpOnly, freio de força bruta por IP, exceção `SessaoInvalida` |
| `myAutoAtendMCP/app/templates/login.html` | Tela de entrada (`/login`), fora do shell do painel — usa `admin.css` (tokens) + `login.css` |
| `myAutoAtendMCP/app/templates/admin.html` | Shell do painel: head (tema antes do paint), barra lateral (nav), topo, uma `<section class="view">` por seção, ponte `window.__ADMIN__`; CSS com cache-bust `?v=N` (subir ao mexer no admin.css) |
| `myAutoAtendMCP/app/templates/partials/` | Conteúdo de cada seção: `conversas` · `agendamentos` · `clientes` · `ficha` · `servicos` · `horarios` · `bloqueios` · `ia` + `prompt` (seção Agente) · `proatividade` · `whatsapp` · `config`; mais `icones` (sprite SVG) |
| `myAutoAtendMCP/app/static/admin/` | `admin.css` (estilo todo, tokens em `:root` + dark em `html[data-theme="dark"]` + acento por seção em `[data-accent]`) + `js/` (ES modules, 1 por feature; entrada `js/admin.js`) |

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
  `_TOOLS_FICHA` (ver_ficha, preencher_ficha) entra nos DOIS perfis, mas só
  com `Config.ficha_ativa` — desligada, o modelo nem vê que a ficha existe.
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
  Com a ficha ligada, `agente.prompt_ficha()` anexa mais um bloco: parte
  técnica fixa (`PROMPT_FICHA_PADRAO` — contrato das tools, nunca inventar
  dado, no máximo 1–2 perguntas por mensagem) + instrução do dono (`Prompt`
  chave `ficha`, default `PROMPT_FICHA_INSTRUCAO_PADRAO`).
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
  no painel (seção "Agente de IA").

## Instância Evolution API

- Nome: `evo_bot` (env `EVOLUTION_INSTANCE`).
- Criada no startup do MCP (`evolution.garantir_instancia`, task no lifespan):
  espera a Evolution, cria se faltar, e **(re)configura o webhook sempre** →
  `http://mcp_agendamentos:8000/webhook/whatsapp/receberMensagem`
  (`MESSAGES_UPSERT`, base64 ativado).
- Pareamento: QR Code na seção "Conexão WhatsApp" do `/admin`.

## Servidor MCP (`myAutoAtendMCP/`)

- FastAPI único: pipeline do WhatsApp + painel `/admin` (login em `/login`) +
  endpoint `/mcp/` (streamable-http) mantido p/ clients MCP externos
  (Claude etc.) — o agente interno NÃO passa por ele (chama as tools direto).
- **Login do painel** (`app/sessao.py`, sem HTTP Basic desde então): `/login`
  compara com `ADMIN_USER`/`ADMIN_PASS` (`compare_digest` sobre bytes) e devolve
  cookie `maa_sessao` — JWT HS256 feito com hmac/hashlib, exp de 12 h, httpOnly,
  samesite lax, `secure=False` porque o painel roda em http://localhost. Segredo
  = hash da SENHA (`settings.session_secret`), então trocar a SENHA derruba toda
  sessão aberta; não há lista de revogação. `admin.autenticar` (dependência de
  TODA rota do painel) só lê o cookie e levanta `SessaoInvalida`; o handler em
  `main.py` decide: navegação HTML → 303 p/ `/login?next=<caminho>` (destino
  filtrado por `_destino_seguro` contra open redirect), fetch → 401 +
  `X-Sessao: expirada`, que o `js/sessao.js` (embrulha `window.fetch`, importado
  primeiro no `admin.js`) transforma em ida ao login. 8 falhas por IP em 5 min →
  429 por 2 min (`sessao.bloqueio_restante`, memória do processo). "Sair" fica no
  pé da barra lateral (form `data-nativo` → `POST /logout`). A tela nunca mostra
  o e-mail configurado — só ecoa o que foi digitado.
- Persistência SQLite (SQLModel), volume `mcp_data` → `/data/agendamentos.db`.
  Tabelas: Config, Prompt, ProvedorIA, Conversa, Cliente (telefone E.164 PK,
  nome do pushName, bot_pausado), Servico, Bloqueio, Agendamento,
  HorarioFuncionamento, Tarefa, CampoFicha, ValorFicha (PK composta
  telefone+campo_id). O telefone é chave em 5 tabelas — trocar o número de um
  contato passa por `db.mover_contato` (ver "Ficha de cadastro"), nunca por
  UPDATE em uma tabela só.
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
- **Máscara de telefone** (`js/telefone.js`): o prefixo `+55` aparece no FOCO
  do campo (não depois do 1º dígito) e é apagável — o estado é o dígito
  internacional (`internacional()` assume 55 quando não há `+`), o backspace
  come um dígito mesmo quando o char deletado era da máscara, então dá para
  chegar em `+` e digitar outro DDI (`+DDI` sem máscara BR). Blur/submit com
  só o prefixo limpa o campo; `required` + Enter no campo é barrado no listener
  de submit em capture (toast), porque para o browser "+55" não é vazio.
  Ao fim de cada checagem o campo dispara o CustomEvent **`telefone-numero`**
  (`detail.numero` = canônico da Evolution, ou os dígitos digitados quando ela
  não confirma/está fora) — é o gancho de quem precisa buscar algo do contato
  (a ficha no modal de agendamento) sem adivinhar quando a digitação terminou.
- **Conversas no painel** (seção + modal, `js/conversas.js`): `GET
  /admin/conversas` (lista com preview), `GET /admin/conversas/{tel}` (bolhas
  cliente/bot/sistema), `POST /admin/conversas/{tel}/enviar` (manual, sem IA;
  só grava na memória após sucesso; falha → 502), `POST
  /admin/conversas/{tel}/pausa` (bot_pausado; dono nunca pausável — também na
  tool `pausar_bot` [DONO]). Botão "Conversa" nas listagens de agendamentos e
  de clientes abre o modal (`window.abrirConversa`).
- **Provedores de IA no painel**: `GET /admin/ia/estado`, `GET /admin/ia/modelos`,
  `POST /admin/ia/modelos-preview` (chave transiente), `POST /admin/ia/credencial`,
  `POST /admin/ia/modelo`.
- **Instruções do agente**: `GET/POST /admin/agente/prompt` (SQLite direto).
- **Cadastro manual de agendamento**: botão "+ Novo agendamento" na seção abre
  MODAL (`js/agendamento.js`) com seletor de horário em quadrados — `GET
  /admin/agenda/slots?data=&servico_id=` (mesma lógica da tool de consulta;
  passo = duração do serviço; ocupado = conflito ou horário passado hoje) —
  e telefone com máscara/checagem. O campo Cliente tem **autocomplete de
  contato** (`js/autocomplete.js` + `GET /admin/clientes/buscar?q=` — mesma
  agenda de `_fichas_clientes`, casa nome ou dígitos do telefone, máx. 8):
  escolher um item preenche nome + telefone (dispara `input` p/ máscara e
  checagem); ↑/↓ navega, Enter escolhe sem enviar o form, Esc fecha só a lista.
  Submissão → `POST /admin/agendamento`
  (telefone normalizado E.164; slots do modal respeitam o expediente, mas o
  endpoint em si não valida horário de funcionamento — override do dono, como
  o reagendar do painel; conflito com agendamento/bloqueio → 409).
  Com a ficha ligada, o modal também traz **os campos da ficha** (`#agm-ficha`,
  montados por `ficha.montarCampos`): sabendo o telefone busca
  `GET /admin/ficha/cliente/{tel}` — que já devolve os VALORES do contato, então
  cliente antigo aparece preenchido (e o nome do cadastro entra se o campo
  estiver vazio); sem telefone ainda, `GET /admin/ficha/campos` dá a definição
  vazia. Gatilhos da (re)montagem: abrir o modal, escolher no autocomplete e o
  evento `telefone-numero`. `POST /admin/agendamento` (agora lê `request.form()`)
  valida os `campo_<chave>` ANTES de criar o agendamento — erro → 400
  `{erros}` e nada é criado — e grava depois com origem "painel"; campo enviado
  vazio APAGA o valor (mesma semântica do modal da ficha).
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
  `contatar_cliente` (acoes `reagendado`/`cancelado` individuais e
  `remarcar`/`cancelar` do remanejo de dia — `remanejar_dia` também passa por
  ela, senão o cliente receberia dois avisos do mesmo agendamento).
- **Ciclo de vida do aviso**: aviso pendente do mesmo agendamento é sempre
  descartado antes de entrar um novo (status **`obsoleto`**, fora dos filtros do
  painel e do worker — separado de `concluida`, que significa MENSAGEM ENVIADA).
  `db.cancelar_agendamento` descarta o aviso pendente do agendamento (por
  qualquer caminho: painel, tool `cancelar`, `remanejar_dia`) — avisar
  remarcação de horário já cancelado é pior que não avisar; cancelar COM aval
  ainda avisa, porque o caller enfileira o `cancelado` depois. O aviso novo
  herda o `inicio_anterior` do descartado (dos pendentes e dos `obsoleto`, já
  que o cancelamento descarta antes de enfileirar): é o único horário que o
  cliente conhece, e a instrução do `cancelado`/`remarcar` avisa o agente em
  caixa alta para falar DESSE horário, não do remarcado que nunca saiu.
  `db.limpar_avisos_orfaos()` roda no boot do worker (rede de segurança para
  fila antiga e agendamento apagado por fora; não toca em aviso de
  cancelamento, que fala de um agendamento cancelado de propósito).
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
- **Proatividade no painel**: seção "Proatividade" (`partials/
  proatividade.html` + `js/proatividade.js`, poll 20s) mostra a fila ao vivo —
  pendente/executando + últimas falhadas (concluídas/canceladas fora).
  `GET /admin/tarefas/estado` (`db.listar_tarefas_painel` +
  `tarefas.descrever_tarefa` p/ resumo legível) e
  `POST /admin/tarefas/{id}/cancelar` (`db.cancelar_tarefa` — só pendente vira
  `cancelada`, status string sem migração; executando/falhou → 409). Status de
  `Tarefa` são string livre: `pendente`/`executando`/`concluida`/`falhou`/
  `cancelada`/`obsoleto` — os filtros são allowlist, então status novo não
  aparece na fila sem querer.
- **Horários de funcionamento**: seção própria no painel; grade semanal na
  tabela `HorarioFuncionamento` (N intervalos por `dia_semana` 0–6; dia sem
  linha = fechado). `POST /admin/horarios` (replace-all da grade),
  `/admin/horarios/restaurar` (padrão seg–sex 08:00–12:00 + 13:30–18:00),
  `/admin/horarios/limpar`. Seed do padrão SÓ na criação da tabela (vazia ≠
  nova). Tools `consultar_horarios_disponiveis`/`agendar`/`reagendar`
  respeitam a grade; `Config.abertura/fechamento` viraram colunas órfãs.
- **Ficha de cadastro** (feature OPCIONAL, `Config.ficha_ativa`, ALTER em
  `_migrar`): cadastro por contato com campos definidos pelo dono. `CampoFicha`
  (chave slug estável + rótulo + tipo + dica p/ o agente + obrigatorio + ordem
  + ativo) e `ValorFicha` (valor já normalizado + `origem` "agente"/"painel" +
  atualizado_em). Tipos e validação em `app/ficha.py` (texto, texto_longo,
  numero, data, hora, telefone, email, booleano, selecao) — a MESMA para o
  painel e para o agente: data vira YYYY-MM-DD, seleção casa a opção
  canônica, telefone vira E.164. `ficha.preencher` é parcial: campo recusado
  volta em `erros` sem derrubar o resto do lote.
  - Tools `ver_ficha` / `preencher_ficha({chave: valor})`: cliente só mexe na
    própria; dono passa `telefone_cliente` p/ a de outro. Desligada → as duas
    recusam com aviso. Escrita pelas tools sempre grava origem "agente".
  - Painel: seção "Ficha de cadastro" (`partials/ficha.html` + `js/ficha.js`) —
    liga/desliga + instrução de coleta (`POST /admin/ficha/ajustes`), CRUD de
    campos (`/admin/ficha/campo`, `/{id}/toggle|excluir|mover`). A ficha de um
    contato abre em MODAL pela lista de Clientes (botão "Ficha", só com a
    feature ligada) — `GET/POST /admin/ficha/cliente/{tel}`, input por tipo,
    erro por campo, botão **Abrir conversa** (`window.abrirConversa`).
    Excluir campo apaga os valores dele em todos os clientes (confirm no form).
  - **Identificação editável no modal da ficha** (é a única tela que corrige um
    contato): campos `nome` + `telefone` no mesmo `POST /admin/ficha/cliente/
    {tel}`. Nome → `db.renomear_cliente` (muda `Cliente.nome` E o `nome_cliente`
    dos agendamentos dele, que é foto do momento da marcação). Telefone
    diferente → `db.mover_contato`, que MIGRA tudo indexado pelo número numa
    transação: `Cliente` (PK, preserva pausa), `ValorFicha` (PK composta),
    `Conversa` (memória, chave = `digitos@s.whatsapp.net`), `Agendamento.
    telefone_cliente` (inclusive cancelados) e `Tarefa.telefone_alvo` (só
    pendente/executando — senão o bot escreveria pro número velho). Guardas:
    ficha validada ANTES de mover (nada de contato movido pela metade),
    destino com rastro (`db.contato_tem_rastro`: cliente, conversa, agenda ou
    ficha) → 409 sem merge, e contato do dono → 400 (número do dono é
    autorização, muda na Configuração geral). No painel: `confirm` antes de
    enviar e `location.reload()` depois (lista e agenda vêm do servidor).
    O campo é preenchido com `definirTelefone` (telefone.js) — máscara sem
    disparar a checagem, senão o canônico da Evolution entraria no valor e o
    submit moveria o contato sem ninguém pedir.
  - `js/ficha.js` exporta `montarCampos(container, campos)` e
    `marcarErros(container, erros)` — o cadastro manual de cliente e o modal de
    agendamento reusam os MESMOS inputs (`GET /admin/ficha/campos` devolve a
    definição sem valores; `/admin/ficha/cliente/{tel}` devolve com valores).
- **Clientes** (seção própria): agenda de contatos renderizada pelo servidor
  (`admin._fichas_clientes` junta tabela `Cliente` + telefones que só existem
  em agendamentos antigos; `dono` marcado por `mesmo_numero`). `js/clientes.js`
  faz busca local (nome/telefone, casa também só os dígitos), toggle de pausa
  (mesma rota `/admin/conversas/{tel}/pausa`), "Conversa" (`window.abrirConversa`)
  e "Agendar" (`window.abrirNovoAgendamento({nome, telefone})` — abre o modal já
  preenchido). Cadastro manual de agendamento faz `db.upsert_cliente`, então o
  contato entra na agenda sem precisar mandar mensagem antes.
  **"+ Novo cliente"** abre modal com nome + telefone e, se a ficha estiver
  ligada, os campos dela (`POST /admin/cliente`): a ficha é validada ANTES de
  criar o contato (nada de cliente meio cadastrado), número repetido → 409
  apontando para a ficha existente. Telefone digitado à mão passa por
  `phone.plausivel` (E.164 válido ou 10–15 dígitos) — `normalizar` sozinho
  aceita qualquer dígito de propósito, para o pipeline nunca perder contato.
- **UI do painel**: barra lateral fixa + área de conteúdo (sem grade
  arrastável — Gridstack/`grade.js`/`gear.js` removidos). Conteúdo **sem caixa
  interna**: `.card` é só bloco de espaçamento (nada de borda/fundo/sombra) —
  o topo já emoldura a seção. Seção de bloco único usa `.bloco-barra` (chips à
  esquerda, ação/busca à direita, sem `h2` que repetiria o título do topo);
  seção com 2+ blocos (Serviços, Bloqueios, Agente) mantém `.card-head` com
  `h2` + fio. `.col2` separa por fio vertical (horizontal quando empilha).
  Campos e superfícies internas usam `--card` sobre o papel da página.
  O servidor entrega
  TODAS as views; `js/nav.js` deixa só uma com `.ativa`, escolhida pelo hash
  (`#clientes`) — sobrevive ao reload dos forms e é linkável. Abaixo de 1000px
  a lateral vira gaveta (botão ☰). **Acento por seção**: cada view e o item de
  nav declaram `data-accent` (`zap` verde-WhatsApp para conversas/conexão,
  `marca` oxblood para agenda, `mare` para clientes, `ouro` para serviços,
  `mata` para horários, `tinta` para o agente, `neutro` p/ config), que
  redefine `--accent`/`--accent-solid`/`--accent-on`; rail do menu, fio do topo,
  tags, `.btn-acento` e o foco dos campos herdam (fundos suaves via
  `color-mix`). Tema claro/escuro por tokens (`data-theme`, default escuro,
  `js/tema.js`, botão no pé da lateral). Toasts (`js/toast.js`) + interceptação
  de forms POST (`js/forms.js`, opt-out `data-nativo`) + validação
  (`js/validar.js`) + máscara/checagem de telefone (`js/telefone.js`). Modais
  (conversas, agendamento) são realocados pro `body` — a view de origem fica
  `display:none` quando outra seção está aberta.
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
- **Chave de IA** → seção "Agente de IA". Agente não responde até isso.
- **Telefone do dono** → "Configuração geral" (placeholder `5500000000000`
  no compose até lá).
- **System prompt** → seção "Agente de IA", card "Instruções do Agente"
  (defaults em `app/agente.py`; env `AGENT_SYSTEM_PROMPT` ainda é aceita como
  seed legado, mas não vem no compose).

Postgres é interno (sem porta no host): credenciais constantes hardcoded no
compose (`evolution` / `evolution_db_interno` / `evolution_api_db`) — inclusive
embutidas na `DATABASE_CONNECTION_URI` da Evolution.

---

## Convenções de Commit

Usar tags convencionais: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
