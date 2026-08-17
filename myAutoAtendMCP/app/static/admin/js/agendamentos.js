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

  // null = ainda não sincronizado. A 1ª resposta só guarda o HTML: o que está
  // na tela veio do servidor agora há pouco e repintar seria trabalho à toa.
  let ultimo = null;

  function contar(total) {
    contador.textContent = `${total} marcado${total === 1 ? '' : 's'}`;
    if (navBadge) {
      navBadge.textContent = total || '';
      navBadge.hidden = !total;
    }
  }

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
      const r = await fetch('/admin/agendamentos/estado', { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      d = await r.json();
    } catch (_) {
      live?.classList.add('off');
      return;
    }
    live?.classList.remove('off');
    contar(d.total);

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
