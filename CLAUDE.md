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
| `myAutoAtendMCP/app/tools.py` | 18 tools (FastMCP): 16 de agendamento (incl. `concluir_atendimento`/`reabrir_atendimento`) + 2 da ficha de cadastro — usadas pelo agente E expostas em `/mcp` |
| `myAutoAtendMCP/app/tarefas.py` | Worker de ações proativas (fila `Tarefa`): bot inicia conversa (ex.: remanejar dia) |
| `myAutoAtendMCP/app/midia.py` | Mídia da conversa: desembrulha o payload Baileys (efêmera/ver-uma-vez/doc com legenda), classifica o tipo e guarda o arquivo em `/data/midia` (teto de 25 MB) |
| `myAutoAtendMCP/app/ficha.py` | Ficha de cadastro: tipos de campo, validação/normalização por tipo, montagem da ficha de um contato |
| `myAutoAtendMCP/app/sessao.py` | Sessão do painel: JWT HS256 (só stdlib) em cookie httpOnly, freio de força bruta por IP, exceção `SessaoInvalida` |
| `myAutoAtendMCP/app/templates/login.html` | Tela de entrada (`/login`), fora do shell do painel — usa `admin.css` (tokens) + `login.css` |
| `myAutoAtendMCP/app/templates/admin.html` | Shell do painel: head (tema antes do paint), barra lateral (nav), topo, uma `<section class="view">` por seção, ponte `window.__ADMIN__`; CSS com cache-bust `?v=N` (subir ao mexer no admin.css — o `/static` também vai com `Cache-Control: no-cache`, ver `EstaticosRevalidados` em `main.py`, senão o browser fica com o JS velho depois do deploy) |
| `myAutoAtendMCP/app/templates/partials/` | Conteúdo de cada seção: `kanban` (Quadro) · `conversas` · `agendamentos` · `clientes` · `ficha` · `servicos` · `horarios` · `bloqueios` · `ia` + `prompt` (seção Agente) · `proatividade` · `whatsapp` · `config`; mais `icones` (sprite SVG), `onboarding` (guia da 1ª execução, incluído só quando `Config.onboarding_visto` é falso) e `agendamentos_linhas` (só as `<tr>` da agenda — servidas na carga E em `/admin/agendamentos/estado`) |
| `myAutoAtendMCP/app/static/admin/` | `admin.css` (estilo todo, tokens em `:root` + dark em `html[data-theme="dark"]` + acento por seção em `[data-accent]`) + `js/` (ES modules, 1 por feature; entrada `js/admin.js`) |

---

## Pipeline da mensagem (app/whatsapp.py)

1. `POST /webhook/whatsapp/receberMensagem` — Evolution entrega `MESSAGES_UPSERT`
   (webhook configurado no startup pelo `evolution.garantir_instancia`).
2. Contato: `_jid_do_contato` resolve o remoteJid — endereço `@lid` (formato
   novo do WhatsApp) NÃO é telefone, o número real vem em `remoteJidAlt`.
   Upsert na tabela `Cliente` (pushName). Se `bot_pausado` p/ o contato (e não
   é o dono): mídia ainda vira texto/arquivo, a mensagem é gravada na memória
   (`agente.registrar_na_memoria`) e o fluxo PARA — sem marcar lida, sem
   debounce, sem resposta. Senão, marca como lida (falha não interrompe).
2b. **`fromMe` não é mais descartado** (`_registrar_saida`): a Evolution
   devolve tudo que sai do nosso número. Se o id está em
   `evolution.enviado_por_nos` (lista dos envios da API, 500 últimos), é eco do
   próprio bot e é ignorado; senão é o DONO digitando no WhatsApp do negócio —
   vira fala do bot na memória com `origem="aparelho"`, sem acionar o agente
   (ele não responde a si mesmo). 2º cinto p/ eco pós-restart:
   `agente.foi_dito_pelo_bot` compara com as bolhas dos 2 últimos turnos dele.
2c. **Reação nunca vira turno** e NUNCA aciona o agente (responder a um 👍 é
   ruído): `_registrar_reacao` grava o emoji na `MensagemRef` da mensagem
   reagida (`reactionMessage.key.id`) e o painel desenha na quina da bolha.
   Emoji vazio = reação desfeita; alvo fora da tabela vira só log.
