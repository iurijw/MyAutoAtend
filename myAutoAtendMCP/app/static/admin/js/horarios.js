/* Seção "Horários de funcionamento".

   O form continua sendo um POST clássico de replace-all (trincas paralelas
   dia/inicio/fim) — o servidor é a verdade. O que este módulo acrescenta:

   - RÉGUA DA SEMANA: as sete faixas desenhadas numa escala de horas, ao vivo.
     É a única peça que mostra a semana inteira de uma vez; digitou, a barra
     andou. A escala se ajusta ao que existe na grade (nunca 00–24 vazio).
   - Chave por dia: fechar NÃO apaga os intervalos, só desabilita os inputs —
     input disabled não é enviado, então a grade some do POST e volta intacta
     quando o dia é reaberto (antes, fechar era apagar linha por linha).
   - "copiar para…": a mesma jornada em vários dias sem redigitar.
   - Estado sujo: o form é grande e só grava no botão. A barra de salvar diz se
     há mudança pendente e o beforeunload segura a saída acidental.
   - Validação antes do POST (horas cheias, fim > início, sem sobreposição):
     o servidor também valida, mas lá o erro chega depois de um round-trip e
     sem apontar o campo. */

import { toast } from './toast.js';

const NOMES = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira',
               'Sexta-feira', 'Sábado', 'Domingo'];
const CURTOS = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom'];

const raiz = document.getElementById('horarios-card');
if (raiz) iniciar(raiz);

