/* Sessão expirada durante o uso do painel.

   As telas do painel falam com o servidor por fetch, e fetch não segue o
   redirect de login: sem isto, um cookie vencido viraria uma tela que
   silenciosamente para de atualizar. O servidor marca a resposta com
   X-Sessao: expirada (401) e aqui a página vai para /login guardando onde
   estava — depois de entrar, a pessoa volta para a mesma seção. */

const original = window.fetch.bind(window);

function irParaLogin() {
  const aqui = location.pathname + location.search + location.hash;
  location.replace('/login?next=' + encodeURIComponent(aqui));
}

window.fetch = async (...args) => {
  const r = await original(...args);
  if (r.status === 401 && r.headers.get('X-Sessao') === 'expirada') irParaLogin();
  return r;
};