3. Conteúdo → texto + arquivo (`_conteudo_da_mensagem` + `app/midia.py`):
   texto/extendedText; áudio = `POST {base}/audio/transcriptions` (whisper-1,
   multipart OpenAI); imagem = chat completions com `image_url` (data URL);
   vídeo, figurinha, documento, localização, contato e enquete viram
   marcador em português (`[Figurinha]`, `[Documento x.pdf]`
   …) — antes disso caíam no `return None` e a mensagem sumia da conversa.
   **A IA é opcional aqui** (`_ler_com_ia`): sem provedor de visão/áudio
   configurado a leitura falha e a mensagem segue com o marcador seco. Já
   quebrou em produção — a exceção do `descrever_imagem` matava o evento
   inteiro e a foto não chegava nem ao painel.
   Mídia SAINDO (dono pelo celular) não gasta IA: nem transcreve nem descreve.
   Base64 vem do próprio webhook (`message.base64`, instância criada com
   `base64: true`) ou de `getBase64FromMediaMessage`; o arquivo é gravado em
   `/data/midia` e a linha em `Midia` (dedupe por `msg_id`), com `texto` = o
   marcador que foi p/ a memória — é a chave que o painel usa p/ casar arquivo
   e bolha, sem sujar o histórico do modelo com ids.
4. **Debounce 12s** por contato: buffer em memória + `asyncio.Task`; mensagem
   nova cancela o timer e abre outro; o lote é concatenado com `[quebrar]`.
   Eram 6s — curto demais p/ quem escreve em rajada, o bot respondia duas
   vezes à mesma pergunta partida. Além do tempo, há um `asyncio.Lock` POR
   CONTATO e o buffer só é esvaziado DENTRO dele: mensagem que chega enquanto
   o agente responde entra no lote seguinte em vez de abrir um turno paralelo.
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
  agendar, meus_agendamentos, reagendar, cancelar) e `_TOOLS_DONO` (16 = as 6 +
  gestão: fechar/abrir_data, bloquear_horario, remanejar_dia, criar/editar_
  servico, ver_agenda_completa, concluir_atendimento, reabrir_atendimento,
  pausar_bot). Agent montado a cada mensagem.
  `_TOOLS_FICHA` (ver_ficha, preencher_ficha) entra nos DOIS perfis, mas só
  com `Config.ficha_ativa` — desligada, o modelo nem vê que a ficha existe.
- **Memória por contato**: tabela `Conversa` (SQLite), histórico serializado
  com `ModelMessagesTypeAdapter`, janela de 50 mensagens com corte só em
  fronteira de turno do usuário (não quebra par tool-call/tool-return).
  `registrar_na_memoria(telefone, texto, papel)` anexa turno SEM rodar o
  agente (pausa + envio manual do painel); `historico_para_bolhas` desserializa
  p/ o modal de conversas; `limpar_raciocinio` remove `<think>`/`<answer>`
  vazados por modelos reasoning (saída e leitura).
