/* Alternância de tema (claro/escuro; padrão do painel é o escuro). O tema já
   é aplicado antes do paint pelo script inline no <head>; aqui cuidamos do
   botão (no pé da barra lateral) e da persistência em localStorage. */

const KEY = 'tema';
const btn = document.getElementById('tema-toggle');
const rotulo = document.getElementById('tema-rotulo');
const meta = document.querySelector('meta[name="theme-color"]');
const COR = { dark: '#1a1510', light: '#f3ece0' };

const temaAtual = () =>
  document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';

function aplicar(tema) {
  document.documentElement.setAttribute('data-theme', tema);
  if (meta) meta.setAttribute('content', COR[tema] || COR.light);
  const dark = tema === 'dark';
  if (rotulo) rotulo.textContent = dark ? 'Tema escuro' : 'Tema claro';
  if (btn) {
    btn.setAttribute('aria-pressed', dark ? 'true' : 'false');
    btn.title = dark ? 'Mudar para o tema claro' : 'Mudar para o tema escuro';
  }
}

function alternar() {
  const novo = temaAtual() === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(KEY, novo); } catch (_) { /* localStorage indisponível */ }
  aplicar(novo);
}

aplicar(temaAtual());  // sincroniza rótulo e meta com o tema já aplicado
btn?.addEventListener('click', alternar);
