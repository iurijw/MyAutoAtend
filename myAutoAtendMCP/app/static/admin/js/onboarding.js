/* Guia de primeiros passos — só existe no DOM na instalação nova (o Jinja não
   inclui o partial depois de concluído; ver admin.html).

   Não inventa rota nenhuma: cada passo grava pelo MESMO endpoint que a seção
   correspondente usa (/admin/whatsapp/*, /admin/servico, /admin/config,
   /admin/ia/*). Passo em branco é passo pulado — ninguém fica preso aqui. */

import { toast } from './toast.js';

const ob = document.getElementById('ob');
if (ob) iniciar();

function iniciar() {
  const TOTAL = 5;                       // telas de configuração (a última é o fim)
  const q = (id) => document.getElementById(id);
  const telas = [...ob.querySelectorAll('.ob-tela')];
  const passos = [...ob.querySelectorAll('.ob-passo')];
  const feitos = new Set();
  let atual = 0;
  let qrTimer = null;
  let conectado = false;

  const TITULOS = [
    ['Conectar o WhatsApp',
     'Abra o WhatsApp do negócio, vá em Aparelhos conectados e aponte a câmera para o código.'],
    ['Cadastrar um serviço',
     'O que você faz, quanto tempo leva e quanto custa — é assim que o bot monta a agenda.'],
    ['Horário de atendimento',
     'Fora dessas faixas o bot não marca nada. É o cerco da agenda.'],
    ['Seu telefone',
     'O número que manda no bot. Ele obedece a você e a mais ninguém.'],
    ['Ligar o agente',
     'A chave de IA que faz o bot pensar. Sem ela, ele lê as mensagens e não responde.'],
    ['Pronto para atender', 'Configuração terminada — dá para mexer em tudo isso depois.'],
  ];
  const ACAO = ['Continuar', 'Salvar e continuar', 'Salvar e continuar',
                'Salvar e continuar', 'Salvar e continuar', 'Começar a usar'];

  // ---- navegação -----------------------------------------------------------
  function pintar() {
    telas.forEach(t => { t.hidden = Number(t.dataset.tela) !== atual; });
    passos.forEach(p => {
      const n = Number(p.dataset.passo);
      p.classList.toggle('atual', n === atual);
      p.classList.toggle('feito', feitos.has(n));
    });
    // o cartão inteiro veste o acento da seção do passo (o fim volta ao verde)
    const acento = passos[Math.min(atual, TOTAL - 1)].dataset.accent;
    ob.setAttribute('data-accent', atual >= TOTAL ? 'zap' : acento);

    q('ob-contador').textContent = atual >= TOTAL ? 'Tudo certo' : `Passo ${atual + 1} de ${TOTAL}`;
    q('ob-titulo').textContent = TITULOS[atual][0];
    q('ob-desc').textContent = TITULOS[atual][1];
    q('ob-avancar').textContent = ACAO[atual];
    q('ob-voltar').hidden = atual === 0 || atual >= TOTAL;
    q('ob-pular').hidden = atual >= TOTAL;
    q('ob-pole-cheio').style.height = Math.round((atual / TOTAL) * 100) + '%';
    erro('');
  }

  function erro(msg) {
    const el = q('ob-erro');
    el.textContent = msg;
    el.classList.toggle('visivel', !!msg);
  }

  function irPara(n) {
    atual = n;
    if (atual === 0) vigiarWhatsapp(); else pararVigia();
    if (atual === TOTAL) montarResumo();
    pintar();
  }

  // ---- passo 1: WhatsApp ---------------------------------------------------
  function pararVigia() { clearInterval(qrTimer); qrTimer = null; }

  function mostrarConectado(perfil) {
    conectado = true;
    feitos.add(0);
    pararVigia();
    // conectado: o QR e as instruções dele não têm mais o que fazer na tela
    q('ob-qr-moldura').hidden = true;
    q('ob-qr-novo').hidden = true;
    q('ob-qr-dica').hidden = true;
    q('ob-estado-txt').textContent = 'conectado';
    q('ob-estado').classList.add('ok');
    const box = q('ob-conectado');
    box.hidden = false;
    q('ob-conectado-nome').textContent = (perfil && perfil.nome) || 'WhatsApp conectado';
    q('ob-conectado-num').textContent = (perfil && (perfil.numero_fmt || perfil.numero)) || '';
    q('ob-ava').textContent = ((perfil && perfil.nome) || 'W').trim().charAt(0).toUpperCase();
    if (perfil && perfil.foto) {
      q('ob-ava').style.backgroundImage = `url('${perfil.foto}')`;
      q('ob-ava').classList.add('loaded');
    }
    if (atual === 0) pintar();
  }

  async function verEstado() {
    try {
      const d = await (await fetch('/admin/whatsapp/estado')).json();
      if (d.erro) { q('ob-estado-txt').textContent = 'Evolution API fora do ar'; return null; }
      const st = (d.instance && d.instance.state) || 'close';
      if (st === 'open') { mostrarConectado(d.perfil); return st; }
      q('ob-estado-txt').textContent = st === 'connecting' ? 'esperando a leitura' : 'desconectado';
      return st;
    } catch (_) {
      q('ob-estado-txt').textContent = 'sem resposta do servidor';
      return null;
    }
  }

  async function pedirQr() {
    if (conectado) return;
    q('ob-qr-vazio').textContent = 'Gerando o código…';
    try {
      const d = await (await fetch('/admin/whatsapp/qr')).json();
      if (d.instance && d.instance.state === 'open') { verEstado(); return; }
      const b64 = d.base64 || '';
      if (!b64) {
        q('ob-qr-vazio').textContent = d.erro
          ? 'Não deu para gerar o código: ' + d.erro
          : 'Sem código agora. Tente gerar outro.';
        return;
      }
      q('ob-qr').src = b64.startsWith('data:') ? b64 : 'data:image/png;base64,' + b64;
      q('ob-qr').classList.add('pronto');
      q('ob-qr-vazio').textContent = '';
    } catch (_) {
      q('ob-qr-vazio').textContent = 'Falha de conexão ao gerar o código.';
    }
  }

  async function vigiarWhatsapp() {
    if (conectado || qrTimer) return;
    const st = await verEstado();
    if (st !== 'open') await pedirQr();
    qrTimer = setInterval(verEstado, 4000);   // pareou lá → a tela reage sozinha
  }

  // ---- passos 2 a 4: gravação ---------------------------------------------
  const val = (id) => (q(id).value || '').trim();

  async function enviar(url, campos) {
    const fd = new FormData();
    Object.entries(campos).forEach(([k, v]) => fd.append(k, v));
    const r = await fetch(url, { method: 'POST', body: fd, redirect: 'manual',
                                 headers: { Accept: 'application/json' } });
    if (r.type === 'opaqueredirect' || r.ok) return null;
    const d = await r.json().catch(() => ({}));
    return d.detail || d.erro || `Não deu para salvar (HTTP ${r.status}).`;
  }

  /* Devolve null quando salvou (ou quando não havia nada para salvar) e uma
     mensagem quando o servidor recusou. */
  async function salvarPasso(n) {
    if (n === 1) {
      const nome = val('ob-serv-nome');
      if (!nome) return null;                       // deixou em branco = pular
      const dur = Number(val('ob-serv-dur') || 0);
      const valor = Number(val('ob-serv-valor') || 0);
      if (!dur) return 'Diga quantos minutos o serviço leva.';
      const falha = await enviar('/admin/servico', {
        nome, descricao: val('ob-serv-desc'), valor, duracao_min: dur,
      });
      if (!falha) { feitos.add(1); toast('ok', `"${nome}" entrou no catálogo.`); }
      return falha;
    }

    if (n === 2) {
      const dias = [...ob.querySelectorAll('.ob-dia')]
        .filter(b => b.getAttribute('aria-pressed') === 'true')
        .map(b => Number(b.dataset.dia));
      // Nenhum dia marcado = passo pulado. O POST é replace-all: mandar vazio
      // apagaria a grade padrão que o primeiro boot semeou.
      if (!dias.length) return null;

      const turnos = [[val('ob-m1'), val('ob-m2')], [val('ob-t1'), val('ob-t2')]]
        .filter(([a, b]) => a && b);
      if (!turnos.length) return 'Preencha pelo menos o horário da manhã.';
      if (turnos.some(([a, b]) => b <= a)) return 'Cada turno tem que terminar depois de começar.';

      const fd = new FormData();
      dias.forEach(d => turnos.forEach(([ini, f]) => {
        fd.append('dia', d); fd.append('inicio', ini); fd.append('fim', f);
      }));
      const r = await fetch('/admin/horarios', { method: 'POST', body: fd,
        redirect: 'manual', headers: { Accept: 'application/json' } });
      if (!(r.type === 'opaqueredirect' || r.ok)) {
        const d = await r.json().catch(() => ({}));
        return d.detail || `Não deu para salvar (HTTP ${r.status}).`;
      }
      feitos.add(2);
      toast('ok', `Atendimento em ${dias.length} ${dias.length === 1 ? 'dia' : 'dias'} da semana.`);
      return null;
    }

    if (n === 3) {
      const tel = val('ob-dono').replace(/\D/g, '');
      if (!tel) return null;
      if (tel.length < 10) return 'Esse número está curto — confira o DDD.';
      const falha = await enviar('/admin/config', {
        telefone_dono: tel, fuso: (window.__ADMIN__ || {}).fuso || 'America/Sao_Paulo',
        avisar_dono: 'true',
      });
      if (!falha) { feitos.add(3); toast('ok', 'Telefone do dono salvo.'); }
      return falha;
    }

    if (n === 4) {
      const chave = val('ob-ia-chave');
      if (!chave) return null;
      const falha = await enviar('/admin/ia/credencial', {
        alvo: 'texto', provedor: q('ob-ia-prov').value,
        api_key: chave, base_url: val('ob-ia-url'),
      });
      if (falha) return falha;
      const modelo = val('ob-ia-modelo');
      if (modelo) await enviar('/admin/ia/modelo', { alvo: 'texto', modelo });
      feitos.add(4);
      toast('ok', 'Agente ligado.');
      return null;
    }
    return null;
  }

  function montarResumo() {
    const linhas = [
      [0, 'WhatsApp conectado', 'Conecte o WhatsApp na seção Conexão'],
      [1, 'Serviço cadastrado', 'Cadastre um serviço em Serviços'],
      [2, 'Horário de atendimento definido', 'Monte a grade em Horários'],
      [3, 'Telefone do dono salvo', 'Informe seu telefone em Configurações'],
      [4, 'Agente com chave de IA', 'Salve a chave de IA em Agente de IA'],
    ];
    q('ob-resumo').innerHTML = linhas.map(([n, feito, falta]) => `
      <li class="${feitos.has(n) ? 'ok' : 'pendente'}">
        <span class="ob-resumo-marca">${feitos.has(n) ? '✓' : '·'}</span>
        <span>${feitos.has(n) ? feito : falta}</span>
      </li>`).join('');
  }

  // ---- fechar --------------------------------------------------------------
  async function fechar(recarregar) {
    pararVigia();
    try {
      await fetch('/admin/onboarding/concluir', { method: 'POST', headers: { Accept: 'application/json' } });
    } catch (_) { /* fechar a tela é mais importante que registrar */ }
    if (recarregar) { location.reload(); return; }
    ob.hidden = true;
    document.body.classList.remove('ob-aberto');
  }

  // ---- provedores de IA ----------------------------------------------------
  const provs = (window.__ADMIN__ || {}).provedores || {};
  // O tojson do Jinja entrega em ordem alfabética, o que deixaria a Anthropic
  // como padrão. OpenAI primeiro: é o provedor dos modelos padrão do projeto
  // e o único que atende os três usos (texto, áudio e imagem).
  const ordenados = Object.entries(provs)
    .filter(([, p]) => p.texto)
    .sort(([a], [b]) => (a === 'openai' ? -1 : b === 'openai' ? 1 : a.localeCompare(b)));
  q('ob-ia-prov').innerHTML = ordenados
    .map(([id, p]) => `<option value="${id}">${p.nome}</option>`).join('');
  q('ob-ia-prov').addEventListener('change', () => {
    // provedor sem base_url no preset é o "Personalizado": aí a URL é obrigatória
    const personalizado = !(provs[q('ob-ia-prov').value] || {}).base_url;
    q('ob-ia-url').hidden = !personalizado;
    q('ob-ia-url-rot').hidden = !personalizado;
  });

  // ---- eventos -------------------------------------------------------------
  q('ob-avancar').addEventListener('click', async () => {
    if (atual >= TOTAL) { fechar(true); return; }
    const btn = q('ob-avancar');
    btn.disabled = true;
    const falha = await salvarPasso(atual);
    btn.disabled = false;
    if (falha) { erro(falha); return; }
    irPara(atual + 1);
  });
  q('ob-voltar').addEventListener('click', () => irPara(Math.max(0, atual - 1)));
  // dias da semana: botão com aria-pressed em vez de checkbox — a chave
  // liga/desliga do painel é grande demais para sete numa linha só
  ob.querySelectorAll('.ob-dia').forEach(b => b.addEventListener('click', () => {
    b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
  }));
  q('ob-qr-novo').addEventListener('click', pedirQr);
  q('ob-pular').addEventListener('click', () => {
    // Pular é decisão consciente: nada é gravado além da marca de "já vi".
    fechar(feitos.size > 0);
  });
  passos.forEach(p => p.addEventListener('click', () => {
    const n = Number(p.dataset.passo);
    if (n < atual || feitos.has(n)) irPara(n);   // só volta; avançar é pelo botão
  }));

  ob.hidden = false;
  document.body.classList.add('ob-aberto');
  q('ob-ia-prov').dispatchEvent(new Event('change'));
  irPara(0);
}
