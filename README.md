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
![PydanticAI](https://img.shields.io/badge/PydanticAI-E92063?logo=pydantic&logoColor=white&labelColor=211c16)
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

> **Pré-requisitos:** [Docker](https://docs.docker.com/get-docker/) + Docker Compose v2 · uma chave de algum provedor de IA (OpenAI, Anthropic, OpenRouter, etc.) · portas `8000` e `9090` livres.

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

> Para trocar a senha depois, edite o `.env` e rode `docker compose up -d` de novo.

### 3 · Subir

```bash
docker compose up -d
```

A primeira inicialização leva alguns minutos. Acompanhe (se quiser):

```bash
docker logs -f mcp_agendamentos
```

Espere por: `AÇÃO NECESSÁRIA: conecte o WhatsApp — QR Code no painel /admin.`

### 4 · Configurar pelo painel

Em **http://localhost:8000/admin** (`LOGIN` / `SENHA`):

1. **📲 WhatsApp** — escaneie o QR Code.
2. **🧠 Chave de IA** — cole a chave e escolha os modelos. *Sem isso o atendente não responde.*
3. **📞 Telefone do dono** — em *Configuração geral*.
4. **🛠 Serviços** — cadastre com preço e duração.

**Pronto** — o atendente já responde no WhatsApp.

<br>

## ▦ Painéis

| Painel | Endereço | Para quê |
|---|---|---|
| **Admin** (agendamentos) | http://localhost:8000/admin | Serviços, agenda, WhatsApp, IA |
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
docker logs -f mcp_agendamentos   # ver a inicialização e o agente
```

<br>

## ⟳ Atualizar sem perder os dados

Todos os dados (agendamentos, serviços, conversas, chaves de IA e pareamento do WhatsApp) ficam em **volumes Docker** — atualizar o código não os apaga:

```bash
git pull
docker compose pull
docker compose up -d --build
```

> ⚠️ **Nunca** use `docker compose down -v` para atualizar: o `-v` apaga os volumes (todos os dados, inclusive o pareamento do WhatsApp). Se precisar parar antes, use `docker compose down` sem `-v`.

<br>

## Todo

- [ ] Adicionar opção de cadastro manual de agendamentos no admin UI.
- [ ] Adicionar a opção de ativar (no painel admin ou pedindo ao bot pelo WhatsApp) o aviso no WhatsApp do dono/administrador sobre agendamentos, cancelamentos etc.
- [ ] Adicionar opção de avisar o cliente quando o serviço dele for reagendado/cancelado (a IA avisa a ação do dono/administrador com o aval dele).
- [ ] Botão no painel admin e comando via WhatsApp para o bot parar de responder a uma pessoa/número de WhatsApp específico.
- [ ] Opção de abrir conversa (em um modal) no painel admin para visualizar as mensagens do WhatsApp, com possibilidade de enviar mensagens manualmente pelo bot. Como dependência, deve-se criar uma nova aba de conversas (além de um botão para abrir a conversa na listagem de agendamentos).
- [ ] Mesmo com as conversas pausadas, mensagens recebidas do cliente e enviadas manualmente pelo dono/administrador devem ser colocadas na memória do agente (depende de outros to-dos).
- [ ] Adicionar ficha de cadastro do cliente, como feature opcional, em uma nova aba na UI. Os campos do cliente devem ser customizáveis, com tipos de dados próprios (str, int, data etc.). O cadastro e os campos poderão ser preenchidos pelo agente durante a conversa com o cliente (conforme instrução dada a ele). A ficha de cadastro deve ter o botão de abrir conversa (depende de outros to-dos).
- [ ] Adicionar uma pequena memória para cada cliente, com o objetivo de manter informações cruciais (nome, opções de acessibilidade etc.). Feature opcional, ativada no admin UI.
- [ ] Adicionar opção de outros provedores de speech-to-text (Whisper).
- [ ] Adicionar opção de geração de áudio pelo bot (para pessoas que não sabem ler e escrever).
- [ ] Adicionar dark mode no painel admin.
- [ ] Adicionar parte no painel admin para baixar os dados (backup geral) e opção de importá-los novamente.
- [ ] Adicionar controle financeiro dos agendamentos (uma nova seção na UI e controle pelo bot), sendo possível criar contas bancárias. Nesse contexto, criar botão para marcar o agendamento como concluído, com forma de pagamento e valor recebido (valor autopreenchido, mas ajustável, de acordo com o valor do serviço cadastrado).

<br>

---

<div align="center">

Construído com **Docker · PydanticAI · Evolution API · OpenAI · FastMCP · PostgreSQL · Redis**

<sub>Licença MIT</sub>

</div>
