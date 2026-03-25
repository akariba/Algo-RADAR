/* ─────────────────────────────────────────────────────────────────────────────
   Cross-Asset Opportunity Radar — app.js

   Loading contract:
   - every panel loads independently (no shared Promise.all gate)
   - every fetch wrapped in withTimeout()
   - every panel resolves to: loaded | empty | error | timeout
   - no spinner stays active after fetch completes or fails
───────────────────────────────────────────────────────────────────────────── */

'use strict';

// ── config ────────────────────────────────────────────────────────────────────
const CFG = {
  TAPE_TTL:        30_000,
  TAPE_TIMEOUT:    15_000,  // IBKR bulk quote: ~2.5s + overhead
  OPPS_TIMEOUT:    25_000,  // yfinance parallel signal sweep
  INSTR_TIMEOUT:   18_000,  // IBKR historical bars (up to 30s backend; 18s FE)
  CHART_RERENDER:     300,
  HEALTH_INTERVAL: 30_000,  // IBKR status badge refresh
};

// ── state ─────────────────────────────────────────────────────────────────────
let selectedSymbol = 'SPY';
let selectedName   = 'SPDR S&P 500 ETF Trust';
let plotlyReady    = false;
let radarData      = [];          // sorted opportunity list
let isManualSelect = false;       // true when user clicked tape / non-top result
let selectedTf     = '1d';        // active timeframe
let showGbm        = true;        // GBM forecast cone visible
let showVol        = true;        // volume subplot visible
let lastInstrumentData = null;    // cache last loaded instrument for redraw

// ── bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkPlotly();
  loadHealth();                // IBKR badge — immediate check
  loadTape();
  loadOpportunities();
  // Chart loads independently — resolves even if opportunities fails/times out
  loadInstrument(selectedSymbol, selectedName);
  setInterval(loadTape,   CFG.TAPE_TTL);
  setInterval(loadHealth, CFG.HEALTH_INTERVAL);
});

function checkPlotly() {
  if (typeof Plotly !== 'undefined') {
    plotlyReady = true;
    return;
  }
  const poll = setInterval(() => {
    if (typeof Plotly !== 'undefined') {
      plotlyReady = true;
      clearInterval(poll);
      console.log('[chart] Plotly ready (deferred)');
      if (selectedSymbol) loadInstrument(selectedSymbol, selectedName);
    }
  }, 200);
}

// ── fetch helpers ─────────────────────────────────────────────────────────────

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('timeout')), ms)
    ),
  ]);
}

async function apiFetch(path, timeoutMs) {
  const resp = await withTimeout(fetch(path), timeoutMs);
  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(`HTTP ${resp.status}: ${body.slice(0, 120)}`);
  }
  return resp.json();
}

// ── panel state helpers ───────────────────────────────────────────────────────

function panelLoading(panelId)            { _setState(panelId, 'loading'); }
function panelLoaded(panelId)             { _setState(panelId, 'loaded'); }
function panelError(panelId, msg)         { _setState(panelId, 'error',   msg); console.warn(`[${panelId}] error — ${msg}`); }
function panelTimeout(panelId, label)     { _setState(panelId, 'timeout', `${label} — request timed out.`); console.warn(`[${panelId}] timeout`); }
function panelEmpty(panelId, msg)         { _setState(panelId, 'empty',   msg); }

function _setState(panelId, state, msg) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const loading = panel.querySelector('[data-role="loading"]');
  const error   = panel.querySelector('[data-role="error"]');
  const timeout = panel.querySelector('[data-role="timeout"]');
  const empty   = panel.querySelector('[data-role="empty"]');
  const content = panel.querySelector('[data-role="content"]');
  [loading, error, timeout, empty].forEach(el => el && el.classList.add('hidden'));
  if (state === 'loading' && loading) loading.classList.remove('hidden');
  if (state === 'error'   && error)   { error.classList.remove('hidden');   if (msg) error.textContent = msg; }
  if (state === 'timeout' && timeout) { timeout.classList.remove('hidden'); if (msg) timeout.textContent = msg; }
  if (state === 'empty'   && empty)   { empty.classList.remove('hidden');   if (msg) empty.textContent = msg; }
  if (content) content.style.visibility = (state === 'loaded') ? 'visible' : 'hidden';
}

// ── IBKR health / connection badge ────────────────────────────────────────────

async function loadHealth() {
  try {
    const data = await withTimeout(fetch('/api/health'), 6000).then(r => r.json());
    _updateIbkrBadge(data.ibkr_connected);
  } catch (_) {
    _updateIbkrBadge(false);
  }
}

