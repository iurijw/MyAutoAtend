/* Card "Conversas": lista ao vivo dos contatos que falaram com o bot
   (GET /admin/conversas, poll) + modal com o histórico estilo WhatsApp,
   toggle de pausa e envio manual de mensagem. */

import { toast } from './toast.js';

/* Ritmo do poll. Curto porque a conversa tem que acompanhar o WhatsApp de
   verdade; o custo de repintar é zero quando nada mudou (ver `mudou`), então
   o que sobra é uma leitura barata no SQLite local. */
const LISTA_POLL_MS = 6000;
const MODAL_POLL_MS = 2500;

const lista = document.getElementById('conversas-lista');
const tagCount = document.getElementById('conversas-count');
const live = document.getElementById('conversas-live');
const navBadge = document.getElementById('nav-badge-conversas');

const modal = document.getElementById('conv-modal');
// O modal nasce dentro da view "Conversas", que fica display:none quando outra
// seção está aberta. Realocado para o body ele abre de qualquer seção — é o que
// permite o botão "Conversa" da lista de agendamentos e da de clientes.
document.body.appendChild(modal);
const elAva = document.getElementById('conv-modal-ava');
const elNome = document.getElementById('conv-modal-nome');
const elTel = document.getElementById('conv-modal-tel');
const elPausa = document.getElementById('conv-modal-pausa');
const elMsgs = document.getElementById('conv-modal-msgs');
const form = document.getElementById('conv-modal-form');
const input = document.getElementById('conv-modal-input');
const btnEnviar = document.getElementById('conv-modal-enviar');

// Cache de fotos por número (evita rebater na Evolution a cada render/poll).
const fotos = {};
let alvo = null;          // telefone da conversa aberta no modal (E.164)
let modalTimer = null;

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

/* Repintar a cada poll pisca a tela e derruba a rolagem de quem está lendo.
   Guarda a última resposta por chave e só redesenha quando ela muda. */
const ultimo = {};
function mudou(chave, dados) {
  const agora = JSON.stringify(dados);
  if (ultimo[chave] === agora) return false;
  ultimo[chave] = agora;
  return true;
}

const QUEM_PREFIXO = { bot: 'Bot: ', sistema: 'Sistema: ' };

// Quem escreveu a mensagem do lado do bot: a IA ou o dono, pelo painel.
const prefixoDaLinha = (c) =>
  c.quem === 'bot' && c.auto === false ? 'Você: ' : (QUEM_PREFIXO[c.quem] || '');

async function pintarFoto(num, ...els) {
  if (!num) return;
  if (!(num in fotos)) {
    try {
      const r = await fetch('/admin/whatsapp/foto?numero=' + encodeURIComponent(num));
      fotos[num] = r.ok ? ((await r.json()).url || null) : null;
    } catch (_) { fotos[num] = null; }
  }
  const url = fotos[num];
  if (!url) return;
  els.forEach(el => {
    el.style.backgroundImage = "url('" + url + "')";
    el.classList.add('loaded');
  });
}

// ---------------------------------------------------------------------------
// Lista de conversas
// ---------------------------------------------------------------------------

function itemLista(c) {
  const el = document.createElement('div');
  el.className = 'conv-item' + (c.pausado ? ' is-pausado' : '');
  el.dataset.tel = c.telefone;

  const inicial = (c.nome || c.telefone || '?').trim().charAt(0).toUpperCase() || '?';
  const titulo = c.nome || c.telefone;
  const prev = c.preview ? `${prefixoDaLinha(c)}${c.preview}` : 'Sem mensagens ainda';

  el.innerHTML = `
    <button type="button" class="conv-item-open">
      <span class="ava conv-item-ava" data-num="${esc(c.telefone)}">${esc(inicial)}</span>
      <span class="conv-item-main">
        <span class="conv-item-top">
          <span class="conv-item-nome">${esc(titulo)}</span>
          <span class="conv-item-hora">${esc(c.hora || '')}</span>
        </span>
        <span class="conv-item-prev ${c.preview ? '' : 'vazio'}">${esc(prev)}</span>
      </span>
    </button>
    <button type="button" class="conv-pausa-pill ${c.pausado ? 'on' : ''}" data-tel="${esc(c.telefone)}"
      title="${c.pausado ? 'Bot pausado — clique para retomar' : 'Bot ativo — clique para pausar'}"
      aria-pressed="${c.pausado ? 'true' : 'false'}">${c.pausado ? 'pausado' : 'ativo'}</button>
  `;
  el.querySelector('.conv-item-open').addEventListener('click', () => abrir(c.telefone));
  el.querySelector('.conv-pausa-pill').addEventListener('click', ev => {
    ev.stopPropagation();
    alternarPausa(c.telefone, !c.pausado);
  });
  pintarFoto(c.telefone, el.querySelector('.conv-item-ava'));
  return el;
}

