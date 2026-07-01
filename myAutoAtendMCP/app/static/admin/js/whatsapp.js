/* Card "Conexão WhatsApp": estado da instância, QR Code e desconexão. */

import { toast } from './toast.js';

const elStatus  = document.getElementById('wa-status');
const elHint    = document.getElementById('wa-hint');
const elQr      = document.getElementById('wa-qr');
const elCode    = document.getElementById('wa-code');
const elMsg     = document.getElementById('wa-msg');
const btnCon    = document.getElementById('wa-connect');
const btnOut    = document.getElementById('wa-logout');
const elCard    = document.getElementById('wa-card');
const elHead    = document.getElementById('wa-head');
let pollTimer   = null;
let wasOpen     = false;

function pill(cls, txt){ elStatus.className = 'pill ' + cls; elStatus.textContent = txt; }

function showQrBox(on){
  elQr.style.display   = on ? '' : 'none';
  elCode.style.display = (on && elCode.textContent) ? '' : 'none';
  elHint.style.display = on ? 'none' : '';
}

function render(state){
  if (state === 'open'){
    pill('on', 'conectado');
    elHint.textContent = 'WhatsApp conectado.';
    showQrBox(false);
    elMsg.innerHTML = 'Número conectado e atendendo. Para trocar de número, desconecte primeiro.';
    btnCon.style.display = 'none';
    btnOut.style.display = '';
    if (!wasOpen) elCard.classList.add('collapsed');  // recolhe ao conectar
    wasOpen = true;
  } else if (state === 'connecting'){
    wasOpen = false;
    elCard.classList.remove('collapsed');
    pill('connecting', 'aguardando leitura');
    btnCon.style.display = '';
    btnCon.textContent = 'Gerar novo QR';
    btnOut.style.display = '';
  } else {
    pill('off', 'desconectado');
    showQrBox(false);
    elHint.textContent = 'Sem conexão. Gere o QR Code para parear.';
    btnCon.style.display = '';
    btnCon.textContent = 'Gerar QR Code';
    btnOut.style.display = 'none';
    wasOpen = false;
    elCard.classList.remove('collapsed');
  }
}

async function estado(){
  try {
    const r = await fetch('/admin/whatsapp/estado');
    const d = await r.json();
    if (d.erro){ pill('off', 'erro'); elHint.textContent = 'Evolution API indisponível.'; return null; }
    const st = (d.instance && d.instance.state) || 'close';
    render(st);
    if (st === 'open' && pollTimer){ clearInterval(pollTimer); pollTimer = null; }
    return st;
  } catch(e){ pill('off', 'erro'); return null; }
}

function startPolling(){
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(estado, 4000);
}

async function gerarQr(){
  btnCon.disabled = true; elHint.textContent = 'Gerando QR Code…'; elHint.style.display = '';
  try {
    const r = await fetch('/admin/whatsapp/qr');
    const d = await r.json();
    if (d.erro){ elHint.textContent = 'Falha: ' + d.erro; return; }
    if (d.instance && d.instance.state === 'open'){ render('open'); return; }
    const b64 = d.base64 || '';
    if (b64){
      elQr.src = b64.startsWith('data:') ? b64 : ('data:image/png;base64,' + b64);
      elCode.textContent = d.pairingCode || '';
      showQrBox(true);
      render('connecting');
      startPolling();
    } else {
      elHint.textContent = 'Resposta sem QR. Tente novamente.';
    }
  } catch(e){ elHint.textContent = 'Erro ao gerar QR Code.'; }
  finally { btnCon.disabled = false; }
}

async function desconectar(){
  if (!confirm('Desconectar o WhatsApp deste número?')) return;
  btnOut.disabled = true;
  try {
    const r = await fetch('/admin/whatsapp/desconectar', {method:'POST'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch(e){ toast('erro', 'Não foi possível desconectar agora. Tente de novo em instantes.'); }
  finally { btnOut.disabled = false; estado(); }
}

btnCon.addEventListener('click', gerarQr);
btnOut.addEventListener('click', desconectar);
elHead.addEventListener('click', () => elCard.classList.toggle('collapsed'));
estado();
