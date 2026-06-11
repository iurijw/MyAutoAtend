/* Card "Instruções do Agente": system prompt (geral + bloco MCP) via painel. */

const $ = (id) => document.getElementById(id);
const card = $('prompt-card');
let mcpPadrao = '';
let carregado = false;

function msg(texto, ok){
  const el = $('prompt-msg');
  el.textContent = texto;
  el.className = 'ia-msg ' + (ok ? 'ok' : 'err');
}

async function carregar(){
  try {
    const r = await fetch('/admin/agente/prompt');
    const d = await r.json();
    if (!r.ok || d.erro) throw new Error(d.erro || ('HTTP ' + r.status));
    $('prompt-geral').value = d.geral || '';
    $('prompt-mcp').value = d.mcp || '';
    mcpPadrao = d.mcp_padrao || '';
    const pill = $('prompt-status');
    if (d.fonte === 'painel'){ pill.className = 'pill on'; pill.textContent = 'gerido pelo painel'; }
    else { pill.className = 'pill off'; pill.textContent = 'padrão'; }
  } catch(e){
    msg('Falha ao carregar as instruções: ' + e.message, false);
  }
}

async function salvar(btn){
  const geral = $('prompt-geral').value.trim();
  if (!geral){ msg('A instrução geral não pode ficar vazia.', false); return; }
  btn.disabled = true; msg('Salvando…', true);
  try {
    const r = await fetch('/admin/agente/prompt', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({ geral, mcp: $('prompt-mcp').value }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.erro) throw new Error(d.erro || d.detail || ('HTTP ' + r.status));
    msg('Instruções publicadas — o agente já responde com o novo prompt.', true);
    const pill = $('prompt-status');
    pill.className = 'pill on'; pill.textContent = 'gerido pelo painel';
  } catch(e){ msg('Falha: ' + e.message, false); }
  finally { btn.disabled = false; }
}

$('prompt-salvar').addEventListener('click', (ev) => salvar(ev.currentTarget));
$('prompt-restaurar').addEventListener('click', () => {
  if (!mcpPadrao) return;
  if (!confirm('Restaurar o bloco MCP para o texto padrão? O campo será sobrescrito (salve para publicar).')) return;
  $('prompt-mcp').value = mcpPadrao;
  msg('Bloco MCP restaurado ao padrão — clique em Salvar.', true);
});

// Card começa recolhido; busca o conteúdo só na primeira expansão.
$('prompt-head').addEventListener('click', () => {
  card.classList.toggle('collapsed');
  if (!card.classList.contains('collapsed') && !carregado){
    carregado = true;
    carregar();
  }
});