function _updateIbkrBadge(connected) {
  const el = document.getElementById('ibkr-badge');
  if (!el) return;
  if (connected) {
    el.textContent  = 'IBKR Connected';
    el.className    = 'ibkr-badge connected';
  } else {
    el.textContent  = 'IBKR Disconnected';
    el.className    = 'ibkr-badge disconnected';
  }
}

function _updateMdsChip(status) {
  const el = document.getElementById('mds-chip');
  if (!el) return;
  const labels = {
    live:         'Live',
    delayed:      'Delayed',
    unsupported:  'Unsupported',
    error:        'No Data',
    disconnected: 'Disconnected',
  };
  el.textContent = labels[status] || status || '';
  el.className   = `mds-chip ${status || ''}`;
}

// ── tape ──────────────────────────────────────────────────────────────────────

async function loadTape() {
  console.log('[tape] fetching');
  try {
    const data = await apiFetch('/api/market/tape', CFG.TAPE_TIMEOUT);
    renderTape(data);
    const el = document.getElementById('tape-status');
    if (el) el.textContent = new Date().toLocaleTimeString();
    console.log('[tape] rendered', data.length, 'items');
  } catch (err) {
    const el = document.getElementById('tape-status');
    if (el) el.textContent = err.message === 'timeout' ? 'Tape timed out' : 'Tape unavailable';
    console.warn('[tape] failed —', err.message);
  }
}

function renderTape(items) {
  const container = document.getElementById('tape-items');
  if (!container) return;
  // duplicate for seamless CSS loop animation
  const html = [...items, ...items].map(item => {
    const st    = item.status || 'error';
    const price = item.price != null ? fmtPrice(item.price) : '—';
    const pct   = item.change_pct;
    // dim the price row for unsupported/error items
    const priceColor = (st === 'unsupported' || st === 'error') ? 'color:var(--text-dim)' : '';
    const cls   = pct > 0 ? 'pos' : pct < 0 ? 'neg' : 'flat';
    const sign  = pct > 0 ? '+' : '';
    const pctTxt = (st === 'unsupported') ? 'N/A'
                 : (st === 'error')       ? 'ERR'
                 : `${sign}${pct != null ? pct.toFixed(2) : '—'}%`;
    const tooltip = (st === 'unsupported' || st === 'error')
      ? `${item.name} (${item.symbol}) — ${item.error || st}`
      : `${item.name} (${item.symbol})`;
    return `<div class="tape-item"
         title="${esc(tooltip)}"
         onclick="selectFromTape('${esc(item.yf_symbol)}','${esc(item.name)}')">
      <span class="tape-symbol">${esc(item.symbol)}</span>
      <span class="tape-name">${esc(item.name)}</span>
      <span class="tape-price" style="${priceColor}">${price}</span>
      <span class="tape-chg ${st === 'live' || st === 'delayed' ? cls : 'flat'}">${pctTxt}</span>
    </div>`;
  }).join('');
  container.innerHTML = html;
  // also update IBKR badge based on observed data types
  const hasLive    = items.some(i => i.status === 'live');
  const hasDelayed = items.some(i => i.status === 'delayed');
  const allError   = items.every(i => i.status === 'error');
  if (allError) {
    _updateIbkrBadge(false);
  } else if (hasLive) {
    const el = document.getElementById('ibkr-badge');
    if (el) { el.textContent = 'IBKR Live'; el.className = 'ibkr-badge connected'; }
  } else if (hasDelayed) {
    const el = document.getElementById('ibkr-badge');
    if (el) { el.textContent = 'IBKR Delayed'; el.className = 'ibkr-badge delayed'; }
  }
}

function selectFromTape(yfSymbol, name) {
  isManualSelect = true;
  selectedSymbol = yfSymbol;
  selectedName   = name;
  highlightRadarRow(yfSymbol);
  loadInstrument(yfSymbol, name);
}

// ── opportunities ─────────────────────────────────────────────────────────────

async function loadOpportunities() {
  panelLoading('radar-panel');
  console.log('[radar] fetching');
  try {
    const data = await apiFetch('/api/opportunities', CFG.OPPS_TIMEOUT);
    radarData = data;
    renderOpportunities(data);
    panelLoaded('radar-panel');
    console.log('[radar] rendered', data.length, 'rows');
    if (data.length > 0) {
      highlightRadarRow(selectedSymbol);
      // Only auto-select top result if user hasn't manually picked something
      if (!isManualSelect) {
        selectedSymbol = data[0].symbol;
        selectedName   = data[0].name;
        loadInstrument(selectedSymbol, selectedName);
        highlightRadarRow(selectedSymbol);
      }
    }
  } catch (err) {
    if (err.message === 'timeout') panelTimeout('radar-panel', 'Opportunity radar');
    else                           panelError('radar-panel', `Radar unavailable: ${err.message}`);
  }
}

