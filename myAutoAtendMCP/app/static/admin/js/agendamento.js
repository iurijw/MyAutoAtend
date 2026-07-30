/* Modal "Novo agendamento" do card Agendamentos: cadastro manual pelo dono.
   Escolhe serviço + data → busca os slots (GET /admin/agenda/slots) e mostra
   um seletor de horário em quadradinhos (livre/ocupado). O horário escolhido
   vira o hidden `inicio` (YYYY-MM-DDTHH:MM). Envio por fetch para não tirar o
   dono da página — sucesso recarrega, erro mostra toast sem fechar o modal.

   O telefone é tratado por telefone.js (máscara + checagem de WhatsApp) e o
   nome do cliente tem autocomplete de contato já cadastrado (autocomplete.js);
   aqui só cuidamos do modal, dos slots e do envio. */

import { toast } from './toast.js';
import { ligarAutocompleteCliente } from './autocomplete.js';
import { montarCampos, marcarErros } from './ficha.js';

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
  const campoNome = form.querySelector('[name="nome_cliente"]');
  const campoTel = form.querySelector('[name="telefone_cliente"]');
  const fichaBox = document.getElementById('agm-ficha');
  const fichaNota = document.getElementById('agm-ficha-nota');
  const fichaCampos = document.getElementById('agm-ficha-campos');

  // -------------------------------------------------------------------------
  // Ficha de cadastro dentro do agendamento
  //
  // Com a ficha ligada, os campos dela entram no modal. Sabendo o telefone,
  // vêm de GET /admin/ficha/cliente/{tel} — que devolve os campos JÁ com os
  // valores do contato, então cliente antigo aparece preenchido e o operador
  // só confere. Sem telefone ainda, GET /admin/ficha/campos dá a definição
  // vazia. Os inputs são os mesmos do modal da ficha (ficha.js).
  // -------------------------------------------------------------------------

  let fichaDe = null;   // dígitos já montados (pedido e canônico) — evita remontar

  function fichaJaMontada(digitos) {
    return fichaDe !== null && fichaDe.includes(digitos);
  }

  async function montarFicha(telefone) {
    const digitos = (telefone || '').replace(/\D/g, '');
    if (fichaJaMontada(digitos)) return;
    const url = digitos.length >= 10
      ? '/admin/ficha/cliente/' + encodeURIComponent(digitos)
      : '/admin/ficha/campos';
    let d;
    try {
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error(r.status);
      d = await r.json();
    } catch (_) {
      fichaBox.hidden = true;   // ficha é complemento: falha não trava o agendamento
      return;
    }
    const campos = d.ativa ? (d.campos || []) : [];
    fichaDe = [digitos, (d.telefone || '').replace(/\D/g, '')];
    if (!campos.length) {
      fichaBox.hidden = true;
      fichaCampos.innerHTML = '';
      return;
    }
    montarCampos(fichaCampos, campos);
    const prontos = campos.filter(c => c.valor).length;
    fichaNota.textContent = prontos
      ? `Ficha de cadastro — ${prontos} de ${campos.length} campo${prontos === 1 ? '' : 's'} `
        + 'já vieram do cadastro deste cliente. Confira e complete.'
      : 'Ficha de cadastro — preencha o que o cliente já informou (pode ficar para depois).';
    fichaBox.hidden = false;
    // Contato conhecido pelo número: aproveita o nome que já está no cadastro.
    if (d.nome && !campoNome.value.trim()) campoNome.value = d.nome;
  }

  // -------------------------------------------------------------------------
  // Autocomplete do cliente: escolher um contato conhecido preenche o telefone
  // (dígitos E.164) e dispara o 'input' — quem monta a máscara e confere o
  // WhatsApp é o telefone.js.
  // -------------------------------------------------------------------------

  ligarAutocompleteCliente(campoNome, c => {
    campoNome.value = c.nome || '';
    campoTel.value = c.telefone;
    campoTel.dispatchEvent(new Event('input', { bubbles: true }));
    montarFicha(c.telefone);
    servicoSel.focus();
  });

  // Telefone digitado à mão: telefone.js avisa o número fechado (canônico da
  // Evolution quando ela confirma) → a ficha daquele contato entra sozinha.
  campoTel.addEventListener('telefone-numero', e => montarFicha(e.detail.numero));

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
    // Ficha sempre remontada: o reset zera o que o JS tinha escrito nos inputs.
    fichaDe = null;
    fichaCampos.innerHTML = '';
    fichaBox.hidden = true;
    modal.classList.add('open');
    document.body.classList.add('agm-aberto');
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
    montarFicha(pre?.telefone || '');
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
    marcarErros(fichaCampos, {});
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
      if (d && d.erros) {   // valor de campo da ficha recusado — nada foi criado
        marcarErros(fichaCampos, d.erros);
        toast('erro', 'Confira os campos marcados na ficha.');
        salvarBtn.disabled = false;
        return;
      }
      toast('erro', (d && typeof d.detail === 'string' && d.detail) ||
        'Não foi possível criar o agendamento.');
    } catch (_) {
      toast('erro', 'Falha de conexão ao criar o agendamento.');
    }
    salvarBtn.disabled = false;
  });
}
