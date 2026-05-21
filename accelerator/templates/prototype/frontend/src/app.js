/**
 * ai-prototype-accelerator — Chat UI
 * Static scaffold file — do NOT modify when processing spec.yaml.
 */

// ── State ────────────────────────────────────────────────────────────────────

let ws = null;
let config = {};
let isWaiting = false;
let currentAgentBubble = null;
let currentAgentBuffer = '';   // raw accumulated text for the current bubble
let reconnectTimer = null;
let currentScenario = null;
let currentScenarioStep = 0;
let hasStartedChat = false;

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  await loadConfig();
  applyTheme();
  renderHeader();
  renderStarterQuestions();
  renderScenarios();
  showWelcomeMessage();
  initResetButton();
  connectWebSocket();
  initInputHandlers();
});

// ── Config ────────────────────────────────────────────────────────────────────

async function loadConfig() {
  try {
    const res = await fetch('/config');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    config = await res.json();
    document.title = `${config.CUSTOMER_NAME || 'AI Prototype'} — ${config.AGENT_NAME || 'Assistant'}`;
  } catch (e) {
    console.warn('Failed to load /config, using fallback theme:', e);
    config = {
      CUSTOMER_NAME: 'AI Prototype',
      AGENT_NAME: 'AI Assistant',
      PRIMARY_COLOR: '#0078D4',
      ACCENT_COLOR: '#50E6FF',
      FONT_FAMILY: 'Arial, sans-serif',
      LOGO_URL: '',
      WELCOME_MESSAGE: 'Hello! How can I help you today?',
      PROTOTYPE_RESET: 'false',
    };
  }
}

function applyTheme() {
  const root = document.documentElement;
  const primary = config.PRIMARY_COLOR || '#0078D4';
  const accent  = config.ACCENT_COLOR  || '#50E6FF';
  const font    = config.FONT_FAMILY   || 'Arial, sans-serif';

  // Parse hex to rgb for derived variants
  function hexToRgb(hex) {
    const h = hex.replace('#', '');
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
    };
  }
  function darken(hex, pct) {
    const {r, g, b} = hexToRgb(hex);
    const f = 1 - pct;
    const toHex = v => Math.round(v * f).toString(16).padStart(2, '0');
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
  }

  const {r, g, b} = hexToRgb(primary);

  root.style.setProperty('--brand',       primary);
  root.style.setProperty('--brand-hover', darken(primary, 0.12));
  root.style.setProperty('--brand-light', `rgba(${r},${g},${b},0.10)`);
  root.style.setProperty('--brand-muted', `rgba(${r},${g},${b},0.08)`);
  root.style.setProperty('--accent',      accent);
  root.style.setProperty('--primary',     primary);
  document.body.style.fontFamily = font;

  // Update favicon with current brand color
  const favicon = document.getElementById('favicon');
  if (favicon) {
    const encoded = encodeURIComponent(primary);
    favicon.href = `data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='${encoded}'/><text y='.9em' font-size='80' x='10'>⚡</text></svg>`;
  }
}

// ── Header ────────────────────────────────────────────────────────────────────