function renderOpportunities(items) {
  const body  = document.getElementById('radar-body');
  const count = document.getElementById('radar-count');
  if (!body) return;
  if (count) count.textContent = `${items.length} signals`;
  if (items.length === 0) {
    body.innerHTML = '<div class="placeholder-row">No signals computed.</div>';
    return;
  }

  body.innerHTML = items.map((item, idx) => {
    const rank      = idx + 1;
    const rankCls   = rank <= 3 ? 'top' : '';
    const dirCls    = item.direction === 'LONG' ? 'long' : 'short';
    const act       = (item.actionability_state || 'MEDIUM').toLowerCase();
    const actLbl    = item.actionability_state || 'MEDIUM';
    const eq        = (item.entry_quality || 'acceptable');
    const tScore    = item.tradeability_score != null ? item.tradeability_score : item.conviction;
    const tScoreCls = tScore >= 68 ? 'high' : tScore < 35 ? 'low' : '';
    const pctCls    = item.change_pct >= 0 ? 'pos' : 'neg';
    const pctSign   = item.change_pct >= 0 ? '+' : '';
    const setupLbl  = (item.setup_type || '').replace(/_/g,' ');
    // Only show distinctive tags
    const tags = (item.tags || []).filter(t => t !== 'trend');
    const tagHtml = tags.length
      ? `<div class="tag-list">${tags.map(t => `<span class="tag ${t === 'wait' ? 'tag-wait' : ''}">${esc(t)}</span>`).join('')}</div>`
      : '';

    return `<div class="radar-row" id="row-${(item.yf_symbol||item.symbol).replace(/[^a-zA-Z0-9]/g, '_')}"
         onclick="selectInstrument('${esc(item.yf_symbol||item.symbol)}','${esc(item.name)}')">
      <div class="radar-row-top">
        <span class="rank-num ${rankCls}">${rank}</span>
        <span class="radar-name" title="${esc(item.name)} (${esc(item.symbol)})">
          ${esc(item.name)} <span style="color:var(--text-dim);font-weight:400">(${esc(item.symbol)})</span>
        </span>
        <span class="dir-badge ${dirCls}">${item.direction}</span>
        <span class="act-badge ${act}">${actLbl}</span>
      </div>
      <div class="conv-bar-wrap">
        <div class="conv-bar-bg">
          <div class="conv-bar-fill ${tScoreCls}" style="width:${tScore}%"></div>
        </div>
        <span class="conv-val">${tScore}</span>
      </div>
      <div class="radar-meta">
        <span>${esc(setupLbl)}</span>
        <span>5D ±<span class="hi">${item.expected_5d}%</span></span>
        <span>Sz <span class="hi">${item.suggested_size}%</span></span>
        <span>R/R <span class="hi">${item.rr}×</span></span>
        <span class="${pctCls}">${pctSign}${item.change_pct}%</span>
      </div>
      <div class="radar-reason">${esc(item.reason)}</div>
      ${item.trigger_text ? `<div class="radar-trigger">⟶ ${esc(item.trigger_text)}</div>` : ''}
      ${tagHtml}
    </div>`;
  }).join('');
}

