/* Seção "Clientes": a agenda de contatos vem pronta do servidor (Jinja) — aqui
   ficam a busca local, o toggle de pausa do bot (mesmo endpoint da lista de
   conversas) e os atalhos para a conversa e para um novo agendamento. */

import { toast } from './toast.js';

const card = document.getElementById('clientes-card');
if (card) {
  const busca = document.getElementById('clientes-busca');
  const vazio = document.getElementById('clientes-vazio');
  const linhas = [...card.querySelectorAll('tbody tr[data-tel]')];

  // ---- busca por nome ou telefone (filtra o que já está na tela) ----
  busca?.addEventListener('input', () => {
    const termo = busca.value.trim().toLowerCase();
    // dígitos soltos casam com o telefone mesmo digitado com máscara
    const so = termo.replace(/\D/g, '');
    let visiveis = 0;
    linhas.forEach(tr => {
      const alvo = tr.dataset.busca || '';
      const ok = !termo || alvo.includes(termo) ||
        (so && alvo.replace(/\D/g, '').includes(so));
      tr.hidden = !ok;
      if (ok) visiveis++;
    });
    if (vazio) vazio.hidden = !!visiveis || !termo;
  });

  // ---- pausa do bot por contato ----
  async function alternarPausa(btn) {
    const tel = btn.dataset.pausa;
    const pausar = !btn.classList.contains('on');
    const fd = new FormData();
    fd.append('pausar', pausar ? '1' : '0');
    btn.disabled = true;
    try {
      const r = await fetch(`/admin/conversas/${encodeURIComponent(tel)}/pausa`, {
        method: 'POST', body: fd, headers: { Accept: 'application/json' },
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        toast('erro', d.detail || 'Não foi possível mudar a pausa deste contato.');
      } else {
        btn.classList.toggle('on', pausar);
        btn.textContent = pausar ? 'pausado' : 'ativo';
        btn.setAttribute('aria-pressed', pausar ? 'true' : 'false');
        btn.title = pausar ? 'Bot pausado — clique para retomar' : 'Bot ativo — clique para pausar';
        toast('ok', pausar ? 'Bot pausado para este contato.' : 'Bot retomado para este contato.');
      }
    } catch (_) {
      toast('erro', 'Falha de conexão ao mudar a pausa.');
    }
    btn.disabled = false;
  }

  card.addEventListener('click', e => {
    const pausa = e.target.closest('[data-pausa]');
    if (pausa) { alternarPausa(pausa); return; }

    const fichaBtn = e.target.closest('[data-ficha]');
    if (fichaBtn && window.abrirFicha) { window.abrirFicha(fichaBtn.dataset.ficha); return; }

    const conversa = e.target.closest('[data-conversa]');
    if (conversa && window.abrirConversa) { window.abrirConversa(conversa.dataset.conversa); return; }

    const agendar = e.target.closest('[data-agendar]');
    if (agendar && window.abrirNovoAgendamento) {
      window.abrirNovoAgendamento({
        nome: agendar.dataset.nome || '',
        telefone: agendar.dataset.agendar,
      });
    }
  });
}