function renderHeader() {
  const nameEl       = document.getElementById('customer-name');
  const subtitleEl   = document.getElementById('agent-subtitle');
  const logoEl       = document.getElementById('logo');
  const agentAvatar  = document.getElementById('agent-avatar');
  const agentNameHdr = document.getElementById('agent-name-header');

  const agentName = config.AGENT_NAME || 'Assistant';
  const useCaseTitle = config.USE_CASE_TITLE || config.AGENT_SUBTITLE || '';

  if (nameEl) nameEl.textContent = config.CUSTOMER_NAME || '';
  // SPEC_DRIVEN: use_case.title displayed as subtitle
  if (subtitleEl) subtitleEl.textContent = useCaseTitle
    ? `${agentName} — ${useCaseTitle}`
    : agentName;
  if (agentNameHdr) agentNameHdr.textContent = agentName;
  if (agentAvatar) {
    agentAvatar.textContent = agentName.charAt(0).toUpperCase();
    agentAvatar.style.color = config.PRIMARY_COLOR || '#0078D4';
  }

  if (logoEl && config.LOGO_URL) {
    logoEl.src = config.LOGO_URL;
    logoEl.alt = config.CUSTOMER_NAME || 'Logo';
    logoEl.classList.remove('hidden');
    logoEl.onerror = () => logoEl.classList.add('hidden');
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}/chat`;

  ws = new WebSocket(url);

  ws.onopen = () => {
    console.log('WebSocket connected');
    hideBanner('reconnect-banner');
    clearTimeout(reconnectTimer);
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleMessage(data);
    } catch (e) {
      console.error('Failed to parse message:', e, event.data);
    }
  };

  ws.onclose = () => {
    console.warn('WebSocket closed, reconnecting in 3s...');
    showBanner('reconnect-banner');
    reconnectTimer = setTimeout(connectWebSocket, 3000);
  };

  ws.onerror = (err) => {
    console.error('WebSocket error:', err);
  };
}

// ── Message handling ──────────────────────────────────────────────────────────

function handleMessage(data) {
  const type = data.type || 'text';

  switch (type) {
    case 'text':
      handleTextMessage(data);
      break;
    case 'confirmation':
      handleConfirmation(data);
      finishWaiting();
      break;
    case 'suggestions':
      handleSuggestions(data);
      finishWaiting();
      break;
    case 'error':
      handleError(data);
      finishWaiting();
      break;
    case 'handoff':
      handleHandoff(data);
      break;
    case 'system':
      appendSystemMessage(data.content || '');
      break;
    default:
      console.warn('Unknown message type:', type);
  }
}

function handleTextMessage(data) {
  const content = data.content || '';
  const done = data.done === true;

  if (!currentAgentBubble) {
    currentAgentBubble = createAgentBubble();
    currentAgentBuffer = '';
  }

  // Accumulate raw text in JS variable — DO NOT read back from DOM (textContent encodes special chars)
  currentAgentBuffer += content;

  const textEl = currentAgentBubble ? currentAgentBubble.querySelector('.bubble-text') : null;
  if (textEl) {
    textEl.textContent = currentAgentBuffer;
    scrollToBottom();
  }

  if (done) {
    // Capture refs before resetting state
    const bubble = currentAgentBubble;
    const rawBuffer = currentAgentBuffer;
    currentAgentBubble = null;
    currentAgentBuffer = '';

    // Strip markdown code fences and attempt to render structured JSON
    const finalTextEl = bubble ? bubble.querySelector('.bubble-text') : null;
    if (finalTextEl) {
      const stripped = rawBuffer.trim()
        .replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '').trim();
      // Try to extract a JSON object from anywhere in the text
      const jsonMatch = stripped.match(/(\{[\s\S]*\})/);
      let parsed = null;
      if (jsonMatch) {
        try { parsed = JSON.parse(jsonMatch[1]); } catch (_) {
          // LLM sometimes emits commas in numbers or JS comments — repair and retry
          const repaired = jsonMatch[1]
            .replace(/\/\/[^\n]*/g, '')              // remove // comments
            .replace(/\/\*[\s\S]*?\*\//g, '')        // remove /* */ comments
            .replace(/(:\s*-?\d+),(?=\d{3})/g, '$1') // remove commas in numbers
            .replace(/,\s*([\}\]])/g, '$1');          // remove trailing commas
          try { parsed = JSON.parse(repaired); } catch (_2) {}
        }
      }
      if (parsed && typeof parsed === 'object') {
        try {
          const rendered = renderStructuredResponse(parsed);
          finalTextEl.innerHTML = '';
          finalTextEl.classList.remove('whitespace-pre-wrap');
          finalTextEl.appendChild(rendered);
        } catch (e) {
          console.error('renderStructuredResponse failed:', e);
          finalTextEl.textContent = stripped;
        }
      } else {
        // Plain text response — strip fences only
        finalTextEl.textContent = stripped;
      }
    }

    finishWaiting();
  }
}

function renderStructuredResponse(obj) {
  const container = document.createElement('div');
  container.className = 'space-y-3 text-sm text-gray-800';

  // Summary
  if (obj.summary) {
    const p = document.createElement('p');
    p.className = 'font-medium text-gray-900';
    p.textContent = obj.summary;
    container.appendChild(p);
  }

  // Positions / items table
  const listKey = ['positions', 'servicers', 'items', 'results', 'data'].find(k => Array.isArray(obj[k]));
  if (listKey && obj[listKey].length > 0) {
    const items = obj[listKey];
    const keys = Object.keys(items[0]);
    const table = document.createElement('table');
    table.className = 'w-full text-xs border-collapse mt-2';

    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    keys.forEach(k => {
      const th = document.createElement('th');
      th.className = 'text-left px-2 py-1 bg-gray-100 border border-gray-200 font-semibold text-gray-600 capitalize';
      th.textContent = k.replace(/_/g, ' ');
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    items.forEach((item, i) => {
      const row = document.createElement('tr');
      row.className = i % 2 === 0 ? 'bg-white' : 'bg-gray-50';
      keys.forEach(k => {
        const td = document.createElement('td');
        td.className = 'px-2 py-1 border border-gray-200';
        const val = item[k];
        if (typeof val === 'number' && k.includes('balance')) {
          td.textContent = '$' + val.toLocaleString('en-US', {maximumFractionDigits: 0});
        } else {
          td.textContent = val ?? '—';
        }
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    container.appendChild(table);
  }

  // Key metrics row
  const metricKeys = ['total_classified_exposure', 'total_exposure', 'total_positions', 'confidence'];
  const metrics = metricKeys.filter(k => obj[k] !== undefined);
  if (metrics.length) {
    const row = document.createElement('div');
    row.className = 'flex flex-wrap gap-3 mt-1';
    metrics.forEach(k => {
      const pill = document.createElement('span');
      pill.className = 'bg-gray-100 rounded-full px-3 py-1 text-xs text-gray-700';
      let val = obj[k];
      if (typeof val === 'number' && k.includes('exposure')) val = '$' + val.toLocaleString('en-US', {maximumFractionDigits: 0});
      if (typeof val === 'number' && k === 'confidence') val = (val * 100).toFixed(0) + '%';
      pill.textContent = k.replace(/_/g, ' ') + ': ' + val;
      row.appendChild(pill);
    });
    container.appendChild(row);
  }

  // Recommended action
  if (obj.recommended_action) {
    const box = document.createElement('div');
    box.className = 'border-l-4 pl-3 py-1 text-xs text-gray-700 mt-1';
    box.style.borderColor = 'var(--accent)';
    box.innerHTML = '<span class="font-semibold">Recommended action:</span> ' + obj.recommended_action;
    container.appendChild(box);
  }

  return container;
}

function handleConfirmation(data) {
  currentAgentBubble = null;

  const bubble = createAgentBubble();
  const textEl = bubble.querySelector('.bubble-text');
  if (textEl) {
    textEl.textContent = data.message || 'Please confirm this operation.';
  }

  // Add Yes/No buttons
  const btnRow = document.createElement('div');
  btnRow.className = 'flex gap-2 mt-3';

  const yesBtn = document.createElement('button');
  yesBtn.textContent = data.confirm_label || 'Yes, proceed';
  yesBtn.className = 'bg-[var(--primary)] text-white text-sm px-4 py-2 rounded-lg hover:opacity-90 transition-opacity confirm-btn';

  const noBtn = document.createElement('button');
  noBtn.textContent = data.cancel_label || 'No, cancel';
  noBtn.className = 'bg-gray-200 text-gray-700 text-sm px-4 py-2 rounded-lg hover:bg-gray-300 transition-colors confirm-btn';

  const spinner = document.createElement('span');
  spinner.className = 'hidden ml-2 text-sm text-gray-400 confirm-spinner';
  spinner.textContent = '⏳ Processing...';

  yesBtn.addEventListener('click', () => sendConfirmation(true, btnRow, spinner));
  noBtn.addEventListener('click', () => sendConfirmation(false, btnRow, spinner));

  btnRow.appendChild(yesBtn);
  btnRow.appendChild(noBtn);
  btnRow.appendChild(spinner);
  bubble.appendChild(btnRow);
  scrollToBottom();
}

function sendConfirmation(value, btnRow, spinner) {
  // Disable both buttons
  btnRow.querySelectorAll('.confirm-btn').forEach(b => {
    b.disabled = true;
    b.classList.add('opacity-50', 'cursor-not-allowed');
  });
  spinner.classList.remove('hidden');

  sendMessage({ type: 'confirm', value });
}

function handleSuggestions(data) {
  const questions = (data.questions || []).slice(0, 3);
  if (!questions.length) return;

  const area = document.getElementById('suggestions-area');
  const chips = document.getElementById('suggestion-chips');
  if (!area || !chips) return;

  chips.innerHTML = '';
  questions.forEach(q => {
    const chip = createChip(q, () => {
      clearSuggestions();
      submitUserMessage(q);
    });
    chips.appendChild(chip);
  });

  area.classList.remove('hidden');
  scrollToBottom();
}

function handleError(data) {
  currentAgentBubble = null;
  const msg = data.content || 'An error occurred.';
  const chatArea = document.getElementById('chat-area');
  if (!chatArea) return;

  const banner = document.createElement('div');
  banner.className = 'flex justify-center animate-fade-in';

  const inner = document.createElement('div');
  inner.className = 'bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-2 max-w-lg text-center';
  inner.textContent = `⚠ ${msg}`;

  banner.appendChild(inner);
  chatArea.appendChild(banner);
  scrollToBottom();
}

function handleHandoff(data) {
  appendSystemMessage(`Routing to ${data.agent || 'specialist'}...`);
}

// ── Message rendering ─────────────────────────────────────────────────────────

function createAgentBubble() {
  const chatArea = document.getElementById('chat-area');
  if (!chatArea) return null;

  const agentName = config.AGENT_NAME || 'A';
  const initial = agentName.charAt(0).toUpperCase();

  const wrapper = document.createElement('div');
  wrapper.className = 'flex items-start gap-3 animate-fade-in';

  const avatar = document.createElement('div');
  avatar.className = 'w-8 h-8 rounded-full text-white flex items-center justify-center text-sm font-bold flex-shrink-0';
  avatar.style.backgroundColor = 'var(--primary)';
  avatar.textContent = initial;

  const bubble = document.createElement('div');
  bubble.className = 'bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 max-w-2xl shadow-sm';

  const text = document.createElement('p');
  text.className = 'text-sm text-gray-800 leading-relaxed bubble-text whitespace-pre-wrap';
  text.textContent = '';

  bubble.appendChild(text);
  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  chatArea.appendChild(wrapper);
  scrollToBottom();

  return bubble;
}

function appendUserBubble(message) {
  const chatArea = document.getElementById('chat-area');
  if (!chatArea) return;

  const wrapper = document.createElement('div');
  wrapper.className = 'flex justify-end animate-fade-in';

  const bubble = document.createElement('div');
  bubble.className = 'text-white rounded-2xl rounded-tr-sm px-4 py-3 max-w-lg text-sm leading-relaxed';
  bubble.style.backgroundColor = 'var(--primary)';
  bubble.textContent = message;

  wrapper.appendChild(bubble);
  chatArea.appendChild(wrapper);
  scrollToBottom();
}

function appendSystemMessage(text) {
  const chatArea = document.getElementById('chat-area');
  if (!chatArea) return;

  const el = document.createElement('div');
  el.className = 'flex justify-center animate-fade-in';

  const inner = document.createElement('span');
  inner.className = 'text-xs text-gray-400 italic bg-gray-100 px-3 py-1 rounded-full';
  inner.textContent = text;

  el.appendChild(inner);
  chatArea.appendChild(el);
  scrollToBottom();
}

// ── Starter questions ─────────────────────────────────────────────────────────

function renderStarterQuestions() {
  const starters = config.STARTER_QUESTIONS || [];
  const chips = document.getElementById('starter-chips');
  const panel = document.getElementById('starter-questions');
  if (!chips) return;

  if (!starters.length) {
    if (panel) panel.classList.add('hidden');
    return;
  }

  starters.forEach(q => {
    const chip = createChip(q, () => {
      hideStarterQuestions();
      submitUserMessage(q);
    });
    chips.appendChild(chip);
  });
}

function hideStarterQuestions() {
  const panel = document.getElementById('starter-questions');
  if (panel) panel.classList.add('hidden');
  hasStartedChat = true;
}

// ── Follow-up suggestions ──────────────────────────────────────────────────────

function clearSuggestions() {
  const area = document.getElementById('suggestions-area');
  const chips = document.getElementById('suggestion-chips');
  if (chips) chips.innerHTML = '';
  if (area) area.classList.add('hidden');
}

// ── Scenarios ─────────────────────────────────────────────────────────────────

function renderScenarios() {
  const scenarios = config.SCENARIOS || [];
  const panel = document.getElementById('scenarios-panel');
  const list = document.getElementById('scenarios-list');
  const toggle = document.getElementById('scenarios-toggle');
  const caret = document.getElementById('scenarios-caret');

  if (!scenarios.length || !panel) return;

  panel.classList.remove('hidden');

  scenarios.forEach((scenario, idx) => {
    const card = document.createElement('div');
    card.className = 'border border-gray-200 rounded-lg p-3 bg-white';

    const title = document.createElement('p');
    title.className = 'text-sm font-medium text-gray-800';
    title.textContent = scenario.name || `Scenario ${idx + 1}`;

    const desc = document.createElement('p');
    desc.className = 'text-xs text-gray-500 mt-1';
    desc.textContent = scenario.description || '';

    const startBtn = document.createElement('button');
    startBtn.textContent = 'Start →';
    startBtn.className = 'mt-2 text-xs text-[var(--primary)] font-medium hover:underline';
    startBtn.addEventListener('click', () => startScenario(scenario));

    card.appendChild(title);
    if (scenario.description) card.appendChild(desc);
    card.appendChild(startBtn);

    if (list) list.appendChild(card);
  });

  if (toggle && list && caret) {
    toggle.addEventListener('click', () => {
      const isHidden = list.classList.toggle('hidden');
      caret.textContent = isHidden ? '▶ expand' : '▼ collapse';
    });
  }
}

function startScenario(scenario) {
  currentScenario = scenario;
  currentScenarioStep = 0;

  const steps = scenario.steps || [];
  if (!steps.length) return;

  hideStarterQuestions();
  submitUserMessage(steps[0].prompt || steps[0]);
}

// ── Input handling ────────────────────────────────────────────────────────────

function initInputHandlers() {
  const input = document.getElementById('user-input');
  const btn = document.getElementById('send-btn');

  if (!input || !btn) return;

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !isWaiting) {
      e.preventDefault();
      const msg = input.value.trim();
      if (msg) {
        input.value = '';
        if (!hasStartedChat) hideStarterQuestions();
        submitUserMessage(msg);
      }
    }
  });

  btn.addEventListener('click', () => {
    const msg = input.value.trim();
    if (msg && !isWaiting) {
      input.value = '';
      if (!hasStartedChat) hideStarterQuestions();
      submitUserMessage(msg);
    }
  });
}

function submitUserMessage(message) {
  appendUserBubble(message);
  clearSuggestions();
  startWaiting();
  sendMessage(message);
  hasStartedChat = true;
}

function sendMessage(data) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    handleError({ content: 'Not connected. Please wait for reconnection.' });
    return;
  }
  const payload = typeof data === 'string'
    ? JSON.stringify({ type: 'text', content: data })
    : JSON.stringify(data);
  ws.send(payload);
}

function startWaiting() {
  isWaiting = true;
  currentAgentBubble = null;
  setInputEnabled(false);

  // Typing indicator
  const chatArea = document.getElementById('chat-area');
  if (chatArea) {
    const typing = document.createElement('div');
    typing.id = 'typing-indicator';
    typing.className = 'flex items-start gap-3 animate-fade-in';

    const agentName = config.AGENT_NAME || 'A';
    const avatar = document.createElement('div');
    avatar.className = 'w-8 h-8 rounded-full text-white flex items-center justify-center text-sm font-bold flex-shrink-0';
    avatar.style.backgroundColor = 'var(--primary)';
    avatar.textContent = agentName.charAt(0).toUpperCase();

    const bubble = document.createElement('div');
    bubble.className = 'bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm';
    bubble.innerHTML = '<span class="typing-dots"><span></span><span></span><span></span></span>';

    typing.appendChild(avatar);
    typing.appendChild(bubble);
    chatArea.appendChild(typing);
    scrollToBottom();
  }
}

function finishWaiting() {
  isWaiting = false;
  setInputEnabled(true);
  currentAgentBubble = null;

  const indicator = document.getElementById('typing-indicator');
  if (indicator) indicator.remove();

  // Scenario: show next step as suggestion
  if (currentScenario) {
    const steps = currentScenario.steps || [];
    currentScenarioStep++;
    if (currentScenarioStep < steps.length) {
      const nextStep = steps[currentScenarioStep];
      const nextPrompt = typeof nextStep === 'string' ? nextStep : nextStep.prompt;
      if (nextPrompt) {
        const area = document.getElementById('suggestions-area');
        const chips = document.getElementById('suggestion-chips');
        if (area && chips) {
          chips.innerHTML = '';
          const chip = createChip(`Next: ${nextPrompt}`, () => {
            clearSuggestions();
            submitUserMessage(nextPrompt);
          });
          chips.appendChild(chip);
          area.classList.remove('hidden');
        }
      }
    } else {
      currentScenario = null;
    }
  }
}

function setInputEnabled(enabled) {
  const input = document.getElementById('user-input');
  const btn = document.getElementById('send-btn');
  if (input) input.disabled = !enabled;
  if (btn) btn.disabled = !enabled;
}

// ── Reset button ──────────────────────────────────────────────────────────────

function initResetButton() {
  const btn = document.getElementById('reset-btn');
  if (!btn) return;

  if (config.PROTOTYPE_RESET === 'true') {
    btn.classList.remove('hidden');
    btn.addEventListener('click', async () => {
      if (!confirm('Reset all prototype data? This cannot be undone.')) return;

      try {
        const res = await fetch('/admin/reset', { method: 'POST' });
        const data = await res.json();

        if (data.status === 'reset') {
          showToast(`✅ Reset complete. ${data.tables_seeded} tables seeded.`);
          clearChatArea();
          showWelcomeMessage();
          document.getElementById('starter-questions')?.classList.remove('hidden');
          hasStartedChat = false;
          clearSuggestions();

          // Reconnect WebSocket
          if (ws) ws.close();
          setTimeout(connectWebSocket, 500);
        } else {
          showToast('❌ Reset failed: ' + (data.detail || 'Unknown error'));
        }
      } catch (e) {
        showToast('❌ Reset error: ' + e.message);
      }
    });
  }
}

// ── Welcome message ───────────────────────────────────────────────────────────

function showWelcomeMessage() {
  const msg       = config.WELCOME_MESSAGE || 'Hello! How can I help you today?';
  const agentName = config.AGENT_NAME || 'Assistant';

  // Populate welcome card elements if they exist
  const welcomeCard    = document.getElementById('welcome-card');
  const welcomeAvatar  = document.getElementById('welcome-avatar');
  const welcomeName    = document.getElementById('welcome-agent-name');
  const welcomeText    = document.getElementById('welcome-message-text');

  if (welcomeCard) {
    if (welcomeAvatar) {
      welcomeAvatar.textContent = agentName.charAt(0).toUpperCase();
      welcomeAvatar.style.backgroundColor = 'var(--primary)';
    }
    if (welcomeName)  welcomeName.textContent  = agentName;
    if (welcomeText)  welcomeText.textContent  = msg;
  } else {
    // Fallback: render as agent chat bubble
    const bubble = createAgentBubble();
    if (bubble) {
      const textEl = bubble.querySelector('.bubble-text');
      if (textEl) textEl.textContent = msg;
      currentAgentBubble = null;
    }
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function createChip(label, onClick) {
  const chip = document.createElement('button');
  chip.className = 'text-xs rounded-full px-3 py-1 transition-colors cursor-pointer bg-white chip-btn';
  chip.style.border = '1px solid var(--primary)';
  chip.style.color = 'var(--primary)';
  chip.addEventListener('mouseenter', () => {
    chip.style.backgroundColor = 'var(--primary)';
    chip.style.color = '#ffffff';
  });
  chip.addEventListener('mouseleave', () => {
    chip.style.backgroundColor = '#ffffff';
    chip.style.color = 'var(--primary)';
  });
  chip.textContent = label;
  chip.addEventListener('click', onClick);
  return chip;
}

function clearChatArea() {
  const area = document.getElementById('chat-area');
  if (area) area.innerHTML = '';
}

function scrollToBottom() {
  const area = document.getElementById('chat-area');
  if (area) area.scrollTop = area.scrollHeight;
}

function showBanner(id) {
  document.getElementById(id)?.classList.remove('hidden');
}

function hideBanner(id) {
  document.getElementById(id)?.classList.add('hidden');
}

function showToast(message, duration = 3000) {
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove('hidden');
  setTimeout(() => toast.classList.add('hidden'), duration);
}