function highlightRadarRow(symbol) {
  document.querySelectorAll('.radar-row').forEach(r => r.classList.remove('selected'));
  const safe = symbol.replace(/[^a-zA-Z0-9]/g, '_');
  const row = document.getElementById(`row-${safe}`);
  if (row) { row.classList.add('selected'); row.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
}

// ── timeframe & chart toggle controls ─────────────────────────────────────────

function selectTf(tf, btn) {
  if (tf === selectedTf) return;
  selectedTf = tf;
  document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const lbl = document.getElementById('tf-label');
  if (lbl) lbl.textContent = tf.toUpperCase();
  if (selectedSymbol) loadInstrument(selectedSymbol, selectedName);
}

function toggleGbm(btn) {
  showGbm = !showGbm;
  btn.classList.toggle('active', showGbm);
  if (lastInstrumentData) renderChart(lastInstrumentData);
}

function toggleVol(btn) {
  showVol = !showVol;
  btn.classList.toggle('active', showVol);
  if (lastInstrumentData) renderChart(lastInstrumentData);
}

function selectInstrument(symbol, name) {
  const rank = radarData.findIndex(d => d.symbol === symbol);
  isManualSelect = rank !== 0;
  selectedSymbol = symbol;
  selectedName   = name;
  highlightRadarRow(symbol);
  loadInstrument(symbol, name);
}

// ── actionability bar ─────────────────────────────────────────────────────────

function renderActionBar(data) {
  const act     = data.actionability_state || data.trade_structure?.actionability_state || '';
  const eq      = data.entry_quality       || data.trade_structure?.entry_quality       || '';
  const setup   = data.setup_type          || data.trade_structure?.setup_type          || '';
  const trigger = data.trigger_text        || data.trade_structure?.trigger_text        || '';
  const dir     = data.direction || '';

  const biasEl = document.getElementById('ab-bias');
  if (biasEl) {
    biasEl.textContent = dir;
    biasEl.className   = `dir-badge ${dir === 'LONG' ? 'long' : 'short'}`;
  }
  const setupEl = document.getElementById('ab-setup');
  if (setupEl) setupEl.textContent = setup.replace(/_/g,' ') || '—';

  const actEl = document.getElementById('ab-action');
  if (actEl) {
    actEl.textContent = act || '—';
    actEl.className   = `act-badge ${(act || '').toLowerCase()}`;
  }
  const eqEl = document.getElementById('ab-entry');
  if (eqEl) {
    eqEl.textContent = eq || '—';
    eqEl.className   = `eq-badge ${(eq || '').toLowerCase()}`;
  }
  const trigEl = document.getElementById('ab-trigger');
  if (trigEl) trigEl.textContent = trigger || '—';
}

function clearActionBar() {
  ['ab-bias','ab-setup','ab-action','ab-entry','ab-trigger'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = '—';
    el.className   = el.className.replace(/\b(long|short|high|medium|low|wait|strong|acceptable|weak)\b/g, '').trim();
  });
}

// ── instrument detail ─────────────────────────────────────────────────────────

async function loadInstrument(symbol, name) {
  updateDetailHeader(name, symbol, null, null);
  _updateMdsChip('');
  clearActionBar();
  panelLoading('chart-panel');
  clearContextBar();
  clearTradeStructure();
  clearWhyNow();
  clearNews();
  console.log('[instrument] fetching', symbol);

  try {
    const data = await apiFetch(
      `/api/instrument?ticker=${encodeURIComponent(symbol)}&tf=${encodeURIComponent(selectedTf)}`,
      CFG.INSTR_TIMEOUT
    );

    if (data.error) { panelError('chart-panel', data.error); _updateMdsChip('error'); return; }

    lastInstrumentData = data;

    _updateMdsChip(data.market_data_status || '');
    updateDetailHeader(data.name, data.symbol, data.last, data.change_pct);
    renderActionBar(data);

    // each sub-panel is independent — one failing won't block others
    renderChart(data);
    renderContextBar(data);
    renderTradeStructure(data.trade_structure);
    renderWhyNow(data.why_now || []);
    renderNews(data.news || [], data.name, data.symbol);

    console.log('[instrument] rendered', symbol);
  } catch (err) {
    if (err.message === 'timeout') {
      panelTimeout('chart-panel', `${name} (${symbol})`);
      _updateMdsChip('error');
    } else {
      panelError('chart-panel', `Failed to load ${name} (${symbol}): ${err.message}`);
      _updateMdsChip('error');
    }
    clearContextBar();
    clearTradeStructure();
    clearWhyNow();
    renderNews([], name, symbol);
  }
}

// ── detail header ─────────────────────────────────────────────────────────────

function updateDetailHeader(name, symbol, last, changePct) {
  const titleEl  = document.getElementById('detail-title');
  const badgeEl  = document.getElementById('detail-symbol-badge');
  const changeEl = document.getElementById('detail-change');
  const rankEl   = document.getElementById('detail-rank');

  if (titleEl) titleEl.textContent = name || '—';
  if (badgeEl) badgeEl.textContent = symbol || '';

  // rank badge
  if (rankEl && symbol) {
    const idx = radarData.findIndex(d => d.symbol === symbol);
    if (idx >= 0) {
      rankEl.textContent = `#${idx + 1}`;
      rankEl.className   = 'detail-rank' + (idx < 3 ? '' : '');
      rankEl.style.display = '';
    } else {
      rankEl.textContent = 'custom';
      rankEl.className   = 'detail-rank custom';
      rankEl.style.display = '';
    }
  }

  if (changeEl) {
    if (last != null) {
      const sign = changePct >= 0 ? '+' : '';
      changeEl.textContent = `${fmtPrice(last)}  ${sign}${changePct != null ? changePct.toFixed(2) : '0.00'}%`;
      changeEl.className   = 'detail-change ' + (changePct >= 0 ? 'pos' : 'neg');
      changeEl.id          = 'detail-change';
    } else {
      changeEl.textContent = '';
    }
  }
}

// ── chart ─────────────────────────────────────────────────────────────────────

