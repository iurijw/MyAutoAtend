/* Ficha de cadastro do cliente (feature opcional).

   Duas partes: o formulário de criação de campo na seção "Ficha de cadastro"
   (só mostra o input de opções quando o tipo é seleção) e o MODAL da ficha de
   um contato, aberto pela lista de clientes (window.abrirFicha) — os campos
   vêm de GET /admin/ficha/cliente/{tel}, montados conforme o tipo, e voltam em
   POST no mesmo endereço. A validação de verdade é do servidor (app/ficha.py,
   a mesma que o agente usa); aqui só marcamos o campo recusado. */

import { toast } from './toast.js';
import { ligarTelefone } from './telefone.js';

// ---------------------------------------------------------------------------
// Formulário de novo campo: opções só existem no tipo "seleção"
// ---------------------------------------------------------------------------

const tipoSel = document.getElementById('ficha-tipo');
const opcoesWrap = document.getElementById('ficha-opcoes-wrap');
if (tipoSel && opcoesWrap) {
  const sincronizar = () => {
    const selecao = tipoSel.value === 'selecao';
    opcoesWrap.hidden = !selecao;
    document.getElementById('ficha-opcoes').required = selecao;
  };
  tipoSel.addEventListener('change', sincronizar);
  sincronizar();
}

// ---------------------------------------------------------------------------
// Modal da ficha de um contato
// ---------------------------------------------------------------------------

