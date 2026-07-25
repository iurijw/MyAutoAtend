/* Modal "Novo agendamento" do card Agendamentos: cadastro manual pelo dono.
   Escolhe serviço + data → busca os slots (GET /admin/agenda/slots) e mostra
   um seletor de horário em quadradinhos (livre/ocupado). O horário escolhido
   vira o hidden `inicio` (YYYY-MM-DDTHH:MM). Envio por fetch para não tirar o
   dono da página — sucesso recarrega, erro mostra toast sem fechar o modal.

   O telefone é tratado por telefone.js (máscara + checagem de WhatsApp); aqui
   só cuidamos do modal, dos slots e do envio. */

import { toast } from './toast.js';

const modal = document.getElementById('agm-modal');
if (modal) {
  // O modal nasce dentro da view "Agendamentos", que fica display:none quando
  // outra seção está aberta. Realocado pro body ele passa a abrir de qualquer
  // seção (a de Clientes usa isso) — mesmo padrão do modal de conversas.
  document.body.appendChild(modal);

  const abrirBtn = document.getElementById('ag-novo-abrir');
  const form = document.getElementById('agm-form');
  const servicoSel = document.getElementById('agm-servico');
  const dataInput = document.getElementById('agm-data');
  const slotsBox = document.getElementById('agm-slots');
  const legenda = document.getElementById('agm-legenda');
  const inicioHidden = document.getElementById('agm-inicio');
  const salvarBtn = document.getElementById('agm-salvar');

  // -------------------------------------------------------------------------
  // Slots (seletor de horário)
  // -------------------------------------------------------------------------

  function dica(texto) {
    slotsBox.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'agm-slots-dica';
    p.textContent = texto;
    slotsBox.appendChild(p);
    legenda.hidden = true;
  }

  function limparSelecao() {
    inicioHidden.value = '';
    slotsBox.querySelectorAll('.agm-slot.sel').forEach(el => el.classList.remove('sel'));
  }

  function selecionar(btn, hhmm) {
    slotsBox.querySelectorAll('.agm-slot.sel').forEach(el => el.classList.remove('sel'));
    btn.classList.add('sel');
    inicioHidden.value = dataInput.value + 'T' + hhmm;
  }

  function quadrado(s) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'agm-slot ' + s.estado;
    b.textContent = s.inicio;
    if (s.estado === 'ocupado') {
      b.disabled = true;
      b.title = 'Horário ocupado';
    } else {
      b.addEventListener('click', () => selecionar(b, s.inicio));
    }
    return b;
  }

  async function carregarSlots() {
    limparSelecao();
    const servico = servicoSel.value;
    const data = dataInput.value;
    if (!servico || !data) {
      dica('Escolha o serviço e a data para ver os horários.');
      return;
    }
    dica('Carregando horários…');
    let dados;
    try {
      const r = await fetch(
        '/admin/agenda/slots?data=' + encodeURIComponent(data) +
        '&servico_id=' + encodeURIComponent(servico),
      );
      if (!r.ok) throw new Error(r.status);
      dados = await r.json();
    } catch (_) {
      dica('Não foi possível carregar os horários. Tente de novo.');
      return;
    }
    if (dados.fechado) {
      dica('Dia sem expediente.');
      return;
    }
    if (!dados.slots.length) {
      dica('Nenhum horário nesta data.');
      return;
    }
    slotsBox.innerHTML = '';
    dados.slots.forEach(s => slotsBox.appendChild(quadrado(s)));
    legenda.hidden = false;
  }

  servicoSel.addEventListener('change', carregarSlots);
  dataInput.addEventListener('change', carregarSlots);

  // -------------------------------------------------------------------------
  // Abrir / fechar
  // -------------------------------------------------------------------------

  /* `pre` opcional ({nome, telefone}) vem da seção Clientes — o botão
     "Agendar" de um contato já abre o modal com a ficha preenchida. O evento
     'input' é o que aciona a máscara e a checagem de WhatsApp (telefone.js). */
  function abrir(pre) {
    form.reset();
    limparSelecao();
    dica('Escolha o serviço e a data para ver os horários.');
    modal.classList.add('open');
    document.body.classList.add('agm-aberto');
    const campoNome = form.querySelector('[name="nome_cliente"]');
    const campoTel = form.querySelector('[name="telefone_cliente"]');
    if (pre && (pre.nome || pre.telefone)) {
      campoNome.value = pre.nome || '';
      if (pre.telefone) {
        campoTel.value = pre.telefone;
        campoTel.dispatchEvent(new Event('input', { bubbles: true }));
      }
      (pre.nome ? servicoSel : campoNome).focus();
    } else {
      campoNome.focus();
    }
  }

  function fechar() {
    modal.classList.remove('open');
    document.body.classList.remove('agm-aberto');
  }

  abrirBtn?.addEventListener('click', () => abrir());
  // Aberto de fora (seção Clientes) já com o contato preenchido.
  window.abrirNovoAgendamento = abrir;
  document.getElementById('agm-x').addEventListener('click', fechar);
  document.getElementById('agm-cancelar').addEventListener('click', fechar);
  modal.querySelector('.agm-backdrop').addEventListener('click', fechar);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) fechar();
  });

  // -------------------------------------------------------------------------
  // Envio
  // -------------------------------------------------------------------------

  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (!inicioHidden.value) {
      toast('erro', 'Escolha um horário disponível para o agendamento.');
      return;
    }
    salvarBtn.disabled = true;
    try {
      const r = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        redirect: 'manual',                 // 303 vira opaqueredirect (= sucesso)
        headers: { Accept: 'application/json' },
      });
      if (r.type === 'opaqueredirect' || r.ok) {
        toast('ok', 'Agendamento criado.');
        location.reload();
        return;
      }
      let d = null;
      try { d = await r.json(); } catch (_) { /* corpo não-JSON */ }
      toast('erro', (d && typeof d.detail === 'string' && d.detail) ||
        'Não foi possível criar o agendamento.');
    } catch (_) {
      toast('erro', 'Falha de conexão ao criar o agendamento.');
    }
    salvarBtn.disabled = false;
  });
}