async function carregarLista() {
  let dados;
  try {
    const r = await fetch('/admin/conversas', { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(r.status);
    dados = await r.json();
  } catch (_) {
    live?.classList.add('off');
    return;
  }
  live?.classList.remove('off');
  if (!mudou('lista', dados)) return;
  const conversas = dados.conversas || [];
  tagCount.textContent = conversas.length
    ? `${conversas.length} ${conversas.length === 1 ? 'contato' : 'contatos'}`
    : 'nenhuma';
  if (navBadge) {
    navBadge.textContent = conversas.length || '';
    navBadge.hidden = !conversas.length;
  }

  lista.innerHTML = '';
  if (!conversas.length) {
    lista.innerHTML = '<p class="conv-empty">Nenhuma conversa ainda. Quando alguém falar com o bot, aparece aqui.</p>';
    return;
  }
  conversas.forEach(c => lista.appendChild(itemLista(c)));
}

async function alternarPausa(telefone, pausar) {
  const fd = new FormData();
  fd.append('pausar', pausar ? '1' : '0');
  try {
    const r = await fetch(`/admin/conversas/${encodeURIComponent(telefone)}/pausa`, {
      method: 'POST', body: fd, headers: { Accept: 'application/json' },
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      toast('erro', d.detail || 'Não foi possível mudar a pausa deste contato.');
      return;
    }
    toast('ok', pausar ? 'Bot pausado para este contato.' : 'Bot retomado para este contato.');
    if (alvo && alvo === telefone) elPausa.checked = pausar;
  } catch (_) {
    toast('erro', 'Falha de conexão ao mudar a pausa.');
  }
  carregarLista();
}

// ---------------------------------------------------------------------------
// Modal de conversa
// ---------------------------------------------------------------------------

/* Etiqueta do lado do bot: no WhatsApp as duas saem do mesmo número, então na
   tela o dono precisa ver quem escreveu — a IA ou ele mesmo, pelo painel. */
const ETIQUETA_BOT = { auto: 'Automatizado', manual: 'Enviado por você' };

function bolha(m) {
  const el = document.createElement('div');
  el.className = 'conv-bolha quem-' + (m.quem || 'cliente') + (m.pendente ? ' pendente' : '');
  let etiqueta = '';
  if (m.quem === 'bot') {
    const chave = m.auto === false ? 'manual' : 'auto';
    el.classList.add('bot-' + chave);
    etiqueta = `<span class="conv-bolha-tag">${ETIQUETA_BOT[chave]}</span>`;
  }
  el.innerHTML = etiqueta +
    `<span class="conv-bolha-txt">${esc(m.texto)}</span>` +
    (m.pendente
      ? '<span class="conv-bolha-hora">recebida agora</span>'
      : (m.hora ? `<span class="conv-bolha-hora">${esc(m.hora)}</span>` : ''));
  return el;
}

function pintarMensagens(dados) {
  if (!mudou('modal', dados)) return;
  elNome.textContent = dados.nome || dados.telefone;
  elTel.textContent = dados.telefone;
  elPausa.checked = !!dados.pausado;
  elAva.textContent = (dados.nome || dados.telefone || '?').trim().charAt(0).toUpperCase() || '?';
  elAva.style.backgroundImage = '';
  elAva.classList.remove('loaded');
  pintarFoto(dados.telefone, elAva);

  const perto = elMsgs.scrollHeight - elMsgs.scrollTop - elMsgs.clientHeight < 60;
  elMsgs.innerHTML = '';
  const msgs = dados.mensagens || [];
  if (!msgs.length) {
    elMsgs.innerHTML = '<p class="conv-msgs-vazio">Nenhuma mensagem ainda nesta conversa.</p>';
    return;
  }
  msgs.forEach(m => elMsgs.appendChild(bolha(m)));
  if (perto) elMsgs.scrollTop = elMsgs.scrollHeight;
}

async function carregarMensagens() {
  if (!alvo) return;
  try {
    const r = await fetch('/admin/conversas/' + encodeURIComponent(alvo), {
      headers: { Accept: 'application/json' },
    });
    if (!r.ok) throw new Error(r.status);
    pintarMensagens(await r.json());
  } catch (_) { /* mantém o que já está na tela */ }
}

function abrir(telefone) {
  alvo = telefone;
  ultimo.modal = null;   // outra conversa (ou a mesma reaberta) sempre repinta
  elMsgs.innerHTML = '<p class="conv-msgs-vazio">Carregando…</p>';
  input.value = '';
  modal.classList.add('open');
  document.body.classList.add('conv-modal-aberto');
  carregarMensagens().then(() => input.focus());
  clearInterval(modalTimer);
  modalTimer = setInterval(carregarMensagens, MODAL_POLL_MS);
}

function fechar() {
  modal.classList.remove('open');
  document.body.classList.remove('conv-modal-aberto');
  clearInterval(modalTimer);
  modalTimer = null;
  alvo = null;
  carregarLista();  // reflete pausa/última mensagem que possa ter mudado
}

document.getElementById('conv-modal-x').addEventListener('click', fechar);
modal.querySelector('.conv-modal-backdrop').addEventListener('click', fechar);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && modal.classList.contains('open')) fechar();
});

elPausa.addEventListener('change', () => {
  if (alvo) alternarPausa(alvo, elPausa.checked);
});

form.addEventListener('submit', async e => {
  e.preventDefault();
  const texto = input.value.trim();
  if (!texto || !alvo) return;
  btnEnviar.disabled = true;
  const fd = new FormData();
  fd.append('texto', texto);
  try {
    const r = await fetch('/admin/conversas/' + encodeURIComponent(alvo) + '/enviar', {
      method: 'POST', body: fd, headers: { Accept: 'application/json' },
    });
    if (r.ok) {
      input.value = '';
      await carregarMensagens();
    } else {
      const d = await r.json().catch(() => ({}));
      toast('erro', d.detail || 'Não foi possível enviar a mensagem.');
    }
  } catch (_) {
    toast('erro', 'Falha de conexão ao enviar a mensagem.');
  }
  btnEnviar.disabled = false;
  input.focus();
});

// Aberto direto pela lista de agendamentos (botão "Conversa" por linha).
window.abrirConversa = abrir;

carregarLista();
setInterval(carregarLista, LISTA_POLL_MS);
