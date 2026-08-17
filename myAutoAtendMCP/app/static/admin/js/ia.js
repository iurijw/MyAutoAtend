/* Seção "Quem atende": provedor, chave e modelo por uso (texto/áudio/imagem).

   Três trilhas sempre visíveis (antes eram abas, que escondiam justamente o
   que se quer saber de relance: o que está no ar). Configurar abre UMA por
   vez, com os passos na ordem em que dependem um do outro — a lista de
   modelos do provedor só existe depois que a chave é colada, e é por isso que
   a numeração é numeração de verdade e não enfeite.

   Um botão Salvar só. Chave e modelo têm endpoints separados no servidor
   (/admin/ia/credencial e /admin/ia/modelo), mas isso é detalhe nosso: quem
   usa mudou "quem atende" e salva uma vez.

   A chave é via de mão única: entra, nunca volta — nem mascarada. Por isso,
   aproveitar a mesma chave em outro uso é feito NO SERVIDOR
   (/admin/ia/reusar), não recolando o segredo em outro campo. Quando já há
   chave, o campo some do caminho e volta no "Trocar chave". */

import { toast } from './toast.js';

const PROVEDORES = window.__ADMIN__.provedores;
const NOME_PROV = (chave) => (PROVEDORES[chave] || {}).nome || chave;
const ATENDE = (prov, alvo) => !!(PROVEDORES[prov] || {})[alvo];
const PADRAO = (prov, alvo) => ((PROVEDORES[prov] || {}).padrao || {})[alvo] || '';

const card = document.getElementById('ia-card');
if (card) iniciar();

