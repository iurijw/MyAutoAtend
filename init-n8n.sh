#!/bin/sh
# =============================================================================
# Script de inicialização do n8n
# Instala community nodes e cria credenciais automaticamente
# Usa Node.js para HTTP (BusyBox wget não suporta cookies)
# Compatível com n8n 2.x
# =============================================================================

set -e

# ---------------- Instalar community node ----------------
echo "[init] Instalando n8n-nodes-evolution-api..."
mkdir -p /home/node/.n8n/nodes
cd /home/node/.n8n/nodes
npm install n8n-nodes-evolution-api 2>/dev/null || echo "[init] Community node já instalado ou erro"

# ---------------- Iniciar n8n em background ----------------
echo "[init] Iniciando n8n..."
n8n start &
N8N_PID=$!

# ---------------- Aguardar n8n ficar pronto ----------------
echo "[init] Aguardando n8n ficar pronto..."
MAX_ATTEMPTS=60
ATTEMPT=0
until wget -qO- http://localhost:5678/healthz >/dev/null 2>&1; do
  ATTEMPT=$((ATTEMPT + 1))
  if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
    echo "[init] ERRO: n8n não iniciou após ${MAX_ATTEMPTS} tentativas."
    wait $N8N_PID
    exit 1
  fi
  sleep 3
done
echo "[init] n8n está pronto!"

# ---------------- Verificar se já foi inicializado ----------------
INIT_MARKER="/home/node/.n8n/.credentials_initialized"
if [ -f "$INIT_MARKER" ]; then
  echo "[init] Credenciais já foram criadas anteriormente. Pulando setup."
  wait $N8N_PID
  exit 0
fi

# ---------------- Criar credenciais via Node.js ----------------
echo "[init] Criando credenciais via Node.js..."

cat > /tmp/create_credentials.js << 'JSEOF'
const http = require('http');
const fs   = require('fs');

const INIT_MARKER = '/home/node/.n8n/.credentials_initialized';