function renderChart(data) {
  if (!plotlyReady) { panelError('chart-panel', 'Chart library not loaded. Reload the page.'); return; }
  const container = document.getElementById('price-chart');
  if (!container)  { panelError('chart-panel', 'Chart container missing.'); return; }

  const rect = container.getBoundingClientRect();
  if (rect.width < 10 || rect.height < 10) {
    setTimeout(() => renderChart(data), CFG.CHART_RERENDER);
    return;
  }

  const c = data.chart;
  if (!c || !c.closes || c.closes.length === 0) {
    panelEmpty('chart-panel', `No chart data for ${data.name} (${data.symbol}).`);
    return;
  }

  try {
    // ── price traces ─────────────────────────────────────────────────────────
    const priceDomain = showVol ? [0.24, 1.0] : [0.0, 1.0];

    const traces = [
      { type: 'scatter', mode: 'lines', x: c.dates, y: Array(c.dates.length).fill(c.bb_upper),
        name: 'BB Upper', line: { color: '#1e3458', width: 1, dash: 'dash' }, hoverinfo: 'skip' },
      { type: 'scatter', mode: 'lines', x: c.dates, y: Array(c.dates.length).fill(c.bb_lower),
        name: 'BB Lower', line: { color: '#1e3458', width: 1, dash: 'dash' },
        fill: 'tonexty', fillcolor: 'rgba(30,52,88,0.08)', hoverinfo: 'skip' },
      { type: 'scatter', mode: 'lines', x: c.dates, y: Array(c.dates.length).fill(data.support),
        name: 'Support', line: { color: '#0f5c30', width: 1.2, dash: 'dot' }, hoverinfo: 'skip' },
      { type: 'scatter', mode: 'lines', x: c.dates, y: Array(c.dates.length).fill(data.resistance),
        name: 'Resist.', line: { color: '#6e1212', width: 1.2, dash: 'dot' }, hoverinfo: 'skip' },
      { type: 'scatter', mode: 'lines', x: c.dates, y: c.ema12,
        name: 'EMA 12', line: { color: '#3b7ae8', width: 1.2 }, hoverinfo: 'skip' },
      { type: 'scatter', mode: 'lines', x: c.dates, y: c.ema26,
        name: 'EMA 26', line: { color: '#00bfa0', width: 1.2, dash: 'dot' }, hoverinfo: 'skip' },
      { type: 'candlestick', x: c.dates,
        open: c.opens, high: c.highs, low: c.lows, close: c.closes,
        name: data.symbol,
        increasing: { line: { color: '#19b85a', width: 1 }, fillcolor: '#052214' },
        decreasing: { line: { color: '#e53535', width: 1 }, fillcolor: '#300808' },
        hoverinfo: 'x+y' },
    ];

    // ── GBM forecast cone ─────────────────────────────────────────────────────
    if (showGbm && data.realized_vol && data.last) {
      const gbm = _buildGbmCone(data, c.dates, selectedTf);
      traces.push(...gbm);
    }

    // ── volume subplot ────────────────────────────────────────────────────────
    if (showVol && c.volumes && c.volumes.length > 0) {
      const volColors = c.closes.map((cl, i) =>
        cl >= c.opens[i] ? 'rgba(25,184,90,0.45)' : 'rgba(229,53,53,0.45)'
      );
      traces.push({
        type: 'bar', x: c.dates, y: c.volumes,
        name: 'Volume',
        marker: { color: volColors },
        yaxis: 'y2',
        hovertemplate: '%{y:,.0f}<extra>Vol</extra>',
      });
      // cumulative delta (order flow approximation)
      const delta = c.closes.map((cl, i) => cl >= c.opens[i] ? c.volumes[i] : -c.volumes[i]);
      const cumDelta = delta.reduce((acc, d) => { acc.push((acc.length ? acc[acc.length-1] : 0) + d); return acc; }, []);
      traces.push({
        type: 'scatter', mode: 'lines', x: c.dates, y: cumDelta,
        name: 'Cum Δ',
        line: { color: '#3b7ae8', width: 1 },
        yaxis: 'y3',
        hoverinfo: 'skip',
      });
    }

    // ── x-axis tick settings per timeframe ───────────────────────────────────
    const tfXAxis = _tfXAxisSettings(selectedTf);

    const layout = {
      paper_bgcolor: 'transparent',
      plot_bgcolor:  '#0c1828',
      margin:  { t: 8, r: 62, b: showVol ? 40 : 36, l: 8 },
      font:    { family: 'JetBrains Mono, monospace', size: 10, color: '#4c6a8a' },
      xaxis: {
        type: 'date',
        showgrid: false,
        tickangle: -30,
        tickfont: { size: 9, color: '#5a7a9a' },
        rangeslider: { visible: false },
        showline: false,
        color: '#5a7a9a',
        automargin: true,
        domain: [0, 1],
        ...tfXAxis,
      },
      yaxis: {
        domain: priceDomain,
        showgrid: true,
        gridcolor: '#18293e',
        gridwidth: 1,
        tickfont: { size: 9, color: '#7a9abf' },
        color: '#7a9abf',
        side: 'right',
        showline: false,
        nticks: 7,
        automargin: true,
        tickformat: ',.4~f',
      },
      yaxis2: showVol ? {
        domain: [0.0, 0.19],
        showgrid: false,
        tickfont: { size: 8, color: '#4c6a8a' },
        color: '#4c6a8a',
        side: 'right',
        showline: false,
        nticks: 3,
        showticklabels: true,
        tickformat: '.2s',
      } : { domain: [0,0], showticklabels: false },
      yaxis3: showVol ? {
        domain: [0.0, 0.19],
        overlaying: 'y2',
        side: 'left',
        showgrid: false,
        tickfont: { size: 8, color: '#3b7ae8' },
        color: '#3b7ae8',
        showline: false,
        nticks: 3,
        showticklabels: false,
        zeroline: true,
        zerolinecolor: '#274868',
        zerolinewidth: 1,
      } : { domain: [0,0], showticklabels: false },
      legend:  { x: 0, y: 1.02, orientation: 'h', font: { size: 9, color: '#4c6a8a' }, bgcolor: 'transparent',
                 traceorder: 'normal', itemsizing: 'constant' },
      hovermode:  'x unified',
      hoverlabel: { bgcolor: '#0c1828', bordercolor: '#274868', font: { size: 10, color: '#b4caeb' } },
    };

    Plotly.react(container, traces, layout, { responsive: true, displayModeBar: false });
    panelLoaded('chart-panel');
    console.log('[chart] rendered', data.symbol, selectedTf);
  } catch (exc) {
    panelError('chart-panel', `Chart render failed: ${exc.message}`);
    console.error('[chart]', exc);
  }
}

