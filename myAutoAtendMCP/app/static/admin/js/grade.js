/* Grade de cards (Gridstack, vendor local): os cards ocupam a janela toda,
   arrastam pelo grip ⠿ e redimensionam pelas bordas/canto. Layout salvo por
   navegador (localStorage). Substitui o antigo drag.js (reordenação vertical).
   A visibilidade continua com o gear.js — aqui só sincronizamos a grade
   quando um card é ocultado/reexibido (evento admin:cards-ocultos). */

const KEY = 'admin-grade-v1';
const CELULA = 30;   // px por linha da grade
const MARGEM = 22;   // vão entre cards (mesmo respiro do painel antigo empilhado)

const wrap = document.querySelector('.wrap');
const secs = [...wrap.querySelectorAll(':scope > .drag-sec')];
if (!secs.length || typeof GridStack === 'undefined') {
  console.warn('grade: gridstack indisponível — layout fica no fluxo normal');
} else {
  iniciar();
}

function iniciar() {
  let salvo = {};
  try { salvo = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (_) { /* layout corrompido → padrão */ }

  // contêiner da grade no lugar dos cards (header/stats ficam fora)
  const grade = document.createElement('div');
  grade.className = 'grid-stack';
  secs[0].before(grade);

  // Pré-ordena pela posição salva (y, depois x): o gridstack posiciona na
  // ordem do DOM, e montar "de baixo pra cima" cascateia colisões que
  // embaralham o layout no F5. Sem posição salva → fim (auto-position).
  const ordenados = [...secs].sort((a, b) => {
    const pa = salvo[a.dataset.sec], pb = salvo[b.dataset.sec];
    if (!pa && !pb) return 0;
    if (!pa) return 1;
    if (!pb) return -1;
    return (pa.y - pb.y) || (pa.x - pb.x);
  });

  ordenados.forEach(s => {
    // gridstack exige item > content; o miolo do card desce pro content
    const conteudo = document.createElement('div');
    conteudo.className = 'grid-stack-item-content';
    while (s.firstChild) conteudo.appendChild(s.firstChild);
    s.appendChild(conteudo);
    s.classList.add('grid-stack-item');
    s.setAttribute('gs-id', s.dataset.sec);
    grade.appendChild(s);

    const pos = salvo[s.dataset.sec];
    if (pos) {
      s.setAttribute('gs-x', pos.x); s.setAttribute('gs-y', pos.y);
      s.setAttribute('gs-w', pos.w); s.setAttribute('gs-h', pos.h);
    } else {
      // primeira visita: card em linha cheia com a altura do conteúdo atual
      const px = conteudo.offsetHeight || 0;
      const h = px ? Math.max(4, Math.round((px + MARGEM) / (CELULA + MARGEM))) : 10;
      s.setAttribute('gs-w', '12');
      s.setAttribute('gs-h', String(h));
      s.setAttribute('gs-auto-position', 'true');
    }
  });

  const grid = GridStack.init({
    cellHeight: CELULA,
    margin: MARGEM,
    column: 12,
    float: true,
    handle: '.drag-grip',
    resizable: { handles: 'e, se, s, sw, w' },
    columnOpts: { breakpoints: [{ w: 900, c: 1 }] },  // 1 coluna no mobile
  }, grade);

  // Restore canônico por id: reafirma as posições salvas depois do init —
  // imune a qualquer colisão/ajuste que o engine tenha feito na montagem.
  // O `false` é essencial: sem ele o load() REMOVE widgets fora da lista.
  const comPos = ordenados.filter(s => salvo[s.dataset.sec]);
  if (comPos.length) {
    grid.load(comPos.map(s => ({ id: s.dataset.sec, ...salvo[s.dataset.sec] })), false);
  }

  // cards já ocultos no boot saem da grade (o DOM fica, o gear reexibe)
  secs.forEach(s => {
    if (s.classList.contains('sec-oculta') && s.gridstackNode) grid.removeWidget(s, false, false);
  });

  // só persiste o layout de 12 colunas — o modo 1 coluna (mobile) é derivado
  const salvar = () => {
    if (grid.getColumn() !== 12) return;
    secs.forEach(s => {
      const n = s.gridstackNode;
      if (n) salvo[s.dataset.sec] = { x: n.x, y: n.y, w: n.w, h: n.h };
    });
    localStorage.setItem(KEY, JSON.stringify(salvo));
  };
  // salva SÓ em gesto do usuário (nunca no 'change': o engine dispara change
  // em ajustes da montagem e persistiria um layout transitório errado)
  grid.on('dragstop resizestop', () => salvar());

  // Ponto fixo: persiste UMA vez o layout já resolvido pela montagem. Se o
  // salvo tinha sobreposição (herança de versões antigas), o engine empurra
  // um card — sem gravar o resultado, TODO F5 repetiria o empurrão. Gravando,
  // a tela atual == o que o próximo F5 carrega, e o layout estabiliza.
  salvar();

  // gear.js avisa quando a lista de ocultos muda
  document.addEventListener('admin:cards-ocultos', e => {
    const ocultos = e.detail || [];
    secs.forEach(s => {
      const esconder = ocultos.includes(s.dataset.sec);
      const ativo = !!s.gridstackNode;
      if (esconder && ativo) grid.removeWidget(s, false, false);
      else if (!esconder && !ativo) grid.makeWidget(s);
    });
    salvar();
  });
}