const modal = document.getElementById('fic-modal');
if (modal) {
  document.body.appendChild(modal);   // sai da view (display:none) p/ o body

  const elAva = document.getElementById('fic-ava');
  const elNome = document.getElementById('fic-nome');
  const elTel = document.getElementById('fic-tel');
  const elProgresso = document.getElementById('fic-progresso');
  const elCampos = document.getElementById('fic-campos');
  const form = document.getElementById('fic-form');
  const btnSalvar = document.getElementById('fic-salvar');
  const btnConversa = document.getElementById('fic-conversa');

  let alvo = null;

  const esc = (s) => {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  };

  const ORIGEM = { agente: 'preenchido pelo agente', painel: 'preenchido por você' };

  // Um input por tipo — o navegador já valida data/hora/número no formato certo.
  function controle(campo) {
    const nome = 'campo_' + campo.chave;
    if (campo.tipo === 'texto_longo') {
      const t = document.createElement('textarea');
      t.name = nome;
      t.value = campo.valor;
      t.rows = 3;
      return t;
    }
    if (campo.tipo === 'selecao' || campo.tipo === 'booleano') {
      const s = document.createElement('select');
      s.name = nome;
      const alternativas = campo.tipo === 'booleano'
        ? [['sim', 'Sim'], ['nao', 'Não']]
        : (campo.opcoes || []).map(o => [o, o]);
      s.innerHTML = '<option value="">— não informado —</option>' +
        alternativas.map(([v, r]) =>
          `<option value="${esc(v)}"${campo.valor === v ? ' selected' : ''}>${esc(r)}</option>`).join('');
      return s;
    }
    const i = document.createElement('input');
    i.name = nome;
    i.value = campo.valor;
    if (campo.tipo === 'numero') { i.type = 'number'; i.step = 'any'; }
    else if (campo.tipo === 'data') i.type = 'date';
    else if (campo.tipo === 'hora') i.type = 'time';
    else if (campo.tipo === 'email') i.type = 'email';
    else if (campo.tipo === 'telefone') { i.type = 'tel'; i.setAttribute('data-telefone', ''); i.inputMode = 'tel'; }
    else i.type = 'text';
    if (campo.descricao) i.placeholder = campo.descricao;
    return i;
  }

  function linha(campo) {
    const bloco = document.createElement('div');
    bloco.className = 'fic-campo';
    bloco.dataset.chave = campo.chave;

    const rot = document.createElement('label');
    rot.textContent = campo.rotulo;
    if (campo.obrigatorio) {
      const marca = document.createElement('span');
      marca.className = 'fic-obrigatorio';
      marca.textContent = 'obrigatório';
      rot.appendChild(marca);
    }
    bloco.appendChild(rot);
    bloco.appendChild(controle(campo));

    const rodape = [];
    if (campo.descricao && campo.tipo === 'texto_longo') rodape.push(campo.descricao);
    if (campo.valor && campo.origem) {
      rodape.push((ORIGEM[campo.origem] || campo.origem) +
        (campo.atualizado_em ? ' · ' + campo.atualizado_em.replace('T', ' ').slice(0, 16) : ''));
    }
    if (rodape.length) {
      const p = document.createElement('p');
      p.className = 'fic-rodape';
      p.textContent = rodape.join(' — ');
      bloco.appendChild(p);
    }
    return bloco;
  }

  function pintar(dados) {
    elNome.textContent = dados.nome || dados.telefone_fmt || dados.telefone;
    elTel.textContent = dados.telefone_fmt || dados.telefone;
    elAva.textContent = (dados.nome || dados.telefone || '?').trim().charAt(0).toUpperCase() || '?';
    elProgresso.textContent = dados.total
      ? `${dados.preenchidos} de ${dados.total}`
      : 'sem campos';

    elCampos.innerHTML = '';
    if (!dados.campos.length) {
      elCampos.innerHTML = '<p class="fic-vazio">Nenhum campo ativo na ficha. Crie campos na seção <b>Ficha de cadastro</b>.</p>';
      btnSalvar.disabled = true;
      return;
    }
    btnSalvar.disabled = false;
    dados.campos.forEach(c => elCampos.appendChild(linha(c)));
    // máscara + checagem de WhatsApp nos campos de telefone recém-criados
    elCampos.querySelectorAll('input[data-telefone]').forEach(ligarTelefone);
  }

  async function carregar() {
    elCampos.innerHTML = '<p class="fic-vazio">Carregando…</p>';
    try {
      const r = await fetch('/admin/ficha/cliente/' + encodeURIComponent(alvo), {
        headers: { Accept: 'application/json' },
      });
      if (!r.ok) throw new Error(r.status);
      pintar(await r.json());
    } catch (_) {
      elCampos.innerHTML = '<p class="fic-vazio">Não foi possível carregar a ficha.</p>';
    }
  }

  function abrir(telefone) {
    alvo = telefone;
    elNome.textContent = '—';
    elTel.textContent = telefone;
    elProgresso.textContent = '';
    modal.classList.add('open');
    document.body.classList.add('fic-aberto');
    carregar();
  }

  function fechar() {
    modal.classList.remove('open');
    document.body.classList.remove('fic-aberto');
    alvo = null;
  }

  document.getElementById('fic-x').addEventListener('click', fechar);
  modal.querySelector('.fic-backdrop').addEventListener('click', fechar);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) fechar();
  });

  btnConversa.addEventListener('click', () => {
    const tel = alvo;
    fechar();
    if (tel && window.abrirConversa) window.abrirConversa(tel);
  });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (!alvo) return;
    elCampos.querySelectorAll('.campo-erro').forEach(el => el.classList.remove('campo-erro'));
    elCampos.querySelectorAll('.campo-erro-msg').forEach(el => el.remove());
    btnSalvar.disabled = true;
    try {
      const r = await fetch('/admin/ficha/cliente/' + encodeURIComponent(alvo), {
        method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' },
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok) {
        toast('ok', 'Ficha salva.');
        pintar(d);
      } else if (d.erros) {
        Object.entries(d.erros).forEach(([chave, msg]) => {
          const bloco = elCampos.querySelector(`.fic-campo[data-chave="${CSS.escape(chave)}"]`);
          if (!bloco) return;
          const campo = bloco.querySelector('input, select, textarea');
          campo?.classList.add('campo-erro');
          const p = document.createElement('p');
          p.className = 'campo-erro-msg';
          p.textContent = msg;
          bloco.appendChild(p);
        });
        toast('erro', 'Confira os campos marcados.');
      } else {
        toast('erro', d.detail || 'Não foi possível salvar a ficha.');
      }
    } catch (_) {
      toast('erro', 'Falha de conexão ao salvar a ficha.');
    }
    btnSalvar.disabled = false;
  });

  // Aberto pela lista de clientes (botão "Ficha").
  window.abrirFicha = abrir;
}