// ── GBM forecast cone builder ──────────────────────────────────────────────────

function _buildGbmCone(data, historicDates, tf) {
  const last     = data.last;
  const sigmaAnn = Math.max((data.realized_vol || 15) / 100, 0.02);
  const exp5d    = (data.expected_5d || 1.0) / 100;

  // bars_per_year per timeframe → annualised to daily drift
  const barsPerYear = { '5m': 78*252, '1h': 7*252, '4h': 2*252, '1d': 252, '1w': 52 };
  const bpy    = barsPerYear[tf] || 252;
  const muAnn  = exp5d * (bpy / 5);                      // annualised
  const muBar  = muAnn / bpy;                             // per bar
  const sigBar = sigmaAnn / Math.sqrt(bpy);               // per bar

  const nFwd   = 20;                                      // project 20 bars forward
  const lastDate = historicDates[historicDates.length - 1];
  const futureDates = _futureDates(lastDate, nFwd, tf);

  // anchor: add current price at t=0
  // Correct GBM cone: S(t) = S0 * exp((mu - 0.5*sigma²)*t ± n*sigma*sqrt(t))
  // Using sqrt(i) for diffusion scaling — NOT linear sigBar*i which explodes
  const allDates = [lastDate, ...futureDates];
  const drift  = muBar - 0.5 * sigBar * sigBar;
  const median = allDates.map((_, i) => last * Math.exp(drift * i));
  const up1    = allDates.map((_, i) => last * Math.exp(drift * i + sigBar * Math.sqrt(i)));
  const dn1    = allDates.map((_, i) => last * Math.exp(drift * i - sigBar * Math.sqrt(i)));
  const up2    = allDates.map((_, i) => last * Math.exp(drift * i + 2 * sigBar * Math.sqrt(i)));
  const dn2    = allDates.map((_, i) => last * Math.exp(drift * i - 2 * sigBar * Math.sqrt(i)));

  const dirColor = data.direction === 'LONG' ? '59,200,100' : '220,60,60';

  return [
    // ±2σ outer band (very faint fill)
    { type: 'scatter', mode: 'lines', x: allDates, y: up2,
      name: '+2σ', line: { color: `rgba(${dirColor},0)`, width: 0 }, hoverinfo: 'skip', showlegend: false },
    { type: 'scatter', mode: 'lines', x: allDates, y: dn2,
      name: '±2σ', line: { color: `rgba(${dirColor},0)`, width: 0 },
      fill: 'tonexty', fillcolor: `rgba(${dirColor},0.06)`,
      hoverinfo: 'skip', showlegend: false },
    // ±1σ inner band
    { type: 'scatter', mode: 'lines', x: allDates, y: up1,
      name: '+1σ', line: { color: `rgba(${dirColor},0.25)`, width: 1, dash: 'dot' }, hoverinfo: 'skip', showlegend: false },
    { type: 'scatter', mode: 'lines', x: allDates, y: dn1,
      name: '-1σ', line: { color: `rgba(${dirColor},0.25)`, width: 1, dash: 'dot' },
      fill: 'tonexty', fillcolor: `rgba(${dirColor},0.10)`,
      hoverinfo: 'skip', showlegend: false },
    // median path
    { type: 'scatter', mode: 'lines', x: allDates, y: median,
      name: 'GBM', line: { color: `rgba(${dirColor},0.70)`, width: 1.5, dash: 'dash' },
      hovertemplate: 'GBM: %{y:.4~f}<extra></extra>' },
  ];
}

