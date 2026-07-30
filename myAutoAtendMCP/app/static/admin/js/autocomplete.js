/* Autocomplete de cliente num campo de nome: sugere contatos já cadastrados
   (GET /admin/clientes/buscar) para o dono não recadastrar quem já existe.
   Hoje usado no modal "Novo agendamento".

   Escolher um item chama o callback com {nome, telefone, telefone_fmt, ...} —
   quem liga decide o que preencher. Teclado: ↑/↓ navega, Enter escolhe (sem
   enviar o form), Esc fecha a lista sem fechar o modal em volta. */

const DEBOUNCE_MS = 200;

function criarLista(inp) {
  // O input é embrulhado para a lista poder ancorar logo abaixo dele.
  const wrap = document.createElement('div');
  wrap.className = 'ac-wrap';
  inp.insertAdjacentElement('beforebegin', wrap);
  wrap.appendChild(inp);

  const lista = document.createElement('div');
  lista.className = 'ac-lista';
  lista.hidden = true;
  lista.setAttribute('role', 'listbox');
  wrap.appendChild(lista);
  return lista;
}

export function ligarAutocompleteCliente(inp, aoEscolher) {
  if (!inp || inp._acLigado) return;
  inp._acLigado = true;

  const lista = criarLista(inp);
  let itens = [];      // clientes exibidos
  let idx = -1;        // item destacado
  let timer = null;
  let controle = null; // AbortController da busca em voo

  inp.setAttribute('aria-autocomplete', 'list');
  inp.setAttribute('aria-expanded', 'false');

  function fechar() {
    lista.hidden = true;
    lista.innerHTML = '';
    itens = [];
    idx = -1;
    inp.setAttribute('aria-expanded', 'false');
  }

  function destacar(novo) {
    idx = novo;
    [...lista.children].forEach((el, i) => {
      el.classList.toggle('sel', i === idx);
      el.setAttribute('aria-selected', i === idx ? 'true' : 'false');
    });
    if (idx >= 0) lista.children[idx].scrollIntoView({ block: 'nearest' });
  }

  function escolher(i) {
    const c = itens[i];
    if (!c) return;
    fechar();
    aoEscolher(c);
  }

  function linha(c, i) {
    const el = document.createElement('div');
    el.className = 'ac-item';
    el.setAttribute('role', 'option');
    el.setAttribute('aria-selected', 'false');

    const ava = document.createElement('span');
    ava.className = 'ava ac-ava';
    ava.textContent = (c.nome || '?').charAt(0).toUpperCase();
    el.appendChild(ava);

    const txt = document.createElement('div');
    txt.className = 'ac-txt';
    const nome = document.createElement('div');
    nome.className = 'ac-nome';
    nome.textContent = c.nome || 'Sem nome ainda';
    if (c.dono) {
      const tag = document.createElement('span');
      tag.className = 'tag-dono';
      tag.textContent = 'dono';
      nome.appendChild(document.createTextNode(' '));
      nome.appendChild(tag);
    }
    const meta = document.createElement('div');
    meta.className = 'ac-meta';
    meta.textContent = c.telefone_fmt +
      (c.agendamentos ? ' · ' + c.agendamentos + ' ativo' + (c.agendamentos === 1 ? '' : 's') : '');
    txt.appendChild(nome);
    txt.appendChild(meta);
    el.appendChild(txt);

    // mousedown em vez de click: o blur do input não pode fechar a lista antes.
    el.addEventListener('mousedown', ev => { ev.preventDefault(); escolher(i); });
    el.addEventListener('mouseenter', () => destacar(i));
    return el;
  }

  function mostrar(clientes) {
    if (!clientes.length) { fechar(); return; }
    itens = clientes;
    idx = -1;
    lista.innerHTML = '';
    clientes.forEach((c, i) => lista.appendChild(linha(c, i)));
    lista.hidden = false;
    inp.setAttribute('aria-expanded', 'true');
  }

  async function buscar() {
    const termo = inp.value.trim();
    if (termo.length < 2) { fechar(); return; }
    controle?.abort();
    controle = new AbortController();
    try {
      const r = await fetch('/admin/clientes/buscar?q=' + encodeURIComponent(termo), {
        headers: { Accept: 'application/json' }, signal: controle.signal,
      });
      if (!r.ok) throw new Error(r.status);
      const d = await r.json();
      mostrar(d.clientes || []);
    } catch (_) {
      // Busca é conveniência: falha/aborto só não sugere nada.
      if (!controle.signal.aborted) fechar();
    }
  }

  inp.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(buscar, DEBOUNCE_MS);
  });

  inp.addEventListener('keydown', e => {
    if (lista.hidden) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      destacar(idx + 1 >= itens.length ? 0 : idx + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      destacar(idx <= 0 ? itens.length - 1 : idx - 1);
    } else if (e.key === 'Enter' && idx >= 0) {
      e.preventDefault();       // Enter escolhe, não envia o form
      escolher(idx);
    } else if (e.key === 'Escape') {
      e.stopPropagation();      // fecha a lista, não o modal em volta
      fechar();
    }
  });

  inp.addEventListener('blur', () => { clearTimeout(timer); fechar(); });

  return { fechar };
}
