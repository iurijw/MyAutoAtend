/* Avatares do WhatsApp nas listagens (foto de perfil por número).

   Exporta `pintarAvatares(raiz)` porque a tabela de agendamentos se repinta ao
   vivo (js/agendamentos.js): as linhas novas precisam da foto de novo, sem
   rebater na Evolution para número que já foi buscado. */

// número → Promise<url|null>. Promise (e não valor) para que duas chamadas
// simultâneas do mesmo número compartilhem a MESMA requisição.
const pedidos = {};

function foto(num) {
  if (!(num in pedidos)) {
    pedidos[num] = fetch('/admin/whatsapp/foto?numero=' + encodeURIComponent(num))
      .then(r => (r.ok ? r.json() : null))
      .then(d => (d && d.url) || null)
      .catch(() => null);      // falha de rede → mantém o fallback de inicial
  }
  return pedidos[num];
}

export function pintarAvatares(raiz = document) {
  // Agrupa por número p/ buscar a foto uma vez só por cliente.
  const porNumero = {};
  raiz.querySelectorAll('.ava[data-num]').forEach(el => {
    if (!el.dataset.num) return;
    (porNumero[el.dataset.num] = porNumero[el.dataset.num] || []).push(el);
  });
  Object.entries(porNumero).forEach(async ([num, els]) => {
    const url = await foto(num);
    if (!url) return;          // sem foto / privada → fica a inicial
    els.forEach(el => {
      el.style.backgroundImage = "url('" + url + "')";
      el.classList.add('loaded');
    });
  });
}

pintarAvatares();