function _futureDates(fromDateStr, n, tf) {
  const dates = [];
  // For intraday, parse as ISO datetime; for daily/weekly, date-only
  const isIntraday = tf === '5m' || tf === '1h' || tf === '4h';
  const d = new Date(fromDateStr);
  for (let i = 0; i < n; i++) {
    if (isIntraday) {
      const mins = tf === '5m' ? 5 : tf === '1h' ? 60 : 240;
      d.setMinutes(d.getMinutes() + mins);
      // skip non-trading hours (approx: before 09:30 or after 16:00 ET)
      // Plotly handles gaps gracefully; just push the ISO string
      dates.push(d.toISOString().slice(0, 19).replace('T', 'T'));
    } else {
      // skip weekends for daily; weekly just add 7 days
      const step = tf === '1w' ? 7 : 1;
      d.setDate(d.getDate() + step);
      if (tf === '1d') {
        while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
      }
      dates.push(d.toISOString().slice(0, 10));
    }
  }
  return dates;
}

function _tfXAxisSettings(tf) {
  if (tf === '5m')  return { tickformat: '%H:%M',     nticks: 8 };
  if (tf === '1h')  return { tickformat: '%b %d %H:%M', nticks: 8 };
  if (tf === '4h')  return { tickformat: '%b %d',     nticks: 8 };
  if (tf === '1w')  return { tickformat: '%b %Y',     nticks: 8 };
  /* 1d */          return { tickformat: '%b %d',     nticks: 10 };
}

// ── context bar ───────────────────────────────────────────────────────────────

function renderContextBar(data) {
  _setCtx('ctx-last',  fmtPrice(data.last));
  _setCtx('ctx-chg',
    data.change_pct != null
      ? `${data.change_pct >= 0 ? '+' : ''}${data.change_pct.toFixed(2)}%`
      : '—',
    data.change_pct >= 0 ? 'pos' : 'neg');
  _setCtx('ctx-high20',
    data.pct_from_high != null
      ? `${data.pct_from_high >= 0 ? '+' : ''}${data.pct_from_high.toFixed(1)}%`
      : '—');
  _setCtx('ctx-low20',
    data.pct_from_low != null
      ? `${data.pct_from_low >= 0 ? '+' : ''}${data.pct_from_low.toFixed(1)}%`
      : '—');
  _setCtx('ctx-rvol',  data.realized_vol != null ? `${data.realized_vol.toFixed(1)}%` : '—');
  _setCtx('ctx-atr',   data.atr          != null ? fmtPrice(data.atr) : '—');
  _setCtx('ctx-size',  data.suggested_size != null ? `${data.suggested_size}%` : '—');
  const regimeEl = document.getElementById('ctx-regime');
  if (regimeEl && data.regime) {
    regimeEl.textContent = data.regime;
    regimeEl.className   = `regime-chip ${data.regime.toLowerCase()}`;
  }
}

function clearContextBar() {
  ['ctx-last','ctx-chg','ctx-high20','ctx-low20','ctx-rvol','ctx-atr','ctx-size'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.textContent = '—'; el.className = 'ctx-value'; }
  });
  const regimeEl = document.getElementById('ctx-regime');
  if (regimeEl) { regimeEl.textContent = '—'; regimeEl.className = 'regime-chip'; }
}

function _setCtx(id, val, extraClass) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = val;
  if (extraClass) el.className = `ctx-value ${extraClass}`;
}

// ── trade structure ───────────────────────────────────────────────────────────

