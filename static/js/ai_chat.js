/* ── AI Research Assistant (Perplexity) ──────────────────────────────────── */

let _aiOpen = false;

function toggleAiPanel() {
  _aiOpen = !_aiOpen;
  document.getElementById('ai-panel').classList.toggle('hidden', !_aiOpen);
  if (_aiOpen) document.getElementById('ai-input').focus();
}

function aiSuggest(btn) {
  document.getElementById('ai-input').value = btn.textContent;
  sendAiMessage();
}

function sendAiMessage() {
  const input = document.getElementById('ai-input');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';

  // Build context from currently selected instrument
  const symbol  = document.getElementById('detail-symbol-badge')?.textContent || '';
  const title   = document.getElementById('detail-title')?.textContent || '';
  const bias    = document.getElementById('ab-bias')?.textContent || '';
  const setup   = document.getElementById('ab-setup')?.textContent || '';
  const last    = document.getElementById('ctx-last')?.textContent || '';
  const context = symbol ? `${title} (${symbol}), Bias: ${bias}, Setup: ${setup}, Last: ${last}` : '';

  _appendMessage('user', question);
  _appendMessage('assistant', '…', 'pending');

  fetch('/api/ai/research', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, context }),
  })
    .then(r => r.json())
    .then(data => {
      _replacePending(data.answer || data.error || 'No response');
    })
    .catch(() => _replacePending('Connection error — please retry.'));
}

function _appendMessage(role, text, cls = '') {
  const msgs = document.getElementById('ai-messages');
  const div = document.createElement('div');
  div.className = `ai-msg ai-msg-${role}${cls ? ' ' + cls : ''}`;
  div.textContent = text;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function _replacePending(text) {
  const msgs = document.getElementById('ai-messages');
  const pending = msgs.querySelector('.pending');
  if (pending) {
    pending.textContent = text;
    pending.classList.remove('pending');
  }
}