function iniciar() {
  const geral = document.getElementById('ia-geral');
  const trilhas = [...card.querySelectorAll('.ia-uso')].map(montar);
  const porAlvo = Object.fromEntries(trilhas.map(t => [t.alvo, t]));

  function fecharOutras(atual) {
    trilhas.forEach(t => { if (t !== atual) t.fechar(); });
  }

  /* Quem pode emprestar a chave para este uso: outro uso já configurado cujo
     provedor também atende aqui. É o caso comum de um provedor só (OpenRouter,
     OpenAI) cobrindo conversa, áudio e imagem. */
  function doadoras(alvo) {
    return trilhas.filter(t =>
      t.alvo !== alvo && t.provedor() && ATENDE(t.provedor(), alvo));
  }

  function resumoGeral() {
    const prontos = trilhas.filter(t => t.provedor()).length;
    geral.textContent = prontos === trilhas.length
      ? 'tudo configurado'
      : `${prontos} de ${trilhas.length} configurados`;
  }

  // -------------------------------------------------------------------------
  // Uma trilha (um uso)
  // -------------------------------------------------------------------------
  function montar(secao) {
    const alvo = secao.dataset.alvo;
    const nome = secao.dataset.nome;
    const q = (sel) => secao.querySelector(sel);

    const editor = q('[data-editor]');
    const btnAbrir = q('[data-abrir]');
    const pill = q('[data-pill]');
    const atual = q('[data-atual]');
    const selProv = q('[data-prov]');
    const caixaUrl = q('[data-url]');
    const campoUrl = q('[data-url-campo]');
    const caixaOk = q('[data-chave-ok]');
    const quandoEl = q('[data-chave-quando]');
    const caixaNova = q('[data-chave-nova]');
    const campoKey = q('[data-key]');
    const caixaReusar = q('[data-reusar]');
    const caixaReplicar = q('[data-replicar]');
    const checkReplicar = q('[data-replicar-check]');
    const txtReplicar = q('[data-replicar-txt]');
    const campoModelo = q('[data-modelo]');
    const dicaModelo = q('[data-dica-modelo]');
    const erroEl = q('[data-erro]');

    let salvo = { provedor: null, modelo: null };
    let modelos = [];
    const combo = comboModelo(campoModelo, () => modelos);

    // O dict chega ordenado alfabeticamente do Jinja (tojson ordena as chaves),
    // o que jogaria "Personalizado" para o topo — e o topo é o que fica
    // escolhido por padrão. Provedores prontos primeiro, URL própria por último.
    const ordenados = Object.entries(PROVEDORES)
      .filter(([, p]) => p[alvo])
      .sort(([a, pa], [b, pb]) =>
        (a === 'custom') - (b === 'custom') || pa.nome.localeCompare(pb.nome, 'pt-BR'));
    for (const [chave, p] of ordenados) {
      const opt = document.createElement('option');
      opt.value = chave;
      opt.textContent = p.nome;
      selProv.appendChild(opt);
    }

    const eCustom = () => selProv.value === 'custom';

    function erro(txt) {
      erroEl.textContent = txt || '';
      erroEl.classList.toggle('visivel', !!txt);
    }

    function dica(txt, tom) {
      dicaModelo.textContent = txt;
      dicaModelo.className = 'ia-dica' + (tom ? ' ' + tom : '');
    }

    /* O número acende quando o passo está cumprido — o único enfeite da
       seção, e ele diz uma coisa verdadeira: o que ainda falta. */
    function marcarPassos() {
      const feito = {
        provedor: !!selProv.value && (!eCustom() || !!campoUrl.value.trim()),
        chave: !!campoKey.value.trim() || !!salvo.provedor,
        modelo: !!campoModelo.value.trim(),
      };
      secao.querySelectorAll('.ia-passo').forEach(li =>
        li.classList.toggle('ok', !!feito[li.dataset.passo]));
    }

    // ---- campo da chave: escondido quando já existe uma ----
    function mostrarCampoChave(mostrar) {
      caixaOk.hidden = mostrar || !salvo.provedor;
      caixaNova.hidden = !mostrar && !!salvo.provedor;
      if (mostrar) campoKey.focus();
      marcarPassos();
    }

    function pintarEstado(info) {
      salvo = { provedor: info.provedor || null, modelo: info.modelo || null };
      if (info.provedor) {
        pill.className = 'pill on';
        pill.textContent = 'no ar';
        atual.innerHTML = '';
        atual.append(NOME_PROV(info.provedor));
        if (info.modelo) {
          const m = document.createElement('span');
          m.className = 'ia-uso-modelo';
          m.textContent = info.modelo;
          atual.append(' · ', m);
        }
        atual.title = '';
        quandoEl.textContent = info.atualizado_em
          ? 'trocada em ' + new Date(info.atualizado_em).toLocaleDateString('pt-BR')
          : '';
        btnAbrir.textContent = 'Trocar';
        btnAbrir.className = 'btn-sm btn-ghost ia-abrir';
        if (selProv.querySelector(`option[value="${info.provedor}"]`)) selProv.value = info.provedor;
        campoModelo.value = info.modelo || '';
      } else {
        pill.className = 'pill off';
        pill.textContent = 'sem chave';
        atual.textContent = atual.dataset.sem || '';
        atual.title = '';
        btnAbrir.textContent = 'Configurar';
        btnAbrir.className = 'btn-sm btn-acento ia-abrir';
        campoModelo.value = '';
      }
      campoKey.value = '';
      caixaUrl.hidden = !eCustom();
      mostrarCampoChave(!salvo.provedor);
    }

    /* Botões "usar a chave de X": o servidor copia a credencial de um uso para
       o outro — a chave não passa por aqui. */
    function pintarReuso() {
      caixaReusar.innerHTML = '';
      const fontes = salvo.provedor ? [] : doadoras(alvo);
      caixaReusar.hidden = !fontes.length;
      if (!fontes.length) return;
      const rot = document.createElement('span');
      rot.className = 'ia-reusar-rot';
      rot.textContent = 'Já tem chave em outro uso:';
      caixaReusar.appendChild(rot);
      fontes.forEach(f => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn-sm btn-ghost';
        b.textContent = `Usar a de ${f.nome} (${NOME_PROV(f.provedor())})`;
        b.addEventListener('click', () => reusarDe(f, b));
        caixaReusar.appendChild(b);
      });
    }

    async function reusarDe(fonte, btn) {
      btn.disabled = true;
      erro('');
      try {
        await postar('/admin/ia/reusar', { de: fonte.alvo, para: alvo });
        toast('ok', `${nome} passou a usar a chave de ${fonte.nome}.`);
        fechar();
        await carregarEstado();
      } catch (e) {
        erro('Não deu para aproveitar a chave: ' + e.message);
        btn.disabled = false;
      }
    }

    /* Ao gravar uma chave nova, oferece aplicá-la nos usos que ainda estão
       vazios e que esse provedor também atende. Uso que já tem chave própria
       nunca é tocado — trocar um só continua sendo trocar um só. */
    function pintarReplicar() {
      const prov = selProv.value;
      const alvos = trilhas.filter(t => t.alvo !== alvo && !t.provedor() && ATENDE(prov, t.alvo));
      caixaReplicar.hidden = !alvos.length || !campoKey.value.trim();
      caixaReplicar.dataset.alvos = alvos.map(t => t.alvo).join(',');
      if (alvos.length) {
        txtReplicar.textContent = 'Usar esta chave também em ' +
          alvos.map(t => t.nome).join(' e ');
      }
    }

    function usarModelos(lista) {
      modelos = lista;
      if (lista.length) {
        dica(`${lista.length} modelo${lista.length === 1 ? '' : 's'} disponíve${lista.length === 1 ? 'l' : 'is'} — comece a digitar para filtrar.`, 'ok');
        combo.abrirSeFocado();
      } else {
        dica('Não deu para listar os modelos deste provedor. Digite o nome do modelo.', '');
      }
    }

    async function listarComChaveSalva() {
      if (!salvo.provedor) return;
      try {
        const r = await fetch('/admin/ia/modelos?alvo=' + alvo);
        const d = await r.json();
        if (!r.ok || d.erro) throw new Error(d.erro || r.status);
        usarModelos((d.modelos || []).map(m => m.valor));
      } catch (_) { /* sem lista: o campo de texto continua valendo */ }
    }

    async function listarComChaveNova() {
      const chave = campoKey.value.trim();
      if (!chave) return;
      dica('Conferindo a chave e buscando os modelos…', '');
      try {
        const d = await postar('/admin/ia/modelos-preview', {
          alvo,
          provedor: selProv.value,
          api_key: chave,
          base_url: campoUrl.value.trim(),
        });
        usarModelos((d.modelos || []).map(m => m.valor));
        erro('');
      } catch (e) {
        dica('', '');
        erro('A chave não foi aceita pelo provedor: ' + e.message);
      }
    }

    // ---- abrir / fechar ----
    function abrir() {
      fecharOutras(api);
      editor.hidden = false;
      btnAbrir.setAttribute('aria-expanded', 'true');
      erro('');
      pintarReuso();
      pintarReplicar();
      marcarPassos();
      (salvo.provedor ? campoModelo : campoKey).focus();
      if (!modelos.length) listarComChaveSalva();
    }

    function fechar() {
      editor.hidden = true;
      btnAbrir.setAttribute('aria-expanded', 'false');
      campoKey.value = '';
      erro('');
      // Descarta troca não confirmada: volta ao que está gravado (ou ao
      // primeiro provedor da lista, quando ainda não há nada salvo).
      if (salvo.provedor) selProv.value = salvo.provedor;
      else selProv.selectedIndex = 0;
      campoModelo.value = salvo.modelo || '';
      caixaUrl.hidden = !eCustom();
      caixaReplicar.hidden = true;
      mostrarCampoChave(!salvo.provedor);
    }

    // ---- salvar ----
    async function salvar(btn) {
      const chave = campoKey.value.trim();
      const modelo = campoModelo.value.trim();
      const trocouProvedor = selProv.value !== salvo.provedor;

      if (!salvo.provedor && !chave) { erro('Cole a chave do provedor para começar.'); campoKey.focus(); return; }
      if (trocouProvedor && !chave) { erro('Trocar de provedor pede a chave nova dele.'); mostrarCampoChave(true); return; }
      if (eCustom() && !campoUrl.value.trim()) { erro('Informe o endereço da API.'); campoUrl.focus(); return; }
      if (!modelo) { erro('Escolha ou digite o modelo.'); campoModelo.focus(); return; }

      btn.disabled = true;
      erro('');
      try {
        if (chave) {
          await postar('/admin/ia/credencial', {
            alvo, provedor: selProv.value, api_key: chave, base_url: campoUrl.value.trim(),
          });
        }
        if (modelo !== salvo.modelo) await postar('/admin/ia/modelo', { alvo, modelo });

        // Replicar a chave nova nos usos vazios marcados.
        let replicados = 0;
        if (chave && !caixaReplicar.hidden && checkReplicar.checked) {
          for (const outro of (caixaReplicar.dataset.alvos || '').split(',').filter(Boolean)) {
            try { await postar('/admin/ia/reusar', { de: alvo, para: outro }); replicados++; }
            catch (_) { /* um destino recusar não invalida o que já gravou */ }
          }
        }
        campoKey.value = '';
        toast('ok', replicados
          ? `${nome} atualizado — a chave também foi aplicada em mais ${replicados} uso${replicados > 1 ? 's' : ''}.`
          : `${nome} atualizado.`);
        fechar();
        await carregarEstado();
      } catch (e) {
        erro('Não foi possível salvar: ' + e.message);
      } finally {
        btn.disabled = false;
      }
    }

    // ---- eventos ----
    btnAbrir.addEventListener('click', () => (editor.hidden ? abrir() : fechar()));
    q('[data-cancelar]').addEventListener('click', fechar);
    q('[data-salvar]').addEventListener('click', e => salvar(e.currentTarget));
    q('[data-trocar-chave]').addEventListener('click', () => mostrarCampoChave(true));

    selProv.addEventListener('change', () => {
      caixaUrl.hidden = !eCustom();
      modelos = [];
      // Provedor novo, modelo sugerido dele — o de transcrição não é o mesmo
      // de conversa, e cada provedor nomeia do seu jeito.
      if (selProv.value !== salvo.provedor) {
        const sugerido = PADRAO(selProv.value, alvo);
        if (sugerido) campoModelo.value = sugerido;
      } else {
        campoModelo.value = salvo.modelo || '';
      }
      pintarReplicar();
      marcarPassos();
      if (selProv.value === salvo.provedor) { listarComChaveSalva(); return; }
      if (campoKey.value.trim()) listarComChaveNova();
      else dica('Cole a chave deste provedor para ver os modelos dele.', '');
    });

    let timerKey = null;
    campoKey.addEventListener('input', () => {
      marcarPassos();
      pintarReplicar();
      clearTimeout(timerKey);
      timerKey = setTimeout(listarComChaveNova, 400);
    });
    campoUrl.addEventListener('input', marcarPassos);
    campoModelo.addEventListener('input', marcarPassos);

    const api = {
      alvo, nome, fechar, pintarEstado, pintarReuso,
      provedor: () => salvo.provedor,
    };
    return api;
  }

  // -------------------------------------------------------------------------
  async function carregarEstado() {
    let d;
    try {
      const r = await fetch('/admin/ia/estado');
      d = await r.json();
      if (d.erro) throw new Error(d.erro);
    } catch (_) {
      geral.textContent = 'estado indisponível';
      return;
    }
    trilhas.forEach(t => t.pintarEstado(d[t.alvo] || {}));
    trilhas.forEach(t => t.pintarReuso());   // depois: depende do estado de todas
    resumoGeral();
  }

  carregarEstado();
}