function renderTradeStructure(ts) {
  const badge = document.getElementById('ts-direction-badge');
  if (!ts || ts.error) {
    if (badge) { badge.textContent = '—'; badge.className = 'dir-badge'; }
    ['ts-entry','ts-invalid','ts-t1','ts-t2','ts-size','ts-rr','ts-note','ts-trigger'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = ts && ts.error ? ts.error : '—';
    });
    return;
  }
  if (badge) {
    badge.textContent = ts.direction;
    badge.className   = `dir-badge ${ts.direction === 'LONG' ? 'long' : 'short'}`;
  }
  const _s = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val ?? '—'; };
  _s('ts-entry',   ts.entry_zone);
  _s('ts-invalid', ts.invalidation);
  _s('ts-t1',      ts.target1);
  _s('ts-t2',      ts.target2);
  _s('ts-size',    ts.size_pct != null ? `${ts.size_pct}%` : '—');
  _s('ts-rr',      ts.rr       != null ? `${ts.rr}×`      : '—');
  _s('ts-note',    ts.risk_note);
  _s('ts-trigger', ts.trigger_text);
}

function clearTradeStructure() {
  renderTradeStructure(null);
}

// ── why-now ───────────────────────────────────────────────────────────────────

function renderWhyNow(bullets) {
  const container = document.getElementById('whynow-bullets');
  if (!container) return;
  if (!bullets || bullets.length === 0) {
    container.innerHTML = '<div class="inline-state">No signal context available.</div>';
    return;
  }
  container.innerHTML = bullets.map(b => `
    <div class="why-bullet">
      <span class="why-tag">${esc(b.tag)}</span>
      <span class="why-text">${esc(b.text)}</span>
    </div>`).join('');
}

function clearWhyNow() {
  const container = document.getElementById('whynow-bullets');
  if (container) container.innerHTML = '<div class="inline-state">Loading…</div>';
}

// ── news ──────────────────────────────────────────────────────────────────────

function renderNews(items, name, symbol) {
  const list  = document.getElementById('news-list');
  const label = document.getElementById('news-instrument-label');
  if (label) label.textContent = `— ${name} (${symbol})`;
  if (!list) return;

  if (!items || items.length === 0) {
    list.innerHTML = `<div class="inline-state">No linked headlines for ${esc(name)} (${esc(symbol)}).</div>`;
    return;
  }

  list.innerHTML = items.map((n, i) => {
    const ts    = n.published ? timeAgo(n.published * 1000) : '';
    const lbl   = n.label || '';
    const rtag  = n.risk_tag || 'market';
    const badge = lbl
      ? `<span class="sentiment-badge ${esc(lbl)}">${esc(lbl)}</span>`
      : `<span class="risk-tag ${esc(rtag)}">${esc(rtag)}</span>`;
    const meta  = [n.publisher, ts].filter(Boolean).join(' · ');
    const bullets = (n.bullets || []);
    const bulletsHtml = bullets.length
      ? `<div class="news-bullets">${bullets.map(b => `<div class="news-bullet">• ${esc(b)}</div>`).join('')}</div>`
      : '';
    const href = n.link ? esc(n.link) : '#';
    return `<div class="news-item" onclick="toggleNewsExpand(this, event)" data-idx="${i}">
      <div class="news-row1">
        <span class="news-title">${esc(n.title)}</span>
        ${badge}
      </div>
      ${meta ? `<div class="news-meta">${esc(meta)}</div>` : ''}
      <div class="news-expand">
        ${bulletsHtml}
        <a class="news-open-link" href="${href}" target="_blank" rel="noopener noreferrer"
           onclick="event.stopPropagation()">Open article ↗</a>
      </div>
    </div>`;
  }).join('');
}

function clearNews() {
  const list = document.getElementById('news-list');
  if (list) list.innerHTML = '<div class="inline-state">Loading…</div>';
}

function toggleNewsExpand(el, event) {
  // Don't toggle if they clicked the open-link directly
  if (event.target.classList.contains('news-open-link')) return;
  const wasOpen = el.classList.contains('news-expanded');
  // Close all others
  document.querySelectorAll('.news-item.news-expanded').forEach(n => n.classList.remove('news-expanded'));
  if (!wasOpen) el.classList.add('news-expanded');
}

// ── utils ─────────────────────────────────────────────────────────────────────

function fmtPrice(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (isNaN(n)) return '—';
  if (n >= 1000) return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 10)   return n.toFixed(2);
  if (n >= 1)    return n.toFixed(3);
  return n.toFixed(4);
}

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function timeAgo(ms) {
  const diff = Date.now() - ms;
  const m = Math.floor(diff / 60_000);
  const h = Math.floor(diff / 3_600_000);
  const d = Math.floor(diff / 86_400_000);
  if (d > 0) return `${d}d ago`;
  if (h > 0) return `${h}h ago`;
  if (m > 0) return `${m}m ago`;
  return 'just now';
}
