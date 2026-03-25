/* ============================================================
   SYBIL — Backtest Research Page
   /static/js/backtest.js
   ============================================================ */

'use strict';

// ── state ────────────────────────────────────────────────────────────────────
let _lastResult = null;
let _tabState   = { trades: 'dist', metrics: 'metrics' };
let _runMode    = 'isoos';   // 'isoos' | 'wfo'

// ── init ─────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  setDatePreset('5y', document.querySelector('.date-preset[data-preset="5y"]'));
  document.getElementById('ctrl-ticker').addEventListener('keydown', e => {
    if (e.key === 'Enter') runBacktest();
  });
});

// ── date presets ─────────────────────────────────────────────────────────────
function setDatePreset(preset, btn) {
  document.querySelectorAll('.date-preset').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const now   = new Date();
  const end   = _fmtDate(now);
  let   start = '';

  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();

  if      (preset === 'ytd')  { start = `${y}-01-01`; }
  else if (preset === '1y')   { start = _fmtDate(new Date(y - 1, m, d)); }
  else if (preset === '3y')   { start = _fmtDate(new Date(y - 3, m, d)); }
  else if (preset === '5y')   { start = _fmtDate(new Date(y - 5, m, d)); }
  else if (preset === 'full') { start = '2000-01-01'; }

  document.getElementById('ctrl-start').value = start;
  document.getElementById('ctrl-end').value   = end;
}

function _fmtDate(d) {
  return d.toISOString().split('T')[0];
}

// ── run mode toggle ───────────────────────────────────────────────────────────
function setRunMode(mode, btn) {
  _runMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('ctrl-wfo-section').classList.toggle('hidden', mode !== 'wfo');
}

// ── advanced toggle ───────────────────────────────────────────────────────────
function toggleAdvanced(labelEl) {
  const panel = document.getElementById('ctrl-advanced');
  const arrow = labelEl.querySelector('.ctrl-arrow');
  const open  = panel.classList.toggle('hidden');
  arrow.textContent = open ? '▸' : '▾';
}

