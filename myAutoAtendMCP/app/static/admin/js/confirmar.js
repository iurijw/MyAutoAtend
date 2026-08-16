/* Confirmação do painel — no lugar do confirm() do browser.

   Duas formas de usar:

   1. Em JS:  if (!await confirmar({ titulo, texto, acao, perigo })) return;
   2. Em form, sem escrever JS: atributos data-confirmar-* no <form>. O
      listener aqui segura o submit, pergunta, e só então deixa passar.

   Por que <dialog>: foco preso dentro, Esc e camada de topo vêm de graça do
   browser — reimplementar isso à mão é onde acessibilidade costuma quebrar. */

let dlg = null;
let resolver = null;

function montar() {
  if (dlg) return dlg;
  dlg = document.createElement('dialog');
  dlg.className = 'cfm';
  dlg.innerHTML = `
    <p class="cfm-eyebrow" id="cfm-eyebrow"></p>
    <h2 class="cfm-titulo" id="cfm-titulo"></h2>
    <p class="cfm-texto" id="cfm-texto"></p>
    <p class="cfm-nota" id="cfm-nota"></p>
    <div class="cfm-acts">
      <button type="button" class="btn btn-ghost btn-sm" id="cfm-nao"></button>
      <button type="button" class="btn btn-sm" id="cfm-sim"></button>
    </div>`;
  document.body.appendChild(dlg);

  const fechar = (resposta) => {
    if (!resolver) return;
    const r = resolver;
    resolver = null;
    dlg.close();
    r(resposta);
  };
  dlg.querySelector('#cfm-nao').addEventListener('click', () => fechar(false));
  dlg.querySelector('#cfm-sim').addEventListener('click', () => fechar(true));
  dlg.addEventListener('cancel', (e) => { e.preventDefault(); fechar(false); });
  // clique fora do cartão (a área do ::backdrop é o próprio dialog)
  dlg.addEventListener('click', (e) => { if (e.target === dlg) fechar(false); });
  return dlg;
}

/* Pergunta e devolve true/false.
   titulo  — a pergunta, curta.
   texto   — o que exatamente acontece (opcional).
   nota    — consequência dura, em destaque (opcional).
   acao    — rótulo do botão que confirma; repete o verbo do botão de origem.
   recusa  — rótulo do botão neutro (padrão "Voltar").
   perigo  — ação destrutiva: veste oxblood e o foco nasce no botão neutro. */
export function confirmar(opts = {}) {
  const d = montar();
  const {
    titulo = 'Confirmar?', texto = '', nota = '',
    acao = 'Confirmar', recusa = 'Voltar', perigo = false,
  } = typeof opts === 'string' ? { titulo: opts } : opts;

  d.classList.toggle('perigo', !!perigo);
  d.querySelector('#cfm-eyebrow').textContent = perigo ? 'Sem desfazer' : 'Confirmação';
  d.querySelector('#cfm-titulo').textContent = titulo;
  const elTexto = d.querySelector('#cfm-texto');
  elTexto.textContent = texto;
  elTexto.hidden = !texto;
  const elNota = d.querySelector('#cfm-nota');
  elNota.textContent = nota;
  elNota.hidden = !nota;

  const btnSim = d.querySelector('#cfm-sim');
  const btnNao = d.querySelector('#cfm-nao');
  btnSim.textContent = acao;
  btnSim.className = 'btn btn-sm ' + (perigo ? 'btn-danger cfm-perigo' : 'btn-acento');
  btnNao.textContent = recusa;

  return new Promise((res) => {
    resolver = res;
    d.showModal();
    // destrutivo: o foco nasce na saída segura, não no gatilho
    (perigo ? btnNao : btnSim).focus();
  });
}

/* ---------------------------------------------------------------------------
   Forms declarativos: <form data-confirmar="Excluir o serviço X?" ...>

   data-confirmar        pergunta (obrigatório p/ ligar o mecanismo)
   data-confirmar-texto  explicação
   data-confirmar-nota   consequência em destaque
   data-confirmar-acao   rótulo do botão que confirma
   data-confirmar-seguro presença = ação não destrutiva (sem oxblood)

   Segunda pergunta opcional (o cancelamento pergunta se avisa o cliente):
   data-confirmar2, -acao, -recusa e data-confirmar2-campo = nome do input
   escondido que recebe "1" (sim) ou "" (não).
--------------------------------------------------------------------------- */
document.addEventListener('submit', async (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  const pergunta = form.dataset.confirmar;
  if (!pergunta || form.dataset.confirmado === '1') return;

  e.preventDefault();
  e.stopPropagation();          // segura antes do forms.js enviar por fetch
  const enviador = e.submitter;

  const ok = await confirmar({
    titulo: pergunta,
    texto: form.dataset.confirmarTexto || '',
    nota: form.dataset.confirmarNota || '',
    acao: form.dataset.confirmarAcao || 'Confirmar',
    perigo: !('confirmarSeguro' in form.dataset),
  });
  if (!ok) return;

  if (form.dataset.confirmar2) {
    const sim = await confirmar({
      titulo: form.dataset.confirmar2,
      texto: form.dataset.confirmar2Texto || '',
      acao: form.dataset.confirmar2Acao || 'Avisar',
      recusa: form.dataset.confirmar2Recusa || 'Não avisar',
    });
    const campo = form.elements[form.dataset.confirmar2Campo];
    if (campo) campo.value = sim ? '1' : '';
  }

  form.dataset.confirmado = '1';
  form.requestSubmit(enviador);     // agora passa reto pelo listener
  delete form.dataset.confirmado;
}, true);                            // capture: chega antes do forms.js
