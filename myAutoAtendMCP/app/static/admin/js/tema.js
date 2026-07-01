/* Alternância de tema (claro/escuro). O tema já é aplicado antes do paint pelo
   script inline no <head>; aqui cuidamos do switch (na engrenagem), da
   persistência em localStorage e do acompanhamento da preferência do SO. */

const KEY = 'tema';
const row = document.getElementById('tema-row');
const meta = document.querySelector('meta[name="theme-color"]');
const COR = { dark: '#1a1510', light: '#f3ece0' };

const temaAtual = () =>
  document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';

function aplicar(tema) {
  document.documentElement.setAttribute('data-theme', tema);
  if (meta) meta.setAttribute('content', COR[tema] || COR.light);
  if (row) {
    const dark = tema === 'dark';
    row.classList.toggle('on', dark);
    row.setAttribute('aria-checked', dark ? 'true' : 'false');
  }
}

function alternar() {
  const novo = temaAtual() === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(KEY, novo); } catch (_) { /* localStorage indisponível */ }
  aplicar(novo);
}

aplicar(temaAtual());  // sincroniza o switch e a meta com o tema já aplicado

if (row) {
  row.addEventListener('click', alternar);
  row.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); alternar(); }
  });
}

// Sem escolha manual salva, acompanha o tema do sistema em tempo real.
try {
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
    if (localStorage.getItem(KEY)) return;   // a escolha do usuário tem prioridade
    aplicar(e.matches ? 'dark' : 'light');
  });
} catch (_) { /* navegador antigo sem addEventListener no MediaQueryList */ }
