/* Campos de telefone (input[data-telefone]): máscara BR progressiva enquanto
   digita + checagem de cortesia "esse número tem WhatsApp?" no blur/pausa.
   Reutilizável — hoje no modal de agendamento, no de cliente, nos campos de
   telefone da ficha e no telefone do dono (config).

   O prefixo +55 aparece assim que o campo recebe o foco (não depois do
   primeiro dígito) e é apagável: o backspace come os dígitos do DDI um a um
   até sobrar só o "+", de onde se digita o código de outro país. Sair do
   campo com apenas o prefixo limpa o campo — "+55" sozinho não é telefone e
   não pode passar por um `required` preenchido.

   O que trafega no submit é sempre dígitos E.164: quando a Evolution confirma
   o número, guardamos o jid canônico em dataset.canon (resolve o nono dígito);
   caso contrário, mandamos os próprios dígitos digitados. A normalização é
   feita num listener de submit em CAPTURE, então roda antes do forms.js e do
   módulo do modal — os dois leem o FormData já com os dígitos limpos. */

import { toast } from './toast.js';

const DEBOUNCE_MS = 600;
const DDI_PADRAO = '55';

function soDigitos(v) {
  return (v || '').replace(/\D/g, '');
}

// Formata os dígitos de um número nacional BR: "+55 (45) 99999-0000".
function mascararBR(national) {
  const d = national.slice(0, 11);
  const ddd = d.slice(0, 2);
  const resto = d.slice(2);
  let out = '+55';
  if (ddd) out += ' (' + ddd + (ddd.length === 2 ? ')' : '');
  if (resto) {
    // Móvel (9 díg.) parte 5+4; fixo (8 díg.) parte 4+4 — corte fixo em 4 no fim.
    const meio = resto.length > 4 ? resto.slice(0, resto.length - 4) : resto;
    const fim = resto.length > 4 ? resto.slice(resto.length - 4) : '';
    out += ' ' + meio + (fim ? '-' + fim : '');
  }
  return out;
}

/* Dígitos internacionais (DDI + nacional) do que está no campo. Sem "+" na
   frente assumimos Brasil — quem cola/digita só DDD+número continua sendo
   entendido; com "+" o DDI é o que o operador escreveu, seja ele qual for. */
function internacional(valor) {
  const temMais = (valor || '').trimStart().startsWith('+');
  const d = soDigitos(valor);
  if (temMais || !d) return d;
  const jaTemDdi = d.startsWith(DDI_PADRAO) && d.length >= 12;
  return jaTemDdi ? d : DDI_PADRAO + d;
}

// Texto exibido: BR ganha máscara; outro DDI fica "+DDI" seguido dos dígitos.
function render(d) {
  if (!d) return '+';
  if (d.startsWith(DDI_PADRAO)) return mascararBR(d.slice(2));
  return '+' + d;
}

/* Tem número de verdade ou só o código do país? "+55", "+1", "+351" são
   prefixo — DDI tem no máximo 3 dígitos, telefone tem no mínimo 10. */
function temNumero(valor) {
  const d = internacional(valor);
  return d.startsWith(DDI_PADRAO) ? d.length > 2 : d.length > 3;
}

// ---------------------------------------------------------------------------
// Badge de resultado (pill + avatar) ao lado do campo
// ---------------------------------------------------------------------------

function limparBadge(inp) {
  if (inp._telBadge) {
    inp._telBadge.remove();
    inp._telBadge = null;
  }
}

function inicialDe(inp) {
  // Fallback do avatar: inicial do nome do cliente, se houver campo no form.
  const nome = inp.form?.querySelector('[name="nome_cliente"], [name="nome"]')?.value?.trim();
  return (nome || '?').charAt(0).toUpperCase();
}

function mostrarBadge(inp, existe, foto) {
  limparBadge(inp);
  const el = document.createElement('div');
  el.className = 'tel-badge ' + (existe ? 'ok' : 'no');
  if (existe) {
    const ava = document.createElement('span');
    ava.className = 'ava tel-badge-ava';
    ava.textContent = inicialDe(inp);
    if (foto) {
      ava.style.backgroundImage = "url('" + foto + "')";
      ava.classList.add('loaded');
    }
    el.appendChild(ava);
    const txt = document.createElement('span');
    txt.textContent = 'WhatsApp ✓';
    el.appendChild(txt);
  } else {
    el.textContent = 'sem WhatsApp';
  }
  inp.insertAdjacentElement('afterend', el);
  inp._telBadge = el;
}

/* "O número deste campo é este" — quem depende do telefone para buscar algo do
   contato (a ficha no modal de agendamento) escuta isso em vez de adivinhar
   quando o operador terminou de digitar. Vai o canônico da Evolution quando ela
   confirma; senão, os dígitos digitados. */
function avisarNumero(inp, numero) {
  inp.dispatchEvent(new CustomEvent('telefone-numero', {
    detail: { numero }, bubbles: true,
  }));
}

