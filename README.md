<div align="center">

<br>

# MyAutoAtend

<img src="assets/banner_readme.png" alt="Banner do myAutoAtend" width="1600">

### Agendamento de serviços por IA no WhatsApp

Atendente virtual que conversa, agenda, remarca e cancela — sozinho.
Serve qualquer negócio com hora marcada: **clínicas · petshops · salões · consultórios · estúdios**.

<br>

![Setup](https://img.shields.io/badge/setup-1_comando-8a2a2f?style=for-the-badge&labelColor=211c16)
![License](https://img.shields.io/badge/license-MIT-b8862f?style=for-the-badge&labelColor=211c16)

![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white&labelColor=211c16)
![n8n](https://img.shields.io/badge/n8n-EA4B71?logo=n8n&logoColor=white&labelColor=211c16)
![Evolution API](https://img.shields.io/badge/Evolution_API-25D366?logo=whatsapp&logoColor=white&labelColor=211c16)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?logo=openai&logoColor=white&labelColor=211c16)
![FastMCP](https://img.shields.io/badge/FastMCP-8a2a2f?labelColor=211c16)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white&labelColor=211c16)
![Redis](https://img.shields.io/badge/Redis-DC382D?logo=redis&logoColor=white&labelColor=211c16)
![Claude](https://img.shields.io/badge/co--autoria-Claude_%28Anthropic%29-D97757?logo=claude&logoColor=white&labelColor=211c16)

<br>
<br>

</div>

<br>

---

## ⌖ O que você ganha

| | |
|---|---|
| 💬 **Atende no WhatsApp** | Texto, áudio (transcrição) e imagem. Conversa natural, lembra do contexto. |
| 📅 **Gerencia a agenda** | Agenda, remarca e cancela. Bloqueia horários e fecha datas. |
| 🛠️ **Painel do dono** | Em `/admin`: cadastre serviços, veja agendamentos e opere tudo no navegador. |
| 🔌 **Sobe sozinho** | Um `docker compose up` provisiona, configura e publica tudo. |

<br>

## ⚙ Configuração

> **Pré-requisitos:** [Docker](https://docs.docker.com/get-docker/) + Docker Compose v2 · uma chave da [OpenAI](https://platform.openai.com/api-keys) · portas `5678`, `8000` e `9090` livres.

### 1 · Clonar

```bash
git clone https://github.com/iurijw/MyAutoAtend.git
cd MyAutoAtend
```

### 2 · Definir login e senha (`.env`)

```bash
cp .env.example .env
```

O `.env` tem **apenas duas variáveis**, usadas em todos os portais:

```env
LOGIN=voce@exemplo.com
SENHA=TroqueEsta123
```

> A senha precisa ter 8+ caracteres, 1 letra maiúscula e 1 número.
> Defina antes do primeiro `up` — trocar depois exige resetar os volumes.

### 3 · Subir

```bash
docker compose up -d
```

A primeira inicialização leva alguns minutos. Acompanhe (se quiser):

```bash
docker logs -f n8n
```

Espere por: `✔ Workflow "Agente Whatsapp" importado e publicado!`

### 4 · Configurar pelo painel

Todo o resto acontece em **http://localhost:8000/admin** (entre com `LOGIN` / `SENHA`):

1. **📲 Conectar o WhatsApp** — no card *Conexão WhatsApp*, escaneie o QR Code com o celular do número que vai atender.
2. **🧠 Chave de IA** — no card *Provedores de IA*, cole sua chave (OpenAI ou outro provedor) e escolha os modelos de texto, áudio e imagem. *Sem isso o atendente não responde.*
3. **📞 Seu número** — em *Configuração geral*, informe o telefone do dono (autoriza comandos de gestão pelo WhatsApp).
4. **🛠 Serviços** — cadastre os serviços com preço e duração.
5. **✍️ Instruções do agente** *(opcional)* — ajuste a personalidade e as regras do atendente no card *Instruções do Agente*; já vem um padrão pronto.

**Pronto** — o atendente já responde no WhatsApp.

<br>

## ▦ Painéis

| Painel | Endereço | Para quê |
|---|---|---|
| **Admin** (agendamentos) | http://localhost:8000/admin | Serviços, agenda, WhatsApp, IA |
| **n8n** | http://localhost:5678 | Editar o fluxo do agente |
| **Evolution API** | http://localhost:9090 | Gerenciar instâncias WhatsApp |

> 🔑 Todos usam o mesmo acesso do `.env`: `LOGIN` / `SENHA` (na Evolution, a apikey é a `SENHA`).
>
> ⚠️ O painel `/admin` não foi projetado para ser exposto na internet — use apenas em `localhost`.

### Por dentro do `/admin`

<details>
<summary><b>📲 Conexão WhatsApp</b> — pareie pelo QR Code sem sair do painel</summary>
<br>
<img src="assets/conexao_via_qr_code_whatsapp.png" alt="Card Conexão WhatsApp com QR Code" width="860">
</details>

<details>
<summary><b>🧠 Provedores de IA</b> — provedor, chave e modelo por uso: texto · áudio · imagem</summary>
<br>
<img src="assets/definicao_de_provedores_ia.png" alt="Card Provedores de IA com os três blocos" width="860">
</details>

<details>
<summary><b>✍️ Instruções do Agente</b> — edite a personalidade do atendente e republique na hora</summary>
<br>
<img src="assets/instrucoes_do_agente.png" alt="Card Instruções do Agente" width="860">
</details>

<br>

## ⌁ Comandos úteis

```bash
docker compose up -d        # subir tudo
docker compose down         # parar (mantém os dados)
docker compose down -v      # parar e APAGAR todos os dados
docker logs -f n8n          # ver a inicialização
```

<br>

## Todo

- [X] Adicionar novas opções de provedores de IA (chave/modelo atualizados no n8n pelo portal admin; fluxo unidirecional — a chave nunca é exibida de volta)
- [X] Transferir lógica de pareamento do Whatsapp para o portal admin
- [X] Adicionar shortchuts para abertura dos portais auxiliares (Evolution API e n8n) no portal admin
- [X] Visualizacao de agendamentos com foto e número do Whatsapp no portal do admin
- [ ] Poder bloquear/fechar range de datas pelo portal do admin e conversa com o bot no WhatsApp
- [X] Adicionar alguma forme de controlar o contexto do agente no nodo do n8n pelo portal do admin, atualmente definido na inicialização via .env (AGENT_SYSTEM_PROMPT) — card "Instruções do Agente": instrução geral + bloco MCP separado (avançado, com aviso e restauração do padrão)
- [ ] Adicionar opção de avisar o cliente que foi reagendado/cancelado o serviço dele (a IA avisar a ação do dono/adminsitrador com o aval do mesmo)
- [ ] Adicionar a opção de ativar (no painel de admin ou pedindo para o bot no whatsapp) o aviso para o Whatsapp do dono/administrador de agendamentos/cancelamentos, etc...

<br>

---

<div align="center">

Construído com **Docker · n8n · Evolution API · OpenAI · FastMCP · PostgreSQL · Redis**

<sub>Licença MIT</sub>

</div>
