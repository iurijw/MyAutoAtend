/* Seção "Quadro": onde cada conversa parou, ao vivo.

   Uma coluna por passo do bot (GET /admin/kanban/estado, poll de 6s). A coluna
   de um contato não é um campo gravado — sai do estado real (quem falou por
   último, o que tem marcado na agenda), então não há o que arrastar: mover um
   card à mão só faria o quadro mentir. O que o dono controla são os LIMITES,
   no form de ajustes (quando uma conversa esfria e sai daqui).

   O card mostra há quanto tempo está parado e esquenta conforme se aproxima
   do limite da coluna (--calor, 0→1): quem espera mais sobe e fica vermelho.
   É o que transforma a lista em triagem. */

import { pintarAvatares } from './avatars.js';

const POLL_MS = 6000;

const card = document.getElementById('quadro-card');
if (card) iniciar();

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function iniciar() {
  const caixa = document.getElementById('qd-colunas');
  const live = document.getElementById('qd-live');
  const total = document.getElementById('qd-total');
  const fora = document.getElementById('qd-fora');
  const navBadge = document.getElementById('nav-badge-quadro');

  const ajustes = document.getElementById('qd-ajustes');
  const abrirAjustes = document.getElementById('qd-ajustes-abrir');
  const fecharAjustes = document.getElementById('qd-ajustes-fechar');

  let ultimo = null;        // assinatura do último desenho
  let ajustesTocado = false; // não sobrescrever o que o dono está digitando

  // -------------------------------------------------------------------------
  // Ajustes do quadro
  // -------------------------------------------------------------------------
  const campos = {
    janela_dias: document.getElementById('qd-janela'),
    esfria_h: document.getElementById('qd-esfria'),
    travado_min: document.getElementById('qd-travado'),
    atendido_dias: document.getElementById('qd-atendido'),
  };

  function preencherAjustes(a) {
    if (ajustesTocado) return;
    Object.entries(campos).forEach(([k, el]) => { if (el) el.value = a[k]; });
    document.getElementById('qd-mostrar').checked = !!a.mostrar_esfriadas;
  }

  function alternarAjustes(mostrar) {
    ajustes.hidden = !mostrar;
    abrirAjustes.setAttribute('aria-expanded', String(mostrar));
    if (mostrar) campos.janela_dias?.focus();
  }

  abrirAjustes.addEventListener('click', () => alternarAjustes(ajustes.hidden));
  fecharAjustes.addEventListener('click', () => {
    ajustesTocado = false;
    alternarAjustes(false);
    carregar(true);            // descarta o que foi digitado e não salvo
  });
  ajustes.addEventListener('input', () => { ajustesTocado = true; });
  ajustes.addEventListener('submit', () => { ajustesTocado = false; });
  // forms.js manda o POST e dispara 'painel:atualizar'. Só o nosso form fecha
  // a gaveta de ajustes — o evento também chega de outras seções (a agenda),
  // e o quadro aproveita para repintar.
  document.addEventListener('painel:atualizar', e => {
    if (e.detail && e.detail.form === ajustes) alternarAjustes(false);
    carregar(true);
  });

  // -------------------------------------------------------------------------
  // Desenho
  // -------------------------------------------------------------------------

  function cardHTML(c) {
    // 0 = acabou de chegar na coluna, 1 = estourou o limite dela.
    const calor = c.limite_min ? Math.min(1, (c.parado_min || 0) / c.limite_min) : 0;
    const alerta = calor >= 1 && !c.esfriada;
    const classes = ['qd-card'];
    if (alerta) classes.push('alerta');
    if (c.esfriada) classes.push('esfriada');
    if (c.respondendo) classes.push('respondendo');

    const tempo = c.respondendo
      ? '<span class="qd-digitando" title="O bot está escrevendo a resposta"><i></i><i></i><i></i></span>'
      : `<span class="qd-card-tempo">${esc(c.parado_txt)}</span>`;

    const chips = [];
    if (c.agendamento) {
      chips.push(`<span class="qd-chip agenda${c.agendamento.hoje ? ' hoje' : ''}">` +
        `${esc(c.agendamento.servico)} · ${esc(c.agendamento.quando)}</span>`);
    }
    if (c.pausado) chips.push('<span class="qd-chip alerta">você assumiu</span>');
    if (alerta) chips.push('<span class="qd-chip alerta">sem resposta</span>');
    if (c.esfriada) chips.push('<span class="qd-chip fria">esfriou</span>');

    const de = c.quem === 'cliente' ? '' : c.quem === 'sistema' ? 'Sistema: ' : 'Bot: ';
    const msg = c.preview
      ? `<p class="qd-card-msg${c.quem === 'cliente' ? ' do-cliente' : ''}">${esc(de)}${esc(c.preview)}</p>`
      : '<p class="qd-card-msg vazia">Sem mensagem ainda.</p>';

    return `<article class="${classes.join(' ')}" style="--calor:${calor.toFixed(2)}">
      <button type="button" class="qd-card-abrir" data-conversa="${esc(c.telefone)}"
              title="Abrir a conversa de ${esc(c.nome || c.telefone_fmt)}">
        <span class="ava" data-num="${esc(c.telefone)}">${esc(c.inicial)}</span>
        <span class="qd-card-quem">
          <span class="qd-card-nome">${esc(c.nome || 'Sem nome ainda')}</span>
          <span class="qd-card-tel">${esc(c.telefone_fmt)}</span>
        </span>
        ${tempo}
      </button>
      ${msg}
      <div class="qd-card-pe">
        <div class="qd-chips">${chips.join('')}</div>
        <button type="button" class="qd-acao" data-agendar="${esc(c.telefone)}"
                data-nome="${esc(c.nome)}">Agendar</button>
      </div>
    </article>`;
  }

  function colunaHTML(col) {
    const cards = col.cards.map(cardHTML).join('');
    return `<section class="qd-col" data-accent="${col.accent}" data-chave="${col.chave}">
      <header class="qd-col-topo">
        <div class="qd-col-id">
          <h3 class="qd-col-rotulo">${esc(col.rotulo)}</h3>
          <p class="qd-col-sub">${esc(col.sub)}</p>
        </div>
        <span class="qd-col-n">${col.cards.length}</span>
      </header>
      <div class="qd-col-cards">
        ${cards || '<p class="qd-vazio">Ninguém aqui.</p>'}
      </div>
    </section>`;
  }

  function pintar(d) {
    caixa.innerHTML = d.colunas.map(colunaHTML).join('');
    caixa.removeAttribute('aria-busy');
    pintarAvatares(caixa);

    const n = d.colunas.reduce((s, c) => s + c.cards.length, 0);
    total.textContent = n ? `${n} contato${n === 1 ? '' : 's'} no quadro` : 'quadro vazio';
    fora.hidden = !d.fora;
    fora.textContent = d.fora === 1
      ? '1 conversa esfriada está fora'
      : `${d.fora} conversas esfriadas estão fora`;

    // O badge do menu conta só o que pede ação: bot devendo resposta ou
    // conversa em que você assumiu.
    const pedindo = d.colunas.reduce((s, col) => s + col.cards.filter(c =>
      c.pausado || (c.limite_min && (c.parado_min || 0) >= c.limite_min && !c.esfriada)
    ).length, 0);
    if (navBadge) {
      navBadge.textContent = pedindo || '';
      navBadge.hidden = !pedindo;
      navBadge.title = pedindo ? 'contatos esperando você' : '';
    }
  }

  async function carregar(forcar = false) {
    if (document.hidden && !forcar) return;
    let d;
    try {
      const r = await fetch('/admin/kanban/estado', { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      d = await r.json();
    } catch (_) {
      live?.classList.add('off');
      return;
    }
    live?.classList.remove('off');
    preencherAjustes(d.ajustes);

    // Repintar a cada poll perderia o hover e piscaria: só redesenha quando a
    // resposta muda de verdade.
    const assinatura = JSON.stringify(d);
    if (!forcar && assinatura === ultimo) return;
    ultimo = assinatura;
    pintar(d);
  }

  // Atalhos: o card leva para a conversa e para um agendamento novo, que são
  // as duas coisas que se faz olhando o quadro.
  caixa.addEventListener('click', e => {
    const conversa = e.target.closest('[data-conversa]');
    if (conversa && window.abrirConversa) { window.abrirConversa(conversa.dataset.conversa); return; }
    const agendar = e.target.closest('[data-agendar]');
    if (agendar && window.abrirNovoAgendamento) {
      window.abrirNovoAgendamento({ nome: agendar.dataset.nome || '', telefone: agendar.dataset.agendar });
    }
  });

  document.addEventListener('visibilitychange', () => { if (!document.hidden) carregar(); });

  carregar();
  setInterval(carregar, POLL_MS);
}
