/* Navegação do painel: a barra lateral troca a seção visível.

   O servidor entrega TODAS as views em /admin; aqui só uma fica com a classe
   .ativa por vez, escolhida pelo hash da URL (#conversas, #clientes…). Usar o
   hash é o que faz a seção sobreviver ao reload que os forms disparam depois
   de salvar, e deixa cada seção linkável. Abaixo de 1000px a lateral vira
   gaveta (botão ☰ no topo). */

const INICIAL = 'conversas';

const side = document.getElementById('side');
const veu = document.getElementById('side-veu');
const btnMenu = document.getElementById('topo-menu');
const topo = document.getElementById('topo');
const elEyebrow = document.getElementById('topo-eyebrow');
const elTitulo = document.getElementById('topo-titulo');
const elDesc = document.getElementById('topo-desc');
const conteudo = document.getElementById('conteudo');

const views = [...document.querySelectorAll('.view')];
const itens = [...document.querySelectorAll('.nav-item')];

const chaveDaView = (v) => v.id.replace(/^view-/, '');
const existe = (chave) => views.some(v => chaveDaView(v) === chave);

function mostrar(chave) {
  const alvo = existe(chave) ? chave : INICIAL;
  let ativa = null;
  views.forEach(v => {
    const on = chaveDaView(v) === alvo;
    v.classList.toggle('ativa', on);
    if (on) ativa = v;
  });
  itens.forEach(a => {
    const on = a.getAttribute('href') === '#' + alvo;
    a.classList.toggle('ativo', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  if (!ativa) return;

  // O topo veste o acento da seção (fio sob a barra, eyebrow e foco dos campos).
  topo.setAttribute('data-accent', ativa.dataset.accent || 'neutro');
  elEyebrow.textContent = ativa.dataset.grupo || '';
  elTitulo.textContent = ativa.dataset.titulo || '';
  elDesc.textContent = ativa.dataset.desc || '';
  document.title = (ativa.dataset.titulo || 'Painel') + ' · myAutoAtend';
  conteudo.scrollTop = 0;
  window.scrollTo({ top: 0, behavior: 'auto' });
}

const doHash = () => (location.hash || '').replace(/^#/, '');

window.addEventListener('hashchange', () => { mostrar(doHash()); fecharGaveta(); });
mostrar(doHash());

// ---------------------------------------------------------------------------
// Gaveta (telas estreitas)
// ---------------------------------------------------------------------------

function abrirGaveta() {
  side.classList.add('aberta');
  veu.hidden = false;
  btnMenu.setAttribute('aria-expanded', 'true');
}

function fecharGaveta() {
  side.classList.remove('aberta');
  veu.hidden = true;
  btnMenu.setAttribute('aria-expanded', 'false');
}

btnMenu.addEventListener('click', () => {
  if (side.classList.contains('aberta')) fecharGaveta(); else abrirGaveta();
});
veu.addEventListener('click', fecharGaveta);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && side.classList.contains('aberta')) fecharGaveta();
});
// Clicar no mesmo item que já está aberto não dispara hashchange — fecha aqui.
itens.forEach(a => a.addEventListener('click', fecharGaveta));

/* Abre uma seção de qualquer lugar do painel (ex.: atalhos entre cards). */
export function irPara(chave) {
  if (doHash() === chave) mostrar(chave);
  else location.hash = '#' + chave;
}
window.irParaSecao = irPara;
