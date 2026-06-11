/* Card "Configuração geral": só o recolher/expandir (form é POST clássico). */

const card = document.getElementById('config-card');
document.getElementById('config-head').addEventListener('click', () => card.classList.toggle('collapsed'));
