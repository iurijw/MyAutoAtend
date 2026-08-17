/* Seção "Agendamentos" ao vivo.

   A tabela vem pronta do servidor na carga da página; daí em diante este
   módulo busca o mesmo trecho em GET /admin/agendamentos/estado e troca o
   corpo da tabela quando ele muda. Serve para os dois lados:

   - o bot marca/remarca pelo WhatsApp e a agenda do painel acompanha sozinha;
   - cancelar, reagendar e criar pelo painel não recarregam mais a página
     (os forms levam `data-sem-reload`, o modal dispara o mesmo evento).

   Só repinta quando o HTML muda — poll com a tela igual não pisca nem derruba
   o formulário de reagendamento que estiver aberto. */

import { pintarAvatares } from './avatars.js';

const POLL_MS = 8000;

const tbody = document.getElementById('ag-tbody');
if (tbody) iniciar();

function iniciar() {
  const live = document.getElementById('ag-live');
  const contador = document.getElementById('ag-count');
  const navBadge = document.getElementById('nav-badge-agendamentos');
  const filtros = document.getElementById('ag-filtros');

  // null = ainda não sincronizado. A 1ª resposta só guarda o HTML: o que está
  // na tela veio do servidor agora há pouco e repintar seria trabalho à toa.
  let ultimo = null;
  // A agenda abre no que está por acontecer, sem recorte nenhum.
  const filtro = { status: 'ativo', periodo: 'tudo', de: '', ate: '', servico: '', q: '' };

  const refino = document.getElementById('ag-refino');
  const campoDe = document.getElementById('ag-de');
  const campoAte = document.getElementById('ag-ate');
  const campoServico = document.getElementById('ag-servico');
  const campoBusca = document.getElementById('ag-busca');
  const btnLimpar = document.getElementById('ag-limpar');

  const temRefino = () =>
    filtro.periodo !== 'tudo' || filtro.de || filtro.ate || filtro.servico || filtro.q;

  function consulta() {
    const p = new URLSearchParams({ status: filtro.status });
    // Datas explícitas mandam no servidor; só passe o atalho quando não houver.
    if (filtro.de || filtro.ate) {
      if (filtro.de) p.set('de', filtro.de);
      if (filtro.ate) p.set('ate', filtro.ate);
    } else if (filtro.periodo !== 'tudo') {
      p.set('periodo', filtro.periodo);
    }
    if (filtro.servico) p.set('servico', filtro.servico);
    if (filtro.q) p.set('q', filtro.q);
    return p.toString();
  }

  function marcarPeriodo(valor) {
    refino.querySelectorAll('[data-periodo]').forEach(b =>
      b.setAttribute('aria-pressed', String(b.dataset.periodo === valor)));
  }

  function contar(d) {
    contador.textContent = `${d.total} agendamento${d.total === 1 ? '' : 's'}`;
    if (navBadge) {
      // Sempre os ativos: o badge não pode mudar porque alguém foi olhar o
      // histórico de cancelados.
      navBadge.textContent = d.ativos || '';
      navBadge.hidden = !d.ativos;
    }
  }

  filtros?.addEventListener('click', e => {
    const botao = e.target.closest('.ag-filtro');
    if (!botao || botao.dataset.status === filtro.status) return;
    filtro.status = botao.dataset.status;
    filtros.querySelectorAll('.ag-filtro').forEach(b =>
      b.setAttribute('aria-pressed', String(b === botao)));
    carregar(true);
  });

  /* Período: os atalhos e o par de datas são dois caminhos para a mesma
     coisa, então um sempre limpa o outro — sem estado híbrido em que o chip
     diz "Hoje" e as datas dizem outra coisa. */
  refino?.addEventListener('click', e => {
    const chip = e.target.closest('[data-periodo]');
    if (!chip) return;
    filtro.periodo = chip.dataset.periodo;
    filtro.de = filtro.ate = '';
    campoDe.value = campoAte.value = '';
    marcarPeriodo(filtro.periodo);
    carregar(true);
  });

  [campoDe, campoAte].forEach(campo => campo?.addEventListener('change', () => {
    filtro.de = campoDe.value;
    filtro.ate = campoAte.value;
    filtro.periodo = 'tudo';
    marcarPeriodo(filtro.de || filtro.ate ? '' : 'tudo');
    carregar(true);
  }));

  campoServico?.addEventListener('change', () => {
    filtro.servico = campoServico.value;
    carregar(true);
  });

  // Busca digitada: espera a pessoa parar de escrever antes de bater no servidor.
  let timerBusca = null;
  campoBusca?.addEventListener('input', () => {
    clearTimeout(timerBusca);
    timerBusca = setTimeout(() => {
      filtro.q = campoBusca.value.trim();
      carregar(true);
    }, 250);
  });

  btnLimpar?.addEventListener('click', () => {
    Object.assign(filtro, { periodo: 'tudo', de: '', ate: '', servico: '', q: '' });
    campoDe.value = campoAte.value = campoServico.value = campoBusca.value = '';
    marcarPeriodo('tudo');
    carregar(true);
    campoBusca.focus();
  });

  /* Troca as linhas preservando o que o operador tem em edição: o form de
     reagendar aberto (com a data digitada e o "avisar cliente" marcado)
     sobrevive ao repinte — perder isso no meio da digitação seria pior que
     mostrar a tabela um segundo desatualizada. */
  function aplicar(html) {
    const abertos = new Map();
    tbody.querySelectorAll('.reagenda.open').forEach(f => {
      abertos.set(f.id, {
        quando: f.querySelector('[name="novo_inicio"]').value,
        avisar: f.querySelector('[name="avisar_cliente"]').checked,
      });
    });

    tbody.innerHTML = html;

    abertos.forEach((estado, id) => {
      const f = document.getElementById(id);
      if (!f) return;           // agendamento saiu da lista (cancelado)
      f.classList.add('open');
      f.querySelector('[name="novo_inicio"]').value = estado.quando;
      f.querySelector('[name="avisar_cliente"]').checked = estado.avisar;
    });

    pintarAvatares(tbody);
  }

  async function carregar(forcar = false) {
    if (document.hidden && !forcar) return;   // aba em segundo plano não gasta poll
    let d;
    try {
      const r = await fetch('/admin/agendamentos/estado?' + consulta(),
        { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      d = await r.json();
    } catch (_) {
      live?.classList.add('off');
      return;
    }
    live?.classList.remove('off');
    contar(d);
    if (btnLimpar) btnLimpar.hidden = !temRefino();

    const primeira = ultimo === null;
    const mudou = d.linhas !== ultimo;
    ultimo = d.linhas;
    if (forcar || (mudou && !primeira)) aplicar(d.linhas);
  }

  // Ação do painel (cancelar/reagendar pelo form, criar pelo modal): repinta na
  // hora, sem esperar o próximo tick.
  document.addEventListener('painel:atualizar', () => carregar(true));
  // Voltar para a aba: o poll ficou parado, atualiza antes de mostrar.
  document.addEventListener('visibilitychange', () => { if (!document.hidden) carregar(); });

  carregar();
  setInterval(carregar, POLL_MS);
}