async function checar(inp) {
  const d = soDigitos(inp.value);
  if (d.length < 10) {
    limparBadge(inp);
    return;
  }
  if (inp._telChecado === d) return;  // já consultado este número
  inp._telChecado = d;
  let info;
  try {
    const r = await fetch('/admin/whatsapp/checar?numero=' + encodeURIComponent(d));
    if (!r.ok) {           // 502 = checagem indisponível → some sem alarde
      limparBadge(inp);
      inp._telChecado = null;
      avisarNumero(inp, d);
      return;
    }
    info = await r.json();
  } catch (_) {
    limparBadge(inp);
    inp._telChecado = null;
    avisarNumero(inp, d);
    return;
  }
  if (info.existe) {
    inp.dataset.canon = soDigitos(info.numero) || d;
    if (info.numero_fmt) {              // assume o canônico
      inp.value = render(internacional(info.numero_fmt));
      inp._telPrev = soDigitos(inp.value);
    }
    mostrarBadge(inp, true, info.foto);
    avisarNumero(inp, inp.dataset.canon);
  } else {
    delete inp.dataset.canon;
    mostrarBadge(inp, false);
    avisarNumero(inp, d);
  }
}

// ---------------------------------------------------------------------------
// Ligação nos campos
// ---------------------------------------------------------------------------

/* Redesenha o campo a partir dos dígitos. `apagando` vem do inputType: quando
   o operador apaga um caractere da máscara (espaço, parêntese, hífen) a
   contagem de dígitos não muda e o formatador devolveria o mesmo texto — o
   backspace travaria. Nesse caso comemos o último dígito, e é isso que deixa
   apagar o próprio "+55" para digitar outro país. */
function aplicar(inp, apagando) {
  let d = internacional(inp.value);
  if (apagando && d === inp._telPrev) d = d.slice(0, -1);
  if (!d) {
    // Só o "+" na tela; apagá-lo também (prev vazio) limpa o campo de vez.
    inp.value = apagando && !inp._telPrev ? '' : '+';
    inp._telPrev = '';
    return;
  }
  inp.value = render(d);
  inp._telPrev = soDigitos(inp.value);   // render pode truncar (BR corta em 11)
}

function ligar(inp) {
  if (inp._telLigado) return;
  inp._telLigado = true;
  inp._telPrev = '';

  // Máscara + valor inicial (ex.: telefone do dono já preenchido pelo Jinja).
  if (inp.value.trim()) aplicar(inp, false);

  let timer = null;
  // Prefixo do país visível desde o clique no campo, não só depois do 1º dígito.
  inp.addEventListener('focus', () => {
    if (!inp.value.trim()) {
      inp.value = render(DDI_PADRAO);
      inp._telPrev = DDI_PADRAO;
    }
  });
  inp.addEventListener('input', (ev) => {
    aplicar(inp, (ev.inputType || '').startsWith('delete'));
    delete inp.dataset.canon;   // editou → cai a confirmação anterior
    inp._telChecado = null;
    limparBadge(inp);
    clearTimeout(timer);
    timer = setTimeout(() => checar(inp), DEBOUNCE_MS);
  });
  inp.addEventListener('blur', () => {
    clearTimeout(timer);
    if (!temNumero(inp.value)) {   // saiu deixando só o prefixo → campo vazio
      inp.value = '';
      inp._telPrev = '';
      limparBadge(inp);
      return;
    }
    checar(inp);
  });
}

document.querySelectorAll('input[data-telefone]').forEach(ligar);

// Normaliza para dígitos E.164 ANTES do envio (capture → roda antes dos
// handlers de submit dos outros módulos). Devolve a máscara logo depois, para
// o campo não ficar com dígitos crus caso o envio não recarregue a página.
document.addEventListener('submit', (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  const campos = [...form.querySelectorAll('input[data-telefone]')];
  if (!campos.length) return;

  /* Enter dentro do campo pula a validação nativa com "+55" no valor (não é
     vazio para o browser, mas é para nós) — barramos aqui. */
  const semNumero = campos.find(i => i.required && !temNumero(i.value));
  if (semNumero) {
    e.preventDefault();
    e.stopPropagation();
    semNumero.value = '';
    semNumero._telPrev = '';
    semNumero.focus();
    toast('erro', 'Preencha o telefone com DDD e número.');
    return;
  }

  campos.forEach(inp => {
    if (!temNumero(inp.value)) {   // opcional deixado em branco
      inp.value = '';
      inp._telPrev = '';
      return;
    }
    const canon = inp.dataset.canon || internacional(inp.value);
    inp.value = canon;
    setTimeout(() => { inp.value = render(canon); inp._telPrev = soDigitos(inp.value); }, 0);
  });
}, true);

// Reset do form (ex.: reabrir o modal) → limpa máscara/badge/confirmação.
document.addEventListener('reset', (e) => {
  if (!(e.target instanceof HTMLFormElement)) return;
  e.target.querySelectorAll('input[data-telefone]').forEach(inp => {
    delete inp.dataset.canon;
    inp._telChecado = null;
    setTimeout(() => { inp._telPrev = ''; limparBadge(inp); }, 0);
  });
}, true);

const formatar = (valor) => render(internacional(valor));

/* Escreve um número no campo SEM disparar a checagem — é o painel preenchendo
   (ficha de um contato), não o operador digitando. Nada de badge nem de canon
   herdado: se a Evolution devolvesse o número canônico aqui, o valor mudaria
   sozinho e o submit trocaria o telefone do contato sem ninguém pedir. */
function definir(inp, valor) {
  const d = internacional(valor);
  inp.value = d ? render(d) : '';
  inp._telPrev = soDigitos(inp.value);
  delete inp.dataset.canon;
  inp._telChecado = null;
  limparBadge(inp);
}

export {
  formatar as formatarTelefone,
  definir as definirTelefone,
  ligar as ligarTelefone,
  soDigitos,
};