function iniciar(card) {
  const form = card.querySelector('#horarios-form');
  const regua = card.querySelector('#hf-regua');
  const barra = card.querySelector('#hf-salvar');
  const barraTxt = barra.querySelector('.hf-salvar-txt span');
  const btnSalvar = barra.querySelector('#hf-salvar-btn');
  const btnDescartar = barra.querySelector('#hf-descartar');
  const resumo = card.querySelector('#hf-resumo');
  const resumoDet = card.querySelector('#hf-resumo-det');
  const dias = [...card.querySelectorAll('.hf-dia')];

  /* ---------- horas ---------- */
  const emMin = (v) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec((v || '').trim());
    if (!m) return null;
    const h = +m[1], mm = +m[2];
    return (h < 24 && mm < 60) ? h * 60 + mm : null;
  };
  const hhmm = (t) => String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0');
  const durTxt = (t) => {
    const h = Math.floor(t / 60), m = t % 60;
    return m ? `${h}h${String(m).padStart(2, '0')}` : `${h}h`;
  };

  const aberto = (dia) => dia.querySelector('.hf-abrir').checked;
  const linhas = (dia) => [...dia.querySelectorAll('.hf-int')];
  const faixa = (el) => {
    const ini = el.querySelector('[name="inicio"]');
    const fim = el.querySelector('[name="fim"]');
    return { el, ini, fim, a: emMin(ini.value), b: emMin(fim.value), ruim: false };
  };

  function novaLinha(d, a, b) {
    const div = document.createElement('div');
    div.className = 'hf-int';
    div.innerHTML =
      `<input type="hidden" name="dia" value="${d}">` +
      `<input name="inicio" type="time" value="${a}" required aria-label="${NOMES[d]}: início do intervalo">` +
      '<span class="hf-ate">–</span>' +
      `<input name="fim" type="time" value="${b}" required aria-label="${NOMES[d]}: fim do intervalo">` +
      `<button type="button" class="hf-rm" title="Remover intervalo" aria-label="Remover este intervalo de ${NOMES[d]}">✕</button>`;
    return div;
  }

  /* Sugestão para o próximo intervalo: começa uma hora depois do fim do último
     (o vão do almoço é o caso comum) e dura quatro horas, dentro do dia. */
  function proximaFaixa(dia) {
    const fs = linhas(dia).map(faixa).filter(f => f.b != null).sort((x, y) => x.b - y.b);
    if (!fs.length) return ['09:00', '18:00'];
    const a = Math.min(fs[fs.length - 1].b + 60, 23 * 60);
    return [hhmm(a), hhmm(Math.min(a + 240, 23 * 60 + 59))];
  }

  /* Dia reaberto sem intervalo herda a jornada do dia aberto mais próximo —
     quem atende de novo quase sempre atende no mesmo horário. */
  function modeloVizinho(i) {
    for (let passo = 1; passo < 7; passo++) {
      for (const j of [i - passo, i + passo]) {
        if (j < 0 || j > 6) continue;
        if (!aberto(dias[j])) continue;
        const fs = linhas(dias[j]).map(f => [f.querySelector('[name="inicio"]').value,
                                             f.querySelector('[name="fim"]').value]);
        if (fs.length) return fs;
      }
    }
    return [['09:00', '18:00']];
  }

  /* ---------- leitura + validação ---------- */
  function analisar() {
    return dias.map((dia, i) => {
      const ab = aberto(dia);
      const fs = linhas(dia).map(faixa);
      let erro = '';

      fs.forEach(f => {
        if (f.a == null || f.b == null) {
          f.ruim = true;
          if (!erro) erro = `${NOMES[i]}: preencha as duas horas do intervalo.`;
        } else if (f.b <= f.a) {
          f.ruim = true;
          if (!erro) erro = `${NOMES[i]}: o intervalo ${hhmm(f.a)}–${hhmm(f.b)} termina antes de começar.`;
        }
      });

      const validas = fs.filter(f => f.a != null && f.b != null && f.b > f.a).sort((x, y) => x.a - y.a);
      for (let k = 1; k < validas.length; k++) {
        if (validas[k].a < validas[k - 1].b) {
          validas[k].ruim = validas[k - 1].ruim = true;
          if (!erro) {
            erro = `${NOMES[i]}: ${hhmm(validas[k - 1].a)}–${hhmm(validas[k - 1].b)} e ` +
                   `${hhmm(validas[k].a)}–${hhmm(validas[k].b)} se sobrepõem.`;
          }
        }
      }

      return {
        aberto: ab,
        faixas: fs,
        total: validas.reduce((s, f) => s + (f.b - f.a), 0),
        erro: ab ? erro : '',
      };
    });
  }

  /* ---------- régua ---------- */
  function desenharRegua(dados) {
    let ini = 24 * 60, fim = 0, tem = false;
    dados.forEach(d => {
      if (!d.aberto) return;
      d.faixas.forEach(f => {
        if (f.a == null || f.b == null || f.b <= f.a) return;
        tem = true;
        ini = Math.min(ini, f.a);
        fim = Math.max(fim, f.b);
      });
    });
    if (!tem) { ini = 8 * 60; fim = 18 * 60; }

    let h0 = Math.max(0, Math.floor(ini / 60) - 1);
    let h1 = Math.min(24, Math.ceil(fim / 60) + 1);
    while (h1 - h0 < 8) {                       // escala curta demais fica ilegível
      if (h0 > 0) h0--;
      else if (h1 < 24) h1++;
      else break;
    }
    const horas = h1 - h0;
    const span = horas * 60;
    const passo = horas <= 10 ? 1 : horas <= 16 ? 2 : 3;
    const pos = (t) => (((t - h0 * 60) / span) * 100).toFixed(3);

    let ticks = '';
    for (let h = h0; h <= h1; h += passo) {
      ticks += `<span style="left:${pos(h * 60)}%">${String(h).padStart(2, '0')}</span>`;
    }
    let html = `<div class="hf-regua-linha hf-regua-escala"><span class="hf-regua-dia"></span>` +
               `<div class="hf-regua-horas">${ticks}</div></div>`;

    dados.forEach((d, i) => {
      let barras = '';
      if (d.aberto) {
        d.faixas.forEach(f => {
          if (f.a == null) return;
          const b = (f.b == null || f.b <= f.a) ? f.a + 15 : f.b;   // faixa inválida vira lasca vermelha
          const larg = Math.max(((Math.min(b, h1 * 60) - f.a) / span) * 100, 1.4);
          const rot = f.b == null ? hhmm(f.a) + '–?' : `${hhmm(f.a)}–${hhmm(f.b)}`;
          const curta = larg < 17 ? ' curta' : '';
          barras += `<span class="hf-barra${f.ruim ? ' ruim' : ''}${curta}" title="${rot}"` +
                    ` style="left:${pos(f.a)}%;width:${larg.toFixed(3)}%">` +
                    `<span class="hf-barra-txt">${rot}</span></span>`;
        });
      }
      if (!barras) barras = `<span class="hf-regua-off">${d.aberto ? 'sem intervalo' : 'fechado'}</span>`;
      html += `<div class="hf-regua-linha${d.aberto ? ' aberta' : ''}" data-dia="${i}">` +
              `<span class="hf-regua-dia">${CURTOS[i]}</span>` +
              `<div class="hf-regua-trilho">${barras}</div></div>`;
    });

    regua.style.setProperty('--hf-tick', (100 * passo / horas).toFixed(4) + '%');
    regua.innerHTML = html;
  }

  /* ---------- estado sujo ---------- */
  function assinatura() {
    return dias.map(dia => {
      if (!aberto(dia)) return 'x';
      return linhas(dia)
        .map(el => el.querySelector('[name="inicio"]').value + '-' + el.querySelector('[name="fim"]').value)
        .sort().join(',');
    }).join('|');
  }
  const inicial = assinatura();
  let saindoDeProposito = false;

  function atualizarBarra() {
    const sujo = assinatura() !== inicial;
    barra.classList.toggle('sujo', sujo);
    barraTxt.textContent = sujo ? 'Alterações não salvas.' : 'Grade salva.';
    btnDescartar.hidden = !sujo;
    btnSalvar.disabled = !sujo;
  }

  /* ---------- pintura geral ---------- */
  function pintar() {
    const dados = analisar();
    let nInt = 0;

    dados.forEach((d, i) => {
      const dia = dias[i];
      dia.toggleAttribute('data-fechado', !d.aberto);
      // Dia fechado sai do POST sem perder o que estava digitado.
      linhas(dia).forEach(el => el.querySelectorAll('input').forEach(inp => { inp.disabled = !d.aberto; }));
      d.faixas.forEach(f => f.el.classList.toggle('ruim', d.aberto && f.ruim));

      const n = d.faixas.length;
      if (d.aberto) nInt += n;
      const tot = dia.querySelector('.hf-dia-total');
      // Com conflito o somatório mentiria (hora sobreposta contada duas vezes).
      tot.textContent = !d.aberto ? 'fechado'
        : d.erro ? 'conflito'
        : (d.total ? durTxt(d.total) : 'sem intervalo');
      tot.classList.toggle('ruim', d.aberto && !!d.erro);
      dia.querySelector('.hf-copiar').hidden = !d.aberto || !n;
      dia.querySelector('.hf-add').textContent = n ? '+ intervalo' : '+ definir horário';
    });

    desenharRegua(dados);

    const abertos = dados.filter(d => d.aberto && d.total > 0).length;
    const semana = dados.reduce((s, d) => s + (d.aberto ? d.total : 0), 0);
    resumo.textContent = abertos
      ? `${abertos} dia${abertos > 1 ? 's' : ''} · ${durTxt(semana)} por semana`
      : 'semana fechada';
    resumoDet.textContent = nInt
      ? `${nInt} intervalo${nInt > 1 ? 's' : ''}`
      : 'nenhum intervalo — o agente não oferece horário';
    card.classList.toggle('hf-sem-nada', !nInt);
    atualizarBarra();
  }

  /* ---------- popover "copiar para…" ---------- */
  let pop = null;

  function fecharPop() {
    if (!pop) return;
    pop.dono.setAttribute('aria-expanded', 'false');
    pop.el.remove();
    pop = null;
  }

  function abrirPop(dia, btn) {
    const d = +dia.dataset.dia;
    fecharPop();
    const el = document.createElement('div');
    el.className = 'hf-pop';
    el.innerHTML =
      `<p class="hf-pop-tit">Copiar ${NOMES[d].toLowerCase()} para</p>` +
      '<div class="hf-pop-dias">' +
      [0, 1, 2, 3, 4, 5, 6].filter(x => x !== d).map(x =>
        `<button type="button" class="hf-pop-dia" data-dia="${x}" aria-pressed="false">${CURTOS[x]}</button>`
      ).join('') +
      '</div>' +
      '<div class="hf-pop-atalhos">' +
      '<button type="button" class="hf-pop-link" data-set="uteis">dias úteis</button>' +
      '<button type="button" class="hf-pop-link" data-set="todos">todos</button>' +
      '</div>' +
      '<div class="hf-pop-acts">' +
      '<button type="button" class="btn-sm btn-ghost" data-fechar>Cancelar</button>' +
      '<button type="button" class="btn-sm btn-acento" data-aplicar>Aplicar</button>' +
      '</div>';

    btn.parentElement.appendChild(el);
    btn.setAttribute('aria-expanded', 'true');
    pop = { el, dono: btn, dia: d };

    const chips = [...el.querySelectorAll('.hf-pop-dia')];
    const marcar = (lista) => chips.forEach(c =>
      c.setAttribute('aria-pressed', String(lista.includes(+c.dataset.dia))));

    el.addEventListener('click', (e) => {
      const chip = e.target.closest('.hf-pop-dia');
      if (chip) {
        chip.setAttribute('aria-pressed', chip.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
        return;
      }
      const link = e.target.closest('.hf-pop-link');
      if (link) {
        marcar(link.dataset.set === 'uteis' ? [0, 1, 2, 3, 4] : [0, 1, 2, 3, 4, 5, 6]);
        return;
      }
      if (e.target.closest('[data-fechar]')) { fecharPop(); btn.focus(); return; }
      if (e.target.closest('[data-aplicar]')) aplicarCopia();
    });

    function aplicarCopia() {
      const alvos = chips.filter(c => c.getAttribute('aria-pressed') === 'true').map(c => +c.dataset.dia);
      if (!alvos.length) { toast('aviso', 'Escolha ao menos um dia para copiar.'); return; }
      const modelo = linhas(dia).map(l => [l.querySelector('[name="inicio"]').value,
                                           l.querySelector('[name="fim"]').value]);
      alvos.forEach(x => {
        const alvo = dias[x];
        const lista = alvo.querySelector('.hf-intervalos');
        lista.textContent = '';
        modelo.forEach(([a, b]) => lista.appendChild(novaLinha(x, a, b)));
        alvo.querySelector('.hf-abrir').checked = true;
      });
      fecharPop();
      btn.focus();
      pintar();
      toast('ok', `Horário de ${NOMES[d].toLowerCase()} copiado para ${alvos.length} dia${alvos.length > 1 ? 's' : ''}.`);
    }

    chips[0]?.focus();
  }

  document.addEventListener('click', (e) => {
    if (pop && !pop.el.contains(e.target) && e.target !== pop.dono) fecharPop();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && pop) { const b = pop.dono; fecharPop(); b.focus(); }
  });

  /* ---------- eventos da grade ---------- */
  card.querySelector('.hf-grade').addEventListener('click', (e) => {
    const dia = e.target.closest('.hf-dia');
    if (!dia) return;
    const d = +dia.dataset.dia;

    if (e.target.closest('.hf-add')) {
      const [a, b] = proximaFaixa(dia);
      dia.querySelector('.hf-intervalos').appendChild(novaLinha(d, a, b));
      dia.querySelector('.hf-abrir').checked = true;
      pintar();
      dia.querySelector('.hf-int:last-child [name="inicio"]').focus();
      return;
    }
    if (e.target.closest('.hf-rm')) {
      e.target.closest('.hf-int').remove();
      if (!linhas(dia).length) dia.querySelector('.hf-abrir').checked = false;
      pintar();
      return;
    }
    if (e.target.closest('.hf-copiar')) {
      if (pop && pop.dia === d) fecharPop();
      else abrirPop(dia, e.target.closest('.hf-copiar'));
    }
  });

  card.querySelector('.hf-grade').addEventListener('change', (e) => {
    const chave = e.target.closest('.hf-abrir');
    if (!chave) { pintar(); return; }
    const dia = chave.closest('.hf-dia');
    const d = +dia.dataset.dia;
    if (chave.checked && !linhas(dia).length) {
      const lista = dia.querySelector('.hf-intervalos');
      modeloVizinho(d).forEach(([a, b]) => lista.appendChild(novaLinha(d, a, b)));
    }
    pintar();
  });

  form.addEventListener('input', () => { saindoDeProposito = false; pintar(); });

  /* Realce cruzado: a linha da régua acende junto com o dia em edição. */
  const realce = (i) => regua.querySelectorAll('.hf-regua-linha').forEach(l =>
    l.classList.toggle('destaque', l.dataset.dia === String(i)));
  card.querySelector('.hf-grade').addEventListener('pointerover', (e) => {
    const dia = e.target.closest('.hf-dia');
    realce(dia ? dia.dataset.dia : -1);
  });
  card.querySelector('.hf-grade').addEventListener('pointerleave', () => realce(-1));
  card.querySelector('.hf-grade').addEventListener('focusin', (e) => {
    const dia = e.target.closest('.hf-dia');
    if (dia) realce(dia.dataset.dia);
  });

  btnDescartar.addEventListener('click', () => { saindoDeProposito = true; location.reload(); });

  form.addEventListener('submit', (e) => {
    const dados = analisar();
    const problema = dados.find(d => d.aberto && d.erro);
    if (problema) {
      e.preventDefault();          // forms.js respeita o defaultPrevented
      pintar();
      toast('erro', problema.erro);
      card.querySelector('.hf-int.ruim [name="inicio"]')?.focus();
      return;
    }
    saindoDeProposito = true;      // o reload do forms.js não deve pedir confirmação
  });

  window.addEventListener('beforeunload', (e) => {
    if (saindoDeProposito || assinatura() === inicial) return;
    e.preventDefault();
    e.returnValue = '';
  });

  pintar();
}
