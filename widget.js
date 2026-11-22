(function () {
  const script = document.currentScript;
  if (!script) return;
  const endpoint = script.dataset.endpoint || new URL('/api/chat', script.src).href;
  const title = script.dataset.title || 'Ladi — NeuralVillage AI';
  const accent = script.dataset.accent || '#137c66';
  const conversation = [];

  const host = document.createElement('div');
  host.id = 'neuralvillage-supportbot';
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; --support-accent: ${accent}; }
    * { box-sizing: border-box; }
    button, input { font: inherit; }
    .launcher { position: fixed; right: 20px; bottom: 20px; z-index: 2147483000; border: 0; border-radius: 999px; padding: 14px 18px; color: white; background: var(--support-accent); font: 600 14px/1.2 system-ui,sans-serif; cursor: pointer; box-shadow: 0 6px 24px #0003; }
    .panel { position: fixed; right: 20px; bottom: 78px; z-index: 2147483000; width: min(360px, calc(100vw - 24px)); height: min(480px, calc(100vh - 110px)); display: none; flex-direction: column; border: 1px solid #d5dedb; border-radius: 14px; background: #fff; color: #17211e; font: 14px/1.45 system-ui,sans-serif; box-shadow: 0 16px 48px #0003; overflow: hidden; }
    .panel[open] { display: flex; }
    header { display:flex; align-items:center; justify-content:space-between; padding:14px 16px; background:var(--support-accent); color:#fff; font-weight:650; }
    .close { border:0; background:transparent; color:inherit; font-size:22px; cursor:pointer; }
    .messages { flex:1; overflow:auto; padding:14px; display:flex; flex-direction:column; gap:10px; }
    .bubble { max-width:90%; padding:10px 12px; border-radius:12px; background:#edf3f1; white-space:pre-wrap; overflow-wrap:anywhere; }
    .bubble.user { align-self:flex-end; background:#e2f2ed; }
    form { display:flex; gap:8px; padding:10px; border-top:1px solid #e5e9e7; }
    input { min-width:0; flex:1; border:1px solid #bbc8c3; border-radius:8px; padding:10px; color:#17211e; }
    form button { border:0; border-radius:8px; padding:0 14px; color:#fff; background:var(--support-accent); font-weight:600; cursor:pointer; }
    .note { margin:0; padding:0 12px 10px; color:#52615c; font-size:11px; }
  `;

  const panel = document.createElement('section');
  panel.className = 'panel';
  panel.setAttribute('aria-label', title);
  const header = document.createElement('header');
  const heading = document.createElement('span');
  heading.textContent = title;
  const close = document.createElement('button');
  close.className = 'close';
  close.type = 'button';
  close.setAttribute('aria-label', 'Close support chat');
  close.textContent = '×';
  header.append(heading, close);
  const messages = document.createElement('div');
  messages.className = 'messages';
  const form = document.createElement('form');
  const input = document.createElement('input');
  input.type = 'text';
  input.maxLength = 2000;
  input.autocomplete = 'off';
  input.placeholder = 'Ask a support question';
  input.setAttribute('aria-label', 'Your question');
  const send = document.createElement('button');
  send.type = 'submit';
  send.textContent = 'Send';
  form.append(input, send);
  const note = document.createElement('p');
  note.className = 'note';
  note.textContent = 'Don’t share passwords, verification codes, or private health details. Messages may be processed by Google Gemini.';
  panel.append(header, messages, form, note);

  const launcher = document.createElement('button');
  launcher.type = 'button';
  launcher.className = 'launcher';
  launcher.textContent = 'Chat with us';
  launcher.setAttribute('aria-expanded', 'false');
  root.append(style, panel, launcher);

  function addBubble(text, kind) {
    const bubble = document.createElement('div');
    bubble.className = `bubble ${kind || ''}`;
    bubble.textContent = text;
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
  }

  launcher.addEventListener('click', function () {
    panel.toggleAttribute('open');
    launcher.setAttribute('aria-expanded', panel.hasAttribute('open') ? 'true' : 'false');
    if (panel.hasAttribute('open')) input.focus();
  });
  close.addEventListener('click', function () {
    panel.removeAttribute('open');
    launcher.setAttribute('aria-expanded', 'false');
  });
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    addBubble(message, 'user');
    input.value = '';
    send.disabled = true;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 25000);
    try {
      const history = conversation.slice(-6);
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, history }),
        credentials: 'omit',
        signal: controller.signal,
      });
      const result = await response.json();
      const answer = result.answer || 'Support is temporarily unavailable.';
      addBubble(answer, '');
      conversation.push({ role: 'user', content: message }, { role: 'assistant', content: answer });
      if (conversation.length > 6) conversation.splice(0, conversation.length - 6);
    } catch (_) {
      addBubble(_.name === 'AbortError'
        ? 'That took too long. Please try again.'
        : 'Support is temporarily unavailable. Please try again later.', '');
    } finally {
      clearTimeout(timeoutId);
      send.disabled = false;
      input.focus();
    }
  });
})();