/* ---------- HTTP helpers ---------- */
function httpRequest(method, path, data, cookie) {
  return new Promise((resolve, reject) => {
    const body = data ? JSON.stringify(data) : '';
    const headers = { 'Content-Type': 'application/json' };
    if (body) headers['Content-Length'] = Buffer.byteLength(body);
    if (cookie) headers['Cookie'] = cookie;

    const req = http.request(
      { hostname: 'localhost', port: 5678, path, method, headers },
      (res) => {
        let buf = '';
        res.on('data', c => buf += c);
        res.on('end', () => resolve({
          status:  res.statusCode,
          body:    buf,
          cookies: res.headers['set-cookie'] || [],
        }));
      }
    );
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

const get   = (path) => httpRequest('GET',   path, null, null);
const post  = (path, data, cookie) => httpRequest('POST',  path, data, cookie);
const patch = (path, data, cookie) => httpRequest('PATCH', path, data, cookie);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function extractAuthCookie(cookies) {
  for (const c of cookies) {
    if (c.includes('n8n-auth=')) return c.split(';')[0].trim();
  }
  return null;
}

/* ---------- Verifica estado do n8n via /rest/settings ---------- */
async function needsOwnerSetup() {
  for (let i = 1; i <= 20; i++) {
    try {
      const res = await get('/rest/settings');
      if (res.status === 200) {
        const settings = JSON.parse(res.body);
        const needs = settings.data.userManagement.showSetupOnFirstLoad === true;
        console.log('[init] showSetupOnFirstLoad: ' + needs);
        return needs;
      }
    } catch (e) { /* retry */ }
    console.log('[init] Aguardando settings... tentativa ' + i);
    await sleep(2000);
  }
  throw new Error('Não foi possível obter /rest/settings');
}

/* ---------- Criar owner (banco limpo) ---------- */
async function setupOwner() {
  for (let i = 1; i <= 20; i++) {
    try {
      const res = await post('/rest/owner/setup', {
        email:     process.env.N8N_OWNER_EMAIL,
        firstName: process.env.N8N_OWNER_FIRST_NAME,
        lastName:  process.env.N8N_OWNER_LAST_NAME,
        password:  process.env.N8N_OWNER_PASSWORD,
      });

      if (res.status === 200) {
        const cookie = extractAuthCookie(res.cookies);
        if (cookie) {
          console.log('[init] Owner criado com sucesso!');
          return cookie;
        }
        console.log('[init] Setup retornou 200 mas sem cookie. Body: ' + res.body.substring(0, 150));
      } else {
        console.log('[init] Setup tentativa ' + i + '/20 — HTTP ' + res.status + ', aguardando 3s...');
      }
    } catch (e) {
      console.log('[init] Setup tentativa ' + i + '/20 — erro: ' + e.message);
    }
    await sleep(3000);
  }
  return null;
}

/* ---------- Login (banco existente) ---------- */
async function login() {
  for (let i = 1; i <= 20; i++) {
    try {
      const res = await post('/rest/login', {
        emailOrLdapLoginId: process.env.N8N_OWNER_EMAIL,
        password:           process.env.N8N_OWNER_PASSWORD,
      });

      if (res.status === 200) {
        const cookie = extractAuthCookie(res.cookies);
        if (cookie) {
          console.log('[init] Login bem-sucedido!');
          return cookie;
        }
      }

      const wait = res.status === 429 ? 15000 : 5000;
      console.log('[init] Login tentativa ' + i + '/20 — HTTP ' + res.status + ', aguardando ' + (wait/1000) + 's...');
      await sleep(wait);
    } catch (e) {
      console.log('[init] Login tentativa ' + i + '/20 — erro: ' + e.message);
      await sleep(5000);
    }
  }
  return null;
}

/* ---------- Evolution API helpers ---------- */
function evoRequest(method, path, data) {
  return new Promise((resolve, reject) => {
    const EVOLUTION_HOST = process.env.EVOLUTION_API_URL.replace(/^https?:\/\//, '').split(':')[0];
    const EVOLUTION_PORT = parseInt(process.env.EVOLUTION_API_URL.replace(/^https?:\/\/[^:]+:?/, '') || '9090', 10);
    const body = data ? JSON.stringify(data) : '';
    const headers = {
      'Content-Type': 'application/json',
      'apikey': process.env.EVOLUTION_API_KEY,
    };
    if (body) headers['Content-Length'] = Buffer.byteLength(body);

    const req = http.request(
      { hostname: EVOLUTION_HOST, port: EVOLUTION_PORT, path, method, headers },
      (res) => {
        let buf = '';
        res.on('data', c => buf += c);
        res.on('end', () => resolve({ status: res.statusCode, body: buf }));
      }
    );
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

/* ---------- Aguarda Evolution API ficar pronta ---------- */
async function waitForEvolution() {
  for (let i = 1; i <= 30; i++) {
    try {
      const res = await evoRequest('GET', '/instance/fetchInstances', null);
      if (res.status === 200) {
        console.log('[init] Evolution API está pronta!');
        return true;
      }
    } catch (e) { /* retry */ }
    console.log('[init] Aguardando Evolution API... tentativa ' + i);
    await sleep(3000);
  }
  return false;
}

/* ---------- Cria instância evo_n8n se não existir ---------- */
async function setupEvolutionInstance() {
  const INSTANCE_NAME = 'evo_n8n';

  // Verificar se instância já existe
  const listRes = await evoRequest('GET', '/instance/fetchInstances', null);
  if (listRes.status === 200) {
    const instances = JSON.parse(listRes.body);
    const exists = instances.some(i => i.name === INSTANCE_NAME);
    if (exists) {
      console.log('[init] Instância ' + INSTANCE_NAME + ' já existe. Pulando criação.');
      return;
    }
  }

  console.log('[init] Criando instância ' + INSTANCE_NAME + '...');

  // Criar instância
  const createRes = await evoRequest('POST', '/instance/create', {
    instanceName: INSTANCE_NAME,
    integration: 'WHATSAPP-BAILEYS',
    qrcode: true,
  });
  console.log('[init] Criar instância → HTTP ' + createRes.status + ' ' + createRes.body.substring(0, 200));

  if (createRes.status !== 201 && createRes.status !== 200) {
    console.log('[init] AVISO: Falha ao criar instância. Pulando configuração do webhook.');
    return;
  }

  await sleep(2000);

  // Configurar settings da instância
  const settingsRes = await evoRequest('POST', '/settings/set/' + INSTANCE_NAME, {
    rejectCall: false,
    msgCall: '',
    groupsIgnore: false,
    alwaysOnline: false,
    readMessages: false,
    readStatus: false,
    syncFullHistory: false,
  });
  console.log('[init] Settings → HTTP ' + settingsRes.status);

  // Configurar webhook
  const webhookRes = await evoRequest('POST', '/webhook/set/' + INSTANCE_NAME, {
    webhook: {
      url: 'http://n8n:5678/webhook/whatsapp/receberMensagem',
      enabled: true,
      webhookByEvents: false,
      base64: true,
      events: ['MESSAGES_UPSERT'],
    },
  });
  console.log('[init] Webhook → HTTP ' + webhookRes.status);

  console.log('[init] ✔ Instância ' + INSTANCE_NAME + ' criada e configurada!');
  console.log('[init] ────────────────────────────────────────────────────────');
  console.log('[init]  AÇÃO NECESSÁRIA: Conecte o WhatsApp manualmente.');
  console.log('[init]  Use o painel da Evolution API para escanear o QR Code.');
  console.log('[init] ────────────────────────────────────────────────────────');
}

/* ---------- Importar e ativar workflow ---------- */
async function importWorkflow(cookie, evolCredId, openAiCredId, redisCredId) {
  try {
    let workflowJson = fs.readFileSync('/agente_whatsapp.json', 'utf8');
    workflowJson = workflowJson
      .replace(/oCZwvvYltMxJIzmA/g, evolCredId)
      .replace(/MU6adeGic3RPMvdM/g, openAiCredId)
      .replace(/trFJWRaDpUKn5nf8/g, redisCredId);
    const workflow = JSON.parse(workflowJson);
    delete workflow.id;
    delete workflow.meta;
    const res = await post('/rest/workflows', workflow, cookie);
    console.log('[init] Importar workflow → HTTP ' + res.status + ' ' + res.body.substring(0, 300));
    if (res.status === 200 || res.status === 201) {
      const body = JSON.parse(res.body);
      const workflow = body.data || body;
      const workflowId = workflow.id;
      const versionId = workflow.versionId;
      const actRes = await post('/rest/workflows/' + workflowId + '/activate', { versionId }, cookie);
      console.log('[init] Publicar workflow → HTTP ' + actRes.status + ' ' + actRes.body.substring(0, 200));
      if (actRes.status === 200) {
        console.log('[init] ✔ Workflow "Agente Whatsapp" importado e publicado!');
      } else {
        console.log('[init] AVISO: Workflow criado mas não foi publicado automaticamente. Publique manualmente no n8n.');
      }
    }
  } catch (e) {
    console.log('[init] AVISO: Não foi possível importar o workflow: ' + e.message);
  }
}

/* ---------- main ---------- */
async function main() {
  let cookie = null;

  const needs = await needsOwnerSetup();

  if (needs) {
    console.log('[init] Banco limpo detectado — criando owner...');
    cookie = await setupOwner();
  } else {
    console.log('[init] Owner já existe — fazendo login...');
    cookie = await login();
  }

  if (!cookie) {
    console.error('[init] ERRO: Não foi possível obter sessão.');
    console.error('[init] Verifique N8N_OWNER_EMAIL e N8N_OWNER_PASSWORD no docker-compose.');
    process.exit(0);
  }

  // Criar credencial Evolution API
  const evolRes = await post('/rest/credentials', {
    name: 'Evolution API',
    type: 'evolutionApi',
    data: {
      "server-url": process.env.EVOLUTION_API_URL,
      apikey: process.env.EVOLUTION_API_KEY,
    },
  }, cookie);
  console.log('[init] Evolution API → HTTP ' + evolRes.status + ' ' + evolRes.body.substring(0, 200));
  let evolCredId = null;
  try { evolCredId = (JSON.parse(evolRes.body).data || JSON.parse(evolRes.body)).id; } catch(e) {}

  // Criar credencial OpenAI
  const openAiRes = await post('/rest/credentials', {
    name: 'OpenAi account',
    type: 'openAiApi',
    data: {
      apiKey: process.env.OPENAI_API_KEY,
    },
  }, cookie);
  console.log('[init] OpenAI → HTTP ' + openAiRes.status + ' ' + openAiRes.body.substring(0, 200));
  let openAiCredId = null;
  try { openAiCredId = (JSON.parse(openAiRes.body).data || JSON.parse(openAiRes.body)).id; } catch(e) {}

  // Criar credencial Redis
  const redisRes = await post('/rest/credentials', {
    name: 'Redis',
    type: 'redis',
    data: {
      host:     process.env.REDIS_HOST,
      port:     parseInt(process.env.REDIS_PORT, 10),
      database: 0,
    },
  }, cookie);
  console.log('[init] Redis → HTTP ' + redisRes.status + ' ' + redisRes.body.substring(0, 200));
  let redisCredId = null;
  try { redisCredId = (JSON.parse(redisRes.body).data || JSON.parse(redisRes.body)).id; } catch(e) {}

  // Marcar como inicializado (credenciais n8n)
  fs.writeFileSync(INIT_MARKER, new Date().toISOString() + '\n');
  console.log('[init] Setup n8n concluído com sucesso!');

  // Importar workflow Agente Whatsapp
  if (evolCredId && openAiCredId && redisCredId) {
    await importWorkflow(cookie, evolCredId, openAiCredId, redisCredId);
  } else {
    console.log('[init] AVISO: IDs de credenciais não capturados. Importe o workflow manualmente.');
  }

  // Criar instância Evolution API
  const evoReady = await waitForEvolution();
  if (evoReady) {
    await setupEvolutionInstance();
  } else {
    console.log('[init] AVISO: Evolution API não respondeu. Instância não foi criada.');
  }
}

main().catch(e => {
  console.error('[init] ERRO fatal:', e.message);
  process.exit(0);
});
JSEOF

node /tmp/create_credentials.js || echo "[init] Script terminou com erro, continuando..."
rm -f /tmp/create_credentials.js

# ---------------- Manter n8n rodando ----------------
wait $N8N_PID
