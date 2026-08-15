/* Tela de entrada. O formulário é nativo (POST /login → 303): sem JS a pessoa
   entra do mesmo jeito. Aqui só o tema (mesmo botão do painel, via tema.js) e
   o olho da senha. */

import './tema.js';

const senha = document.getElementById('log-senha');
const olho = document.getElementById('log-olho');
const icone = olho?.querySelector('use');

olho?.addEventListener('click', () => {
  const mostrando = senha.type === 'text';
  senha.type = mostrando ? 'password' : 'text';
  olho.setAttribute('aria-pressed', mostrando ? 'false' : 'true');
  olho.setAttribute('aria-label', mostrando ? 'Mostrar senha' : 'Ocultar senha');
  icone?.setAttribute('href', mostrando ? '#i-olho' : '#i-olho-off');
  senha.focus();
});
