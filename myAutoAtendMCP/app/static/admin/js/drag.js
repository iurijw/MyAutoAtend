/* Reordenação dos cards (drag & drop pelo grip ⠿), ordem no localStorage. */

const wrap = document.querySelector('.wrap');
const KEY = 'admin-ordem-cards';
const secoes = () => [...wrap.querySelectorAll(':scope > .drag-sec')];

// Restaura a ordem salva; seções que não estiverem na lista (cards novos) vão para o fim.
try {
  const salva = JSON.parse(localStorage.getItem(KEY) || 'null');
  if (Array.isArray(salva)){
    const porChave = {};
    secoes().forEach(s => porChave[s.dataset.sec] = s);
    salva.forEach(ch => { if (porChave[ch]){ wrap.appendChild(porChave[ch]); delete porChave[ch]; } });
    Object.values(porChave).forEach(s => wrap.appendChild(s));
  }
} catch(_){ /* ordem corrompida → fica a ordem padrão */ }

let arrastando = null;

// O card só vira "draggable" enquanto o mouse segura o grip — assim não
// atrapalha seleção de texto nem o clique de recolher do card-head.
wrap.querySelectorAll('.drag-grip').forEach(g => {
  const sec = g.closest('.drag-sec');
  g.addEventListener('click', e => e.stopPropagation());
  g.addEventListener('mousedown', () => sec.setAttribute('draggable', 'true'));
  g.addEventListener('mouseup', () => sec.removeAttribute('draggable'));
});

wrap.addEventListener('dragstart', e => {
  const sec = e.target.closest ? e.target.closest('.drag-sec') : null;
  if (!sec || sec.getAttribute('draggable') !== 'true'){ return; }
  arrastando = sec;
  sec.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', sec.dataset.sec);  // Firefox exige setData
});

wrap.addEventListener('dragover', e => {
  if (!arrastando) return;
  e.preventDefault();
  const alvo = e.target.closest ? e.target.closest('.drag-sec') : null;
  if (!alvo || alvo === arrastando) return;
  const r = alvo.getBoundingClientRect();
  const antes = e.clientY < r.top + r.height / 2;
  wrap.insertBefore(arrastando, antes ? alvo : alvo.nextSibling);
});

wrap.addEventListener('dragend', () => {
  if (!arrastando) return;
  arrastando.classList.remove('dragging');
  arrastando.removeAttribute('draggable');
  localStorage.setItem(KEY, JSON.stringify(secoes().map(s => s.dataset.sec)));
  arrastando = null;
});