/* Combobox local: um campo que aceita texto livre E mostra a lista do
   provedor filtrada enquanto se digita. Substitui o trio select + busca +
   input livre + opção "Outro (digitar)" — que era o ponto onde ninguém sabia
   qual dos três valia. Usa as classes .ac-* do autocomplete de clientes para
   a lista ter a mesma cara em todo o painel. */
function comboModelo(inp, obterLista) {
  const wrap = document.createElement('div');
  wrap.className = 'ac-wrap';
  inp.insertAdjacentElement('beforebegin', wrap);
  wrap.appendChild(inp);

  const lista = document.createElement('div');
  lista.className = 'ac-lista';
  lista.hidden = true;
  lista.setAttribute('role', 'listbox');
  wrap.appendChild(lista);

  let itens = [];
  let idx = -1;
  inp.setAttribute('aria-autocomplete', 'list');
  inp.setAttribute('aria-expanded', 'false');

  const fechar = () => {
    lista.hidden = true;
    lista.innerHTML = '';
    itens = [];
    idx = -1;
    inp.setAttribute('aria-expanded', 'false');
  };

  function destacar(novo) {
    idx = novo;
    [...lista.children].forEach((el, i) => {
      el.classList.toggle('sel', i === idx);
      el.setAttribute('aria-selected', i === idx ? 'true' : 'false');
    });
    if (idx >= 0) lista.children[idx].scrollIntoView({ block: 'nearest' });
  }

  function escolher(i) {
    if (!itens[i]) return;
    inp.value = itens[i];
    fechar();
    inp.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function mostrar() {
    const termo = inp.value.trim().toLowerCase();
    const todos = obterLista();
    itens = (termo ? todos.filter(m => m.toLowerCase().includes(termo)) : todos).slice(0, 40);
    if (!itens.length) { fechar(); return; }
    lista.innerHTML = '';
    idx = -1;
    itens.forEach((m, i) => {
      const el = document.createElement('div');
      el.className = 'ac-item ac-item-simples';
      el.setAttribute('role', 'option');
      el.setAttribute('aria-selected', 'false');
      el.textContent = m;
      el.addEventListener('mousedown', ev => { ev.preventDefault(); escolher(i); });
      el.addEventListener('mouseenter', () => destacar(i));
      lista.appendChild(el);
    });
    lista.hidden = false;
    inp.setAttribute('aria-expanded', 'true');
  }

  inp.addEventListener('input', mostrar);
  inp.addEventListener('focus', mostrar);
  inp.addEventListener('blur', fechar);
  inp.addEventListener('keydown', e => {
    if (lista.hidden) {
      if (e.key === 'ArrowDown') { e.preventDefault(); mostrar(); }
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); destacar(idx + 1 >= itens.length ? 0 : idx + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); destacar(idx <= 0 ? itens.length - 1 : idx - 1); }
    else if (e.key === 'Enter' && idx >= 0) { e.preventDefault(); escolher(idx); }
    else if (e.key === 'Escape') { e.stopPropagation(); fechar(); }
  });

  return { abrirSeFocado: () => { if (document.activeElement === inp) mostrar(); }, fechar };
}

async function postar(url, dados) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(dados),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.erro || d.detail || 'HTTP ' + r.status);
  return d;
}
