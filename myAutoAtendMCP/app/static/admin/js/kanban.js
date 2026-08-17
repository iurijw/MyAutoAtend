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

// Formas de pagamento na ordem em que se usa no balcão.
const FORMAS = [['dinheiro', 'Dinheiro'], ['pix', 'Pix'], ['debito', 'Débito'], ['credito', 'Crédito']];
const FORMA_LEMBRADA = 'maa_forma_pgto';

const card = document.getElementById('quadro-card');
if (card) iniciar();

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

const brl = v => (v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });

// Valor para dentro do input: vírgula decimal, sem "R$" (o campo já mostra).
const paraCampo = v => (v || 0).toFixed(2).replace('.', ',').replace(',00', '');

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

  function cardHTML(c, coluna) {
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
      ${fecharHTML(c)}
      ${fechadoHTML(c, coluna)}
      <div class="qd-card-pe">
        <div class="qd-chips">${chips.join('')}</div>
        <button type="button" class="qd-acao" data-agendar="${esc(c.telefone)}"
                data-nome="${esc(c.nome)}">Agendar</button>
      </div>
    </article>`;
  }

  /* Desfecho pendente: os dois botões ficam no CARD, não na coluna — quem tem
     atendimento para fechar pode estar em "Vez do bot" (mandou mensagem depois)
     e o dono precisa poder fechar de lá também. "Compareceu" abre o formulário
     no lugar; "Faltou" passa pela confirmação e não lança nada. */
  function fecharHTML(c) {
    const f = c.fechamento;
    if (!f) return '';
    const quem = esc(c.nome || c.telefone_fmt);
    return `<div class="qd-fechar" data-fechar="${f.id}">
      <p class="qd-fechar-ref">${esc(f.servico)} · ${esc(f.quando)}</p>
      <div class="qd-fechar-linha">
        <button type="button" class="qd-btn-veio" data-veio="${f.id}"
                data-valor="${f.valor}">Compareceu</button>
        <form method="post" action="/admin/agendamento/${f.id}/concluir"
              data-sem-reload="Falta registrada."
              data-confirmar="Marcar falta de ${quem}?"
              data-confirmar-texto="${esc(f.servico)} · ${esc(f.quando)}"
              data-confirmar-nota="Falta não lança nada no caixa."
              data-confirmar-acao="Marcar falta" data-confirmar-seguro>
          <input type="hidden" name="compareceu" value="0">
          <button class="qd-btn-faltou">Faltou</button>
        </form>
      </div>
    </div>`;
  }

  /* Recibo do que já foi fechado. SEMPRE datado: sem a data, um "R$ 150,00"
     ao lado do chip do próximo horário parece ser daquele horário.

     Na coluna "Fechar atendimento" o recibo é o assunto do card — vem inteiro,
     com Desfazer. Em qualquer outra coluna (o cliente já remarcou, por
     exemplo) ele é só contexto do que passou: uma linha discreta, sem ✓ e sem
     Desfazer, que continua disponível na seção Agendamentos. */
  function fechadoHTML(c, coluna) {
    const f = c.fechado;
    if (!f) return '';
    const veio = f.resultado === 'concluido';
    const quando = esc(f.quando);
    const servico = esc(f.servico || '');

    if (coluna !== 'fechar') {
      const partes = [`${veio ? 'atendido' : 'faltou'} ${f.quando}`];
      if (veio && f.valor != null) partes.push(brl(f.valor));
      return `<p class="qd-passado" title="${servico}${veio && f.forma ? ' · ' + esc(f.forma) : ''}">
        ${esc(partes.join(' · '))}</p>`;
    }

    const partes = [`${veio ? 'Compareceu' : 'Faltou'} ${f.quando}`];
    if (veio && f.valor != null) partes.push(brl(f.valor));
    if (veio && f.forma) partes.push(f.forma);
    return `<div class="qd-fechado${veio ? '' : ' faltou'}">
      <span class="qd-fechado-txt" title="${servico}">${veio ? '✓' : '✕'} ${esc(partes.join(' · '))}</span>
      ${veio && f.valor != null && !f.pago ? '<span class="qd-chip alerta">a receber</span>' : ''}
      <form method="post" action="/admin/agendamento/${f.id}/reabrir"
            data-sem-reload="Fechamento desfeito.">
        <button class="qd-acao qd-desfazer">Desfazer</button>
      </form>
    </div>`;
  }

  /* Formulário de fechamento — trocado pelo par de botões no próprio card.
     Valor já vem preenchido com o preço do serviço e a forma de pagamento
     lembra a última usada neste navegador: fechar o dia é repetição. */
  function formularioHTML(id, valor) {
    const ultima = localStorage.getItem(FORMA_LEMBRADA) || '';
    const chips = FORMAS.map(([v, r]) =>
      `<button type="button" class="qd-forma" data-forma="${v}"
               aria-pressed="${v === ultima}">${r}</button>`).join('');
    return `<form class="qd-fechar-form" method="post"
                  action="/admin/agendamento/${id}/concluir"
                  data-sem-reload="Atendimento fechado.">
      <input type="hidden" name="compareceu" value="1">
      <input type="hidden" name="forma" value="${esc(ultima)}">
      <label class="qd-fechar-rot" for="qd-valor-${id}">Valor cobrado</label>
      <div class="qd-valor">
        <span class="qd-valor-cifra">R$</span>
        <input id="qd-valor-${id}" name="valor" inputmode="decimal" autocomplete="off"
               value="${esc(paraCampo(valor))}">
      </div>
      <div class="qd-formas" role="group" aria-label="Forma de pagamento">${chips}</div>
      <label class="qd-pago"><input type="checkbox" name="pago" value="1" checked> Já recebido</label>
      <div class="qd-fechar-acoes">
        <button type="button" class="qd-acao" data-cancelar-fechar>Cancelar</button>
        <button class="btn-sm btn-acento">Confirmar</button>
      </div>
    </form>`;
  }

  function colunaHTML(col) {
    const cards = col.cards.map(c => cardHTML(c, col.chave)).join('');
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

    // O badge do menu conta só o que pede ação: atendimento esperando
    // desfecho, bot devendo resposta ou conversa em que você assumiu.
    const pedindo = d.colunas.reduce((s, col) => s + col.cards.filter(c =>
      c.fechamento || c.pausado ||
      (c.limite_min && (c.parado_min || 0) >= c.limite_min && !c.esfriada)
    ).length, 0);
    if (navBadge) {
      navBadge.textContent = pedindo || '';
      navBadge.hidden = !pedindo;
      navBadge.title = pedindo ? 'contatos esperando você' : '';
    }
  }

  async function carregar(forcar = false) {
    if (document.hidden && !forcar) return;
    // Formulário de fechamento aberto: o poll espera. Repintar apagaria o
    // valor que o dono está digitando — quadro um pouco velho é melhor que
    // formulário sumindo na mão.
    if (!forcar && caixa.querySelector('.qd-fechar-form')) return;
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

  // Atalhos do card: conversa, agendamento novo e o fechamento do atendimento.
  caixa.addEventListener('click', e => {
    const veio = e.target.closest('[data-veio]');
    if (veio) {
      const bloco = veio.closest('.qd-fechar');
      bloco.innerHTML = formularioHTML(veio.dataset.veio, parseFloat(veio.dataset.valor) || 0);
      bloco.querySelector('input[name="valor"]').select();
      return;
    }
    const cancelar = e.target.closest('[data-cancelar-fechar]');
    if (cancelar) { carregar(true); return; }

    const forma = e.target.closest('.qd-forma');
    if (forma) {
      const grupo = forma.closest('.qd-formas');
      const jaEra = forma.getAttribute('aria-pressed') === 'true';
      grupo.querySelectorAll('.qd-forma').forEach(b => b.setAttribute('aria-pressed', 'false'));
      if (!jaEra) forma.setAttribute('aria-pressed', 'true');
      const valor = jaEra ? '' : forma.dataset.forma;
      grupo.closest('form').querySelector('input[name="forma"]').value = valor;
      localStorage.setItem(FORMA_LEMBRADA, valor);
      return;
    }

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
