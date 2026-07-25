/* Seção "Clientes": a agenda de contatos vem pronta do servidor (Jinja) — aqui
   ficam a busca local, o toggle de pausa do bot (mesmo endpoint da lista de
   conversas) e os atalhos para a conversa e para um novo agendamento. */

import { toast } from './toast.js';
import { montarCampos, marcarErros } from './ficha.js';

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

// ---------------------------------------------------------------------------
// Cadastro manual de cliente (modal). Nome + telefone e, com a ficha ligada,
// os campos dela — montados por ficha.js para não haver dois jeitos de
// desenhar o mesmo campo. O servidor valida a ficha ANTES de criar o contato.
// ---------------------------------------------------------------------------

const modal = document.getElementById('cli-modal');
if (modal) {
  document.body.appendChild(modal);   // sai da view (display:none) p/ o body

  const form = document.getElementById('cli-form');
  const caixaFicha = document.getElementById('cli-ficha');
  const btnSalvar = document.getElementById('cli-salvar');

  let campos = null;   // definição da ficha, buscada uma vez por página

  async function carregarCampos() {
    if (campos) return campos;
    try {
      const r = await fetch('/admin/ficha/campos', { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      const d = await r.json();
      campos = d.ativa ? (d.campos || []) : [];
    } catch (_) {
      campos = [];   // ficha indisponível não impede cadastrar o contato
    }
    return campos;
  }

  async function abrir() {
    form.reset();
    caixaFicha.innerHTML = '';
    modal.classList.add('open');
    document.body.classList.add('cli-aberto');
    document.getElementById('cli-nome').focus();
    const lista = await carregarCampos();
    if (lista.length) montarCampos(caixaFicha, lista);
  }

  function fechar() {
    modal.classList.remove('open');
    document.body.classList.remove('cli-aberto');
  }

  document.getElementById('cli-novo-abrir')?.addEventListener('click', abrir);
  document.getElementById('cli-x').addEventListener('click', fechar);
  document.getElementById('cli-cancelar').addEventListener('click', fechar);
  modal.querySelector('.cli-backdrop').addEventListener('click', fechar);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) fechar();
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    marcarErros(caixaFicha, {});
    btnSalvar.disabled = true;
    try {
      const r = await fetch(form.action, {
        method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' },
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok) {
        toast('ok', 'Cliente cadastrado.');
        location.reload();
        return;
      }
      if (d.erros) {
        marcarErros(caixaFicha, d.erros);
        toast('erro', 'Confira os campos marcados.');
      } else {
        toast('erro', d.detail || 'Não foi possível cadastrar o cliente.');
      }
    } catch (_) {
      toast('erro', 'Falha de conexão ao cadastrar o cliente.');
    }
    btnSalvar.disabled = false;
  });
}