- System prompt: cabeçalho gerado em Python — identidade (`Você é o assistente
  virtual do estabelecimento {Config.nome_negocio}…`, só quando o nome está
  preenchido) + data/hora no fuso da Config. O nome NÃO fica escrito no texto
  do prompt: trocar em Configuração geral vale na mensagem seguinte, sem
  ninguém editar instrução. O default `PROMPT_GERAL_PADRAO` proíbe emoji
  ("NUNCA use emojis") — prompt já salvo no banco não muda sozinho, o dono
  edita ou restaura o padrão no painel. Depois do cabeçalho vem a
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
  cookie `maa_sessao` — JWT HS256 feito com hmac/hashlib, exp de 30 dias, httpOnly,
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
  telefone+campo_id), Midia (metadados do arquivo; os bytes ficam em
  `/data/midia`, mesmo volume), MensagemRef (id da mensagem no WhatsApp →
  direção + texto, e a reação atual dela). O telefone é chave em 7 tabelas — trocar o
  número de um contato passa por `db.mover_contato` (ver "Ficha de cadastro"),
  nunca por UPDATE em uma tabela só.
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
  - **A tela mostra as MESMAS bolhas do WhatsApp**: `historico_para_bolhas`
    quebra a resposta do bot com `agente.dividir_bolhas` (a função do envio —
    mudou de `whatsapp.py` para `agente.py` para os dois lados usarem a mesma
    regra) e o turno do cliente com `dividir_lote_do_cliente` (só o `[quebrar]`
    do debounce; `\n` dentro de uma mensagem dele é quebra de linha).
  - **Autoria da bolha do bot**: `ModelResponse.model_name` guarda de quem foi
    a mão (metadado, o modelo não lê) — `MODELO_ENVIO_MANUAL` p/
    `registrar_na_memoria(..., origem="painel")` e `MODELO_ENVIO_APARELHO` p/
    `origem="aparelho"`. A bolha sai com `origem` "ia" | "painel" | "aparelho"
    → etiquetas "Automatizado" (acento), "Enviado por você" e "Enviado pelo
    celular" (ouro). Na lista, o preview troca "Bot:" por "Você:".
  - **Mídia e reação na bolha**: `GET /admin/midia/{id}` (rota autenticada,
    `<img>` manda o cookie junto; `midia.caminho` corta travessia de diretório)
    serve o arquivo, e `admin._anexar_extras` casa `Midia` e `MensagemRef`
    (reações) com a bolha por (direção, texto) na ordem de chegada. Imagem/vídeo/figurinha/áudio
    aparecem no player; documento vira link. Com mídia a bolha mostra a
    legenda no lugar do marcador — menos no áudio, onde o texto é a transcrição.
  - **Mensagem recém-chegada**: só vira memória quando o agente termina, então
    `whatsapp.mensagens_pendentes()` expõe o buffer do debounce + o lote
    `_em_voo` (com o agente) e o painel pinta como bolha `pendente`
    ("recebida agora", tracejada) — sem buraco entre receber e responder.
  - Poll: lista 6s, modal 2,5s. Repintar só quando a resposta muda
    (assinatura JSON em `mudou()`) — sem piscar nem perder a rolagem.