// ── tab switching ─────────────────────────────────────────────────────────────
function switchTab(panelKey, tabKey, btn) {
  _tabState[panelKey] = tabKey;
  const panelId = panelKey === 'trades' ? 'panel-trades' : 'panel-metrics';
  const panel   = document.getElementById(panelId);

  panel.querySelectorAll('.panel-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const prefix = panelKey === 'trades' ? 'tab-trades-' : 'tab-metrics-';
  panel.querySelectorAll('.tab-pane').forEach(p => p.classList.add('hidden'));
  const target = document.getElementById(prefix + tabKey);
  if (target) target.classList.remove('hidden');
}

// ── panel state helpers ───────────────────────────────────────────────────────
function _setState(panel, role) {
  panel.querySelectorAll('[data-role]').forEach(el => {
    el.classList.toggle('hidden', el.dataset.role !== role);
  });
}

function _setPanelState(panelId, role) {
  _setState(document.getElementById(panelId), role);
}

function _setAllPanels(role) {
  ['panel-equity', 'panel-drawdown', 'panel-trades', 'panel-metrics'].forEach(id =>
    _setPanelState(id, role)
  );
  if (role === 'loading') {
    _setPanelState('panel-wfo', role);
  }
}

// ── run ───────────────────────────────────────────────────────────────────────
function _commonParams() {
  return {
    strategy:   document.getElementById('ctrl-strategy').value,
    ticker:     document.getElementById('ctrl-ticker').value.trim().toUpperCase() || 'SPY',
    start:      document.getElementById('ctrl-start').value,
    end:        document.getElementById('ctrl-end').value,
    fast:       document.getElementById('ctrl-fast').value,
    slow:       document.getElementById('ctrl-slow').value,
    stop_atr:   document.getElementById('ctrl-stop-atr').value,
    target_atr: document.getElementById('ctrl-target-atr').value,
    vol_filter: document.getElementById('ctrl-vol-filter').checked ? 'true' : 'false',
  };
}

function runBacktest() {
  const btn = document.getElementById('btn-run');
  btn.disabled    = true;
  btn.textContent = 'Running…';

  const status = document.getElementById('bt-run-status');
  status.textContent = '';

  // Hide WFO row; decide which to show after response
  document.getElementById('bt-row-wfo').classList.add('hidden');
  document.getElementById('bt-row-mc').classList.add('hidden');
  document.getElementById('bias-warnings-banner').classList.add('hidden');

  _setAllPanels('loading');

  if (_runMode === 'wfo') {
    const p = new URLSearchParams({
      ..._commonParams(),
      n_folds:   document.getElementById('ctrl-n-folds').value,
      oos_ratio: (parseFloat(document.getElementById('ctrl-oos-ratio').value) / 100).toFixed(2),
      wfo_mode:  document.getElementById('ctrl-wfo-mode').value,
    });
    fetch(`/api/backtest/wfo?${p}`)
      .then(r => r.json())
      .then(data => {
        btn.disabled    = false;
        btn.textContent = 'Run Backtest';
        if (!data.ok) {
          _setAllPanels('error');
          status.textContent = data.error || 'WFO error';
          return;
        }
        _lastResult = data;
        _renderWfo(data);
      })
      .catch(err => {
        btn.disabled    = false;
        btn.textContent = 'Run Backtest';
        _setAllPanels('error');
        status.textContent = 'Network error: ' + err.message;
      });
    return;
  }

  // IS/OOS mode
  const params = new URLSearchParams({
    ..._commonParams(),
    benchmark:  document.getElementById('ctrl-benchmark').value.trim().toUpperCase() || 'SPY',
    is_pct:     (parseFloat(document.getElementById('ctrl-is-pct').value) / 100).toFixed(2),
  });

  fetch(`/api/backtest?${params}`)
    .then(r => r.json())
    .then(data => {
      btn.disabled    = false;
      btn.textContent = 'Run Backtest';
      if (!data.ok) {
        _setAllPanels('error');
        status.textContent = data.error || 'Backend error';
        return;
      }
      _lastResult = data;
      _renderAll(data);
    })
    .catch(err => {
      btn.disabled    = false;
      btn.textContent = 'Run Backtest';
      _setAllPanels('error');
      status.textContent = 'Network error: ' + err.message;
    });
}

// ── render all panels ─────────────────────────────────────────────────────────
function _renderAll(d) {
  _renderMeta(d.meta);
  _renderEquityChart(d.equity_curve, d.drawdown, d.meta);
  _renderTradesPanel(d.trades, d.distribution, d.regime_breakdown);
  _renderMetricsPanel(d.metrics, d.notes);
  _renderBiasWarnings(d.bias_warnings || []);
  _renderMonteCarlo(d.monte_carlo, d.trades);
}

// ── meta sidebar ─────────────────────────────────────────────────────────────
function _renderMeta(meta) {
  document.getElementById('meta-strategy').textContent = meta.strategy_name || '—';
  document.getElementById('meta-symbol').textContent   = meta.symbol        || '—';
  document.getElementById('meta-bars').textContent     = (meta.n_bars || 0).toLocaleString();
  document.getElementById('meta-is-split').textContent = meta.is_split_date  || '—';
  document.getElementById('meta-cost').textContent     = meta.cost_per_side_pct != null
    ? (+meta.cost_per_side_pct).toFixed(3) + '%' : '—';
  document.getElementById('meta-exec').textContent     = meta.execution || '—';
  document.getElementById('bt-meta').classList.remove('hidden');
}

// ── equity + drawdown charts ──────────────────────────────────────────────────
function _renderEquityChart(curve, drawdown, meta) {
  const dates = curve.map(r => r.date);
  const strat = curve.map(r => r.strategy);
  const bench = curve.map(r => r.benchmark);

  const isSplitDate = meta.is_split_date;
  const isSplitIdx  = dates.indexOf(isSplitDate);
  const splitX      = isSplitIdx >= 0 ? dates[isSplitIdx] : null;

  const palette = _palette();

  // — equity traces —
  const equityTraces = [
    {
      x: dates, y: bench,
      name: meta.benchmark || 'Benchmark',
      type: 'scatter', mode: 'lines',
      line: { color: palette.dim, width: 1.2, dash: 'dot' },
    },
    {
      x: dates, y: strat,
      name: meta.symbol || 'Strategy',
      type: 'scatter', mode: 'lines',
      line: { color: palette.accent1, width: 1.8 },
      fill: 'tonexty', fillcolor: 'rgba(59,122,232,0.04)',
    },
  ];

  const equityShapes = splitX ? [_isOosLine(splitX, palette)] : [];
  const equityAnnots = splitX ? [_isOosAnnotation(splitX, palette)] : [];

  const equityLayout = _baseLayout({
    title: '',
    shapes:      equityShapes,
    annotations: equityAnnots,
    yaxis:  { tickformat: '.1f', nticks: 6, ...palette.axis },
    xaxis:  { type: 'date', nticks: 8, tickformat: '%b %Y', ...palette.axis },
    height: 220,
    margin: { t: 10, r: 14, b: 36, l: 48 },
  });

  Plotly.newPlot('chart-equity', equityTraces, equityLayout, _plotlyConfig());
  _setPanelState('panel-equity', 'content');

  // — drawdown traces —
  const ddDates  = drawdown.map(r => r.date);
  const ddStrat  = drawdown.map(r => r.strategy_dd * 100);
  const ddBench  = drawdown.map(r => r.benchmark_dd * 100);

  const ddTraces = [
    {
      x: ddDates, y: ddBench,
      name: meta.benchmark || 'Benchmark',
      type: 'scatter', mode: 'lines',
      line: { color: palette.dim, width: 1, dash: 'dot' },
      fill: 'tozeroy', fillcolor: 'rgba(128,128,160,0.04)',
    },
    {
      x: ddDates, y: ddStrat,
      name: meta.symbol || 'Strategy',
      type: 'scatter', mode: 'lines',
      line: { color: palette.danger, width: 1.4 },
      fill: 'tozeroy', fillcolor: 'rgba(220,60,60,0.06)',
    },
  ];

  const ddShapes = splitX ? [_isOosLine(splitX, palette)] : [];

  const ddLayout = _baseLayout({
    title: '',
    shapes: ddShapes,
    yaxis:  { tickformat: '.1f', ticksuffix: '%', nticks: 5, ...palette.axis },
    xaxis:  { type: 'date', nticks: 8, tickformat: '%b %Y', ...palette.axis },
    height: 220,
    margin: { t: 10, r: 14, b: 36, l: 52 },
  });

  Plotly.newPlot('chart-drawdown', ddTraces, ddLayout, _plotlyConfig());
  _setPanelState('panel-drawdown', 'content');
}

// ── trades panel ──────────────────────────────────────────────────────────────
function _renderTradesPanel(trades, dist, regimeBreakdown) {
  _renderDistChart(trades);
  _renderDistStats(dist);
  _renderTradeLog(trades);
  _renderRegimeChart(regimeBreakdown);
  _setPanelState('panel-trades', 'content');
  // show active tab
  const currentTab = _tabState.trades || 'dist';
  document.querySelectorAll('#panel-trades .tab-pane').forEach(p => p.classList.add('hidden'));
  const el = document.getElementById('tab-trades-' + currentTab);
  if (el) el.classList.remove('hidden');
}

function _renderDistChart(trades) {
  const returns = trades.map(t => t.return_pct * 100);
  if (!returns.length) return;

  const palette = _palette();
  const wins  = returns.filter(r => r > 0);
  const loses = returns.filter(r => r <= 0);

  const traces = [
    {
      x: wins,  name: 'Win',
      type: 'histogram', autobinx: true, opacity: 0.75,
      marker: { color: palette.pos },
    },
    {
      x: loses, name: 'Loss',
      type: 'histogram', autobinx: true, opacity: 0.75,
      marker: { color: palette.danger },
    },
  ];

  const layout = _baseLayout({
    barmode: 'overlay',
    xaxis: { title: 'Return %', ...palette.axis },
    yaxis: { title: 'Count', nticks: 5, ...palette.axis },
    height: 180,
    margin: { t: 10, r: 14, b: 44, l: 44 },
    showlegend: true,
    legend: { x: 0.01, y: 0.99, bgcolor: 'transparent',
              font: { color: palette.textDim, size: 11 } },
  });

  Plotly.newPlot('chart-dist', traces, layout, _plotlyConfig());
}

function _renderDistStats(dist) {
  if (!dist) return;
  const el = document.getElementById('dist-stats');
  const fmt = (v, decimals = 2, suffix = '') =>
    v != null ? (+v).toFixed(decimals) + suffix : '—';

  const cells = [
    { k: 'Trades',         v: dist.n_trades ?? '—' },
    { k: 'Win Rate',       v: fmt(dist.win_rate * 100, 1, '%') },
    { k: 'Avg Win',        v: fmt(dist.avg_win * 100, 2, '%') },
    { k: 'Avg Loss',       v: fmt(dist.avg_loss * 100, 2, '%') },
    { k: 'Median Trade',   v: fmt(dist.median_trade * 100, 2, '%') },
    { k: 'Profit Factor',  v: fmt(dist.profit_factor, 2) },
    { k: 'Avg Hold',       v: fmt(dist.avg_holding_days, 1, ' d') },
  ];

  el.innerHTML = cells.map(c =>
    `<div class="dist-stat-cell">
       <span class="dist-stat-k">${c.k}</span>
       <span class="dist-stat-v">${c.v}</span>
     </div>`
  ).join('');
}

function _renderTradeLog(trades) {
  const tbody = document.getElementById('trade-log-body');
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="no-data">No trades.</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map(t => {
    const ret    = (t.return_pct * 100).toFixed(2);
    const cls    = t.return_pct > 0 ? 'pos' : t.return_pct < 0 ? 'neg' : '';
    const side   = t.side === 1 ? '<span class="side-long">LONG</span>' : '<span class="side-short">SHORT</span>';
    return `<tr>
      <td>${t.date ? t.date.slice(0, 10) : '—'}</td>
      <td>${side}</td>
      <td>${(+t.entry).toFixed(4)}</td>
      <td>${(+t.exit).toFixed(4)}</td>
      <td class="${cls}">${ret}%</td>
      <td>${t.holding_days ?? '—'}</td>
      <td>${t.exit_reason || '—'}</td>
      <td>${t.regime || '—'}</td>
    </tr>`;
  }).join('');
}

function _renderRegimeChart(breakdown) {
  if (!breakdown || !breakdown.length) return;
  const palette = _palette();

  const sorted  = [...breakdown].sort((a, b) => b.pnl - a.pnl);
  const regimes = sorted.map(r => r.regime);
  const pnls    = sorted.map(r => +(r.pnl * 100).toFixed(2));
  const counts  = sorted.map(r => r.trade_count);

  const colors = pnls.map(p => p >= 0 ? palette.pos : palette.danger);

  const traces = [
    {
      x: pnls, y: regimes,
      name: 'Total PnL',
      type: 'bar', orientation: 'h',
      text: counts.map(c => `${c} trades`),
      textposition: 'outside',
      marker: { color: colors },
    },
  ];

  const layout = _baseLayout({
    xaxis: { ticksuffix: '%', ...palette.axis },
    yaxis: { automargin: true, ...palette.axis },
    height: 180,
    margin: { t: 10, r: 60, b: 44, l: 90 },
  });

  Plotly.newPlot('chart-regime', traces, layout, _plotlyConfig());
}

// ── metrics + notes panel ─────────────────────────────────────────────────────
function _renderMetricsPanel(metrics, notes) {
  _renderMetrics(metrics);
  _renderNotes(notes);
  _setPanelState('panel-metrics', 'content');
  const currentTab = _tabState.metrics || 'metrics';
  document.querySelectorAll('#panel-metrics .tab-pane').forEach(p => p.classList.add('hidden'));
  const el = document.getElementById('tab-metrics-' + currentTab);
  if (el) el.classList.remove('hidden');
}

function _renderMetrics(m) {
  if (!m) return;

  const fmt    = (v, d = 2, suffix = '') => v != null ? (+v).toFixed(d) + suffix : '—';
  const fmtPct = (v, d = 2)  => v != null ? (+(v * 100)).toFixed(d) + '%' : '—';
  const fmtSign = v => {
    if (v == null) return '—';
    const n = +v;
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  };

  const risk = m.overfit_risk || 'LOW';
  const riskColor = risk === 'HIGH' ? '#e05252' : risk === 'MEDIUM' ? '#d4a03a' : '#00bfa0';

  const dsrVal = m.deflated_sharpe != null
    ? `<span title="Deflated Sharpe Ratio — probability of genuine skill after multiple-testing correction (Bailey &amp; Lopez de Prado 2014)">${(+m.deflated_sharpe * 100).toFixed(1)}%</span>`
    : '—';

  const perfCells = [
    { k: 'Total Return',      v: fmtPct(m.total_return) },
    { k: 'CAGR',              v: fmtPct(m.cagr) },
    { k: 'Sharpe',            v: fmt(m.sharpe, 2) },
    { k: 'Sortino',           v: fmt(m.sortino, 2) },
    { k: 'DSR (skill prob.)', v: dsrVal },
    { k: 'Ann. Vol',          v: fmtPct(m.annualized_vol) },
  ];

  const riskCells = [
    { k: 'Max Drawdown',   v: fmtPct(m.max_drawdown) },
    { k: 'Avg Drawdown',   v: fmtPct(m.average_drawdown) },
    { k: 'Best Month',     v: fmtSign(m.best_month != null ? m.best_month * 100 : null) },
    { k: 'Worst Month',    v: fmtSign(m.worst_month != null ? m.worst_month * 100 : null) },
  ];

  const degradation = m.degradation_pct != null ? (+(m.degradation_pct * 100)).toFixed(1) + '%' : '—';
  const robustCells = [
    { k: 'IS Sharpe',      v: fmt(m.is_sharpe,  2) },
    { k: 'OOS Sharpe',     v: fmt(m.oos_sharpe, 2) },
    { k: 'Degradation',    v: degradation },
    { k: 'Overfit Risk',   v: `<span style="color:${riskColor};font-weight:600">${risk}</span>` },
  ];

  const toGrid = (cells) => cells.map(c =>
    `<div class="metric-cell">
       <span class="metric-k">${c.k}</span>
       <span class="metric-v">${c.v}</span>
     </div>`
  ).join('');

  document.getElementById('metrics-perf-grid').innerHTML   = toGrid(perfCells);
  document.getElementById('metrics-risk-grid').innerHTML   = toGrid(riskCells);
  document.getElementById('metrics-robust-grid').innerHTML = toGrid(robustCells);
}

function _renderNotes(notes) {
  if (!notes) return;

  const _block = (id, title, items) => {
    const el = document.getElementById(id);
    if (!el || !items || !items.length) return;
    el.innerHTML = `<div class="notes-title">${title}</div>` +
      items.map(s => `<div class="notes-item">• ${s}</div>`).join('');
  };

  _block('notes-summary',    'Summary',    notes.summary    ? [notes.summary]    : []);
  _block('notes-strengths',  'Strengths',  notes.strengths  || []);
  _block('notes-weaknesses', 'Weaknesses', notes.weaknesses || []);
  _block('notes-next',       'Next Tests', notes.next_tests || []);
}

// ── bias warnings ─────────────────────────────────────────────────────────────
function _renderBiasWarnings(warnings) {
  const banner = document.getElementById('bias-warnings-banner');
  if (!warnings || !warnings.length) {
    banner.classList.add('hidden');
    return;
  }
  const sevClass = { HIGH: 'warn-high', MEDIUM: 'warn-medium', LOW: 'warn-low' };
  banner.innerHTML = warnings.map(w =>
    `<div class="bias-warning ${sevClass[w.severity] || 'warn-medium'}">
       <span class="warn-icon">⚠</span>
       <span class="warn-text"><strong>${w.severity}</strong> — ${w.message}</span>
     </div>`
  ).join('');
  banner.classList.remove('hidden');
}

// ── monte carlo ───────────────────────────────────────────────────────────────
function _renderMonteCarlo(mc, trades) {
  const row = document.getElementById('bt-row-mc');
  if (!mc || !mc.ok || !mc.p5 || !mc.p5.length) {
    row.classList.add('hidden');
    return;
  }
  row.classList.remove('hidden');

  const palette = _palette();
  const n       = mc.n_trades;
  const xs      = Array.from({ length: n }, (_, i) => i + 1);

  // Observed equity from trades (cumulative product)
  let eq = 1;
  const obsY = trades.map(t => { eq *= (1 + t.return_pct / 100); return +eq.toFixed(4); });

  const traces = [
    {
      x: xs, y: mc.p95,
      name: '95th pct',
      type: 'scatter', mode: 'lines',
      line: { color: 'rgba(0,191,160,0.3)', width: 0 },
      fill: 'tonexty', fillcolor: 'rgba(0,191,160,0.08)',
      showlegend: false,
    },
    {
      x: xs, y: mc.p5,
      name: '5th pct',
      type: 'scatter', mode: 'lines',
      line: { color: 'rgba(0,191,160,0.3)', width: 0 },
      showlegend: false,
    },
    {
      x: xs, y: mc.p50,
      name: 'Median sim',
      type: 'scatter', mode: 'lines',
      line: { color: palette.accent2, width: 1.2, dash: 'dot' },
    },
    {
      x: xs, y: obsY,
      name: 'Observed',
      type: 'scatter', mode: 'lines',
      line: { color: palette.accent1, width: 1.8 },
    },
  ];

  const layout = _baseLayout({
    xaxis: { title: 'Trade #', ...palette.axis },
    yaxis: { title: 'Equity (×1)', tickformat: '.2f', ...palette.axis },
    height: 200,
    margin: { t: 10, r: 14, b: 44, l: 52 },
    showlegend: true,
    legend: { x: 0.01, y: 0.99, bgcolor: 'transparent',
              font: { color: palette.textDim, size: 10 } },
  });

  Plotly.newPlot('chart-mc', traces, layout, _plotlyConfig());

  const fmt2 = v => v != null ? (+v).toFixed(3) : '—';
  const pct  = v => v != null ? (+(v * 100)).toFixed(1) + '%' : '—';
  document.getElementById('mc-stats').innerHTML =
    `<span class="mc-stat"><span class="mc-k">Sims</span><span class="mc-v">${mc.n_sims}</span></span>` +
    `<span class="mc-stat"><span class="mc-k">5th pct final</span><span class="mc-v">${fmt2(mc.final_p5)}</span></span>` +
    `<span class="mc-stat"><span class="mc-k">Median final</span><span class="mc-v">${fmt2(mc.final_p50)}</span></span>` +
    `<span class="mc-stat"><span class="mc-k">95th pct final</span><span class="mc-v">${fmt2(mc.final_p95)}</span></span>` +
    `<span class="mc-stat"><span class="mc-k">Obs. final</span><span class="mc-v">${fmt2(mc.observed_final)}</span></span>` +
    `<span class="mc-stat"><span class="mc-k">% sims worse</span><span class="mc-v">${pct(mc.pct_sims_beat_obs)}</span></span>`;
}

// ── wfo rendering ─────────────────────────────────────────────────────────────
function _renderWfo(d) {
  // Show WFO panel, hide IS/OOS panels
  document.getElementById('bt-row-wfo').classList.remove('hidden');
  _setPanelState('panel-wfo', 'content');

  // Hide IS/OOS panels
  ['panel-equity', 'panel-drawdown', 'panel-trades', 'panel-metrics'].forEach(id => {
    _setPanelState(id, 'idle');
  });

  // WFO summary chips
  const s    = d.wfo_summary || {};
  const fmt2 = v => v != null ? (+v).toFixed(2) : '—';
  const pct1 = v => v != null ? (+(v * 100)).toFixed(0) + '%' : '—';
  document.getElementById('wfo-summary-chips').innerHTML =
    `<span class="wfo-chip">Avg OOS SR: <b>${fmt2(s.avg_oos_sharpe)}</b></span>` +
    `<span class="wfo-chip">Std: <b>${fmt2(s.std_oos_sharpe)}</b></span>` +
    `<span class="wfo-chip">Consistency: <b>${pct1(s.consistency_score)}</b></span>` +
    `<span class="wfo-chip">Avg Degrad.: <b>${pct1(s.avg_degradation)}</b></span>`;

  // Stitched OOS equity chart
  const palette = _palette();
  const curve   = d.oos_equity || [];
  if (curve.length) {
    const xs = curve.map(r => r.date);
    const ys = curve.map(r => r.equity);
    Plotly.newPlot('chart-wfo-equity', [{
      x: xs, y: ys,
      name: 'OOS equity (stitched)',
      type: 'scatter', mode: 'lines',
      line: { color: palette.accent1, width: 1.8 },
      fill: 'tozeroy', fillcolor: 'rgba(59,122,232,0.05)',
    }], _baseLayout({
      xaxis: { type: 'date', tickformat: '%b %Y', ...palette.axis },
      yaxis: { tickformat: '.2f', ...palette.axis },
      height: 200,
      margin: { t: 10, r: 14, b: 36, l: 52 },
    }), _plotlyConfig());
  }

  // Fold table
  const tbody = document.getElementById('wfo-fold-body');
  if (!d.folds || !d.folds.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="no-data">No folds.</td></tr>';
    return;
  }
  tbody.innerHTML = d.folds.map(f => {
    const isS   = (f.is_metrics  && f.is_metrics.sharpe  != null) ? (+f.is_metrics.sharpe).toFixed(2)  : '—';
    const oosS  = (f.oos_metrics && f.oos_metrics.sharpe != null) ? (+f.oos_metrics.sharpe).toFixed(2) : '—';
    const deg   = f.degradation != null ? (+(f.degradation * 100)).toFixed(0) + '%' : '—';
    const degCls = (f.degradation || 0) > 0.5 ? 'neg' : (f.degradation || 0) < 0.2 ? 'pos' : '';
    const oosCls = (f.oos_metrics && (f.oos_metrics.sharpe || 0)) > 0 ? 'pos' : 'neg';
    return `<tr>
      <td><b>${f.fold}</b></td>
      <td>${f.is_start} → ${f.is_end}</td>
      <td>${f.oos_start} → ${f.oos_end}</td>
      <td>${isS}</td>
      <td class="${oosCls}">${oosS}</td>
      <td class="${degCls}">${deg}</td>
      <td>${f.is_trades}</td>
      <td>${f.oos_trades}</td>
    </tr>`;
  }).join('');
}

// ── IS/OOS dividers ───────────────────────────────────────────────────────────
function _isOosLine(x, palette) {
  return {
    type: 'line',
    x0: x, x1: x, yref: 'paper', y0: 0, y1: 1,
    line: { color: palette.dim, width: 1, dash: 'dot' },
  };
}

function _isOosAnnotation(x, palette) {
  return {
    x, xanchor: 'left', yref: 'paper', y: 0.98, yanchor: 'top',
    text: 'OOS →',
    showarrow: false,
    font: { color: palette.dim, size: 10 },
  };
}

// ── layout helpers ────────────────────────────────────────────────────────────
function _palette() {
  return {
    bg:       '#0c1726',
    paper:    '#0c1726',
    accent1:  '#3b7ae8',
    accent2:  '#00bfa0',
    pos:      '#2db87a',
    danger:   '#e05252',
    dim:      '#4a6080',
    textDim:  '#7090a8',
    textMain: '#b4caeb',
    axis: {
      color:           '#b4caeb',
      gridcolor:       '#1a2d45',
      zerolinecolor:   '#2a3d55',
      tickfont:        { color: '#7090a8', size: 10 },
      showgrid:        true,
    },
  };
}

function _baseLayout(overrides) {
  const p = _palette();
  return Object.assign({
    paper_bgcolor: p.paper,
    plot_bgcolor:  p.bg,
    font:          { family: 'monospace', color: p.textMain, size: 11 },
    showlegend:    false,
    hovermode:     'x unified',
    hoverlabel:    { bgcolor: '#0f1e30', font: { color: p.textMain, size: 11 } },
  }, overrides);
}

function _plotlyConfig() {
  return { displayModeBar: false, responsive: true, scrollZoom: false };
}