- **Quadro de atendimento** (seção "Quadro", `partials/kanban.html` +
  `js/kanban.js`, poll 6s — é a seção que o painel abre, `INICIAL` em
  `nav.js`): um card por contato, na coluna do passo em que o bot está com ele.
  `GET /admin/kanban/estado` monta tudo em `admin._quadro_estado` a partir de
  `_resumo_conversas()` (que agora devolve também `atualizado_em` e
  `respondendo`) + agendamentos ativos. **A coluna não é campo gravado**: sai do
  estado real, por isso não há arrastar — mover card à mão só faria o quadro
  mentir. Ordem das regras em `COLUNAS_QUADRO`/`_quadro_estado`:
  1. `Vez do bot` — o cliente falou por último (ou tem lote no debounce/agente,
     que vira "respondendo…"). Ganha até de quem tem horário marcado: o bot
     está devendo resposta AGORA.
  2. `Agendado` — tem agendamento ativo futuro (ordena pela agenda).
  3. `Atendido` — o último agendamento passou há menos de `kanban_atendido_dias`.
  4. `Esperando o cliente` — o bot falou por último.
  Contato sem conversa e sem agenda não entra (não há passo nenhum).
  - **Fechar atendimento** é a coluna 2 na ordem das regras (`pend or feito`):
    horário que passou e ninguém disse o que aconteceu. O par de botões
    (Compareceu / Faltou) mora no CARD, não na coluna — quem mandou mensagem
    depois está em "Vez do bot" e o dono fecha de lá também. "Compareceu"
    troca os botões pelo formulário no lugar (valor já preenchido com o preço
    do serviço, chips de forma de pagamento com a última lembrada em
    localStorage, chave "Já recebido"); "Faltou" passa pela confirmação e não
    lança nada. Enquanto o formulário está aberto o poll PARA (repintar
    apagaria o que está sendo digitado). O card fechado vira recibo (`✓
    Compareceu · R$ 35,50 · Pix`), fica apagado no pé da coluna pelos dias de
    `kanban_atendido_dias` e leva **Desfazer**.
  - **O que é "esfriado" é do dono** (`Config.kanban_*`, ALTER em `_migrar`,
    form "Ajustes do quadro" → `POST /admin/kanban/ajustes`):
    `kanban_janela_dias` (7) tira do quadro quem parou há mais tempo — menos
    quem tem horário marcado, que é informação viva; `kanban_esfria_h` (24)
    esfria quem está em "Esperando o cliente"; `kanban_travado_min` (5) vira
    alerta em "Vez do bot"; `kanban_atendido_dias` (2); e
    `kanban_mostrar_esfriadas` traz os esfriados de volta, apagados no pé da
    coluna. O que ficou de fora aparece contado na barra ("3 conversas
    esfriadas estão fora").
  - **`--calor` (0→1)**: quanto do limite da coluna o card já gastou. O fio da
    esquerda e o relógio vão do acento da coluna ao oxblood
    (`color-mix(... calc(var(--calor) * 100%) ...)`), e a coluna ordena por
    tempo parado — quem espera mais sobe. É o que faz o quadro ser triagem e
    não lista em quatro pedaços.
  - Card leva a `window.abrirConversa` (clique no topo) e a
    `window.abrirNovoAgendamento` ("Agendar"); badge do menu = quem espera
    você (bot travado ou conversa pausada).
  - **DOIS relógios** (já quebrou: agendamento de hoje 13:30 caiu em
    "Atendido"). O container roda em UTC e é esse relógio que escreve
    `Conversa.atualizado_em` — então idade de conversa se mede com
    `datetime.now()`. Já `Agendamento.inicio` é hora local do negócio, então
    tudo que compara com a AGENDA (passado/futuro, "hoje") usa
    `tools._agora_local()` (fuso da Config). Misturar os dois dá exatamente o
    offset do fuso.
- **Provedores de IA no painel**: `GET /admin/ia/estado`, `GET /admin/ia/modelos`,
  `POST /admin/ia/modelos-preview` (chave transiente), `POST /admin/ia/credencial`,
  `POST /admin/ia/modelo`.
- **Instruções do agente**: `GET/POST /admin/agente/prompt` (SQLite direto).
- **Agenda ao vivo** (`js/agendamentos.js`, poll 8s): `GET /admin/agendamentos/
  estado` devolve `{total, linhas}` — `linhas` é HTML, renderizado do MESMO
  partial `agendamentos_linhas.html` da carga inicial (montar a linha de novo em
  JS deixaria dois markups — form de reagendar, `data-confirmar` do
  cancelamento — para manter em sincronia). O JS compara a string com a anterior
  e só troca o `<tbody>` quando muda: agendamento que o bot marcou no WhatsApp
  aparece sozinho, sem F5. Ao repintar, o form de reagendar que estiver ABERTO é
  preservado (data digitada + "avisar cliente"), os avatares são pintados de
  novo (`avatars.js` exporta `pintarAvatares(raiz)`, cache por número) e o
  contador/badge do menu acompanham. Aba em segundo plano não gasta poll
  (`document.hidden`; volta a atualizar no `visibilitychange`).
  - **Filtros** — desfecho na linha de cima (`ag-filtros`: Ativos (padrão) ·
    Concluídos · Faltas · Cancelados · Todos) e o refino na de baixo
    (`ag-refino`): período (chips Tudo/Hoje/Semana/Mês **ou** o par de datas —
    data digitada ganha do atalho, e um sempre limpa o outro), serviço e busca
    por nome/telefone (debounce de 250 ms, casa também só os dígitos). Tudo
    vira query string do MESMO endpoint (`periodo|de|ate|servico|q`) — nada é
    filtrado no navegador, então contador e linhas nunca discordam. O atalho de
    período é resolvido no SERVIDOR (`_intervalo_periodo`, fuso da Config):
    "hoje" é o hoje do negócio, não o do celular que abriu o painel. Com filtro
    ligado a lista vazia diz "Nada encontrado com esses filtros" (e não que não
    existe nada) e aparece o "Limpar filtros".
    O JS manda `?status=` para o mesmo endpoint;
    `admin._contexto_agenda` monta o contexto do partial — ativos ordenados
    pelos próximos primeiro (fila de trabalho), histórico do mais recente para
    o mais antigo. Linha ativa é operável (reagendar/cancelar); linha com
    desfecho vira leitura: pill do status, valor e forma vindos do `Lancamento`
    ligado (nunca recalcula preço), "a receber" quando não pago, e Desfazer.
    O badge do menu conta SEMPRE os ativos (campo `ativos` na resposta), senão
    mudaria só por alguém ter ido olhar os cancelados.
  - **Ação do painel não recarrega mais a página**: form com `data-sem-reload`
    (cancelar e reagendar) faz o `forms.js` trocar o `location.reload()` por um
    toast — o texto do atributo é a confirmação, já que sem reload o toast é o
    único sinal de sucesso — e disparar o evento `painel:atualizar`, que o
    módulo da agenda escuta para repintar na hora. O modal de novo agendamento
    dispara o mesmo evento. **Efeito colateral conhecido**: a seção Clientes
    continua vindo pronta do servidor, então contato criado por um agendamento
    novo só aparece lá no próximo carregamento da página.
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
- **Conclusão do atendimento + base do financeiro**: `Agendamento.status` agora
  é `ativo · concluido · faltou · cancelado` (+ `concluido_em`, ALTER em
  `_migrar`). O desfecho é SEMPRE informado — nada de concluir por decurso de
  prazo: dinheiro não se marca como recebido pelo relógio. `db.concluir_
  agendamento(id, compareceu, agora_iso, valor, forma, pago)` grava o status e,
  só quando compareceu e há valor, cria o `Lancamento`; `db.reabrir_
  agendamento` desfaz os dois (é a janela de correção do clique errado).
  `_conflita` passou a considerar `concluido`/`faltou` como ocupados — o
  horário aconteceu.
  - **`Lancamento` é tabela separada do agendamento** de propósito: caixa tem
    dinheiro que não é atendimento (produto, gorjeta, aluguel, insumo), um
    atendimento pode virar mais de um lançamento (serviço + produto, pagamento
    dividido) e estorno é lançamento novo, não reescrita do histórico. `valor`
    sempre positivo (o sinal vem de `tipo` receita/despesa); `data` é a
    COMPETÊNCIA (dia do atendimento) e `criado_em` é quando registraram —
    fechar hoje a agenda de ontem cai no caixa de ontem. O valor é CÓPIA do
    preço no fechamento, nunca join com `Servico`: subir preço em março não
    pode reescrever o que entrou em janeiro. `db.FORMAS_PAGAMENTO` /
    `CATEGORIAS_LANCAMENTO`, `listar_lancamentos(de, ate)` e `resumo_caixa`
    já existem — a seção Caixa (fase 2) só precisa de tela.
  - Rotas: `POST /admin/agendamento/{id}/concluir` (form `compareceu`,
    `valor` — aceita "45,50" via `_valor_brl` —, `forma`, `pago`) e
    `/{id}/reabrir`. Tools do dono: `concluir_atendimento` (sem `valor` usa o
    preço do serviço; 0 = cortesia; `pago=false` = a receber) e
    `reabrir_atendimento`; `ver_agenda_completa` devolve também `a_fechar`.
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
  - **UI** (`partials/horarios.html` + `js/horarios.js`): no topo a **régua da
    semana** — sete trilhos numa escala de horas desenhada pelo JS a cada tecla
    (a escala se ajusta ao que a grade ocupa; dia fechado vira hachura). É a
    única leitura da semana inteira; `aria-hidden`, porque repete o form.
    Abaixo, uma linha por dia: chave liga/desliga + total do dia + os
    intervalos como pastilhas (par de `input[type=time]`, sem o reloginho do
    Chrome) + "+ intervalo" + "copiar para…" (popover com chips dos outros
    dias, atalhos "dias úteis"/"todos").
  - Fechar um dia NÃO apaga os intervalos: o JS só marca os inputs
    `disabled` — input disabled não vai no POST, e o dia volta intacto ao
    religar. Reabrir dia sem intervalo herda a jornada do dia aberto mais
    próximo. O envio segue sendo o form clássico (trincas paralelas).
  - Barra de salvar grudada no rodapé da seção: diz se há mudança pendente
    ("Alterações não salvas."), com Descartar (reload) e Salvar; `beforeunload`
    segura a saída acidental. Validação client-side ANTES do POST (hora cheia,
    fim > início, sem sobreposição) marca a pastilha e a barra em oxblood e
    manda o motivo no toast — o servidor continua validando igual.
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
    `Conversa` (memória, chave = `digitos@s.whatsapp.net`), `Midia`,
    `Agendamento.
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
- **Guia de primeiros passos** (`partials/onboarding.html` + `js/onboarding.js`):
  sobreposição que abre UMA vez, na instalação nova. 5 passos na ordem em que
  o sistema depende deles — conectar WhatsApp (QR ao vivo, poll 4s), cadastrar
  um serviço, horário de atendimento, nome do negócio + telefone do dono
  (mesmo POST `/admin/config`; o nome vai para o cabeçalho do prompt), chave de IA — e uma
  tela final com o que ficou pendente. Não tem endpoint próprio de gravação:
  cada passo POSTa no MESMO lugar que a seção correspondente
  (`/admin/servico`, `/admin/horarios`, `/admin/config`,
  `/admin/ia/credencial` + `/admin/ia/modelo`). O passo de horário é a versão
  enxuta da grade: sete chips de dia + dois turnos que valem para todos os
  dias marcados (a grade fina, por dia, fica na seção Horários). Nenhum dia
  marcado = passo pulado, porque `/admin/horarios` é replace-all e um POST
  vazio apagaria o padrão semeado no primeiro boot. Passo em branco é pulado;
  "Pular por agora" fecha tudo. Cada passo veste o `data-accent` da seção que
  configura, então o guia já ensina o código de cor da barra lateral.
  Estado: `Config.onboarding_visto` — o ALTER em `_migrar` entra com
  **DEFAULT 1** de propósito (banco que já existe é instalação em uso e não
  deve ver o guia; instalação nova nasce com o default do modelo, False).
  `POST /admin/onboarding/concluir` marca visto (serve p/ concluir e p/ pular)
  e `POST /admin/onboarding/refazer` reabre — botão "Abrir o guia" no pé da
  Configuração geral.
- **UI do painel**: barra lateral fixa + área de conteúdo (sem grade
  arrastável — Gridstack/`grade.js`/`gear.js` removidos). Conteúdo **sem caixa
  interna**: `.card` é só bloco de espaçamento (nada de borda/fundo/sombra) —
  o topo já emoldura a seção. Seção de bloco único usa `.bloco-barra` (chips à
  esquerda, ação/busca à direita, sem `h2` que repetiria o título do topo);
  seção com 2+ blocos (Serviços, Bloqueios, Agente) mantém `.card-head` com
  `h2` + fio. `.col2` separa por fio vertical (horizontal quando empilha).
  Campos e superfícies internas usam `--card` sobre o papel da página.
  **Todo `input[type=checkbox]` é um slider liga/desliga** (regra global no
  `admin.css`: `appearance:none` + `::after` como botão; ligado veste o
  `--accent` da seção). O input segue nativo — label, form, teclado, `:checked`
  continuam valendo, nada de markup novo. Medidas em `--sw-w`/`--sw-h`/
  `--sw-pad`: onde a linha é miúda (reagendar, topo do modal de conversa) só
  redefinir essas variáveis.
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
  de forms POST (`js/forms.js`, opt-out `data-nativo`; `data-sem-reload="texto"`
  troca o reload por toast + evento `painel:atualizar`) + validação
  (`js/validar.js`) + máscara/checagem de telefone (`js/telefone.js`). Modais
  (conversas, agendamento) são realocados pro `body` — a view de origem fica
  `display:none` quando outra seção está aberta.
- **Confirmação** (`js/confirmar.js`): não existe mais `confirm()` nativo no
  painel. `confirmar({titulo, texto, nota, acao, recusa, perigo})` devolve
  Promise<bool> e desenha um `<dialog>` (foco preso, Esc e camada de topo vêm
  do browser). `perigo` (padrão nos forms) veste oxblood e nasce com o foco no
  botão de sair; a `.cfm-nota` é a faixa que diz o estrago concreto. Form
  declara sem escrever JS: `data-confirmar` (pergunta) + `-texto`/`-nota`/
  `-acao`, e `data-confirmar-seguro` desliga o tom de perigo. O listener é em
  CAPTURE e dá `stopPropagation`, então o `forms.js` só vê o submit depois do
  "sim" (`requestSubmit` remarcado com `data-confirmado`). Segunda pergunta
  encadeada: `data-confirmar2*` + `data-confirmar2-campo`, o input escondido
  que recebe "1"/"" — é assim que o cancelamento pergunta se avisa o cliente.
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
