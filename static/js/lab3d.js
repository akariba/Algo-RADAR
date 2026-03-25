/* ─────────────────────────────────────────────────────────────────────────────
   RADAR 3D Lab — lab3d.js

   Three Plotly research surfaces:
   1. Forecast Density   — lognormal GBM return distribution
   2. Regime × Asset     — signal metric across vol-regime × asset-class grid
   3. Portfolio Stress   — 2-factor equity × vol shock P&L surface

   Each chart supports:
   - 3D surface mode
   - 2D heatmap projection mode (toggle)
   - Preset camera views
   - Shock range sliders (Chart 3)
   - Colored safe/caution/danger annotations (Chart 3)
───────────────────────────────────────────────────────────────────────────── */

'use strict';

// ── palette ────────────────────────────────────────────────────────────────────
const P = {
  bg:      '#07101e',
  surface: '#0c1828',
  panel:   '#101f34',
  border:  '#1c2f4a',
  text:    '#b4caeb',
  muted:   '#4c6a8a',
  bright:  '#dceeff',
  accent:  '#3b7ae8',
  teal:    '#00bfa0',
  pos:     '#19b85a',
  neg:     '#e53535',
  warn:    '#e5980a',
  mono:    'JetBrains Mono, monospace',
};

// ── chart mode (3d | 2d) per chart slot ───────────────────────────────────────
const chartMode = { 1: '3d', 2: '3d', 3: '3d' };

// ── state ──────────────────────────────────────────────────────────────────────
let opportunitiesData = [];
let selectedIdx       = 0;

// ── layout helpers ─────────────────────────────────────────────────────────────

function baseLayout3d() {
  return {
    paper_bgcolor: 'transparent',
    plot_bgcolor:  P.surface,
    margin: { t: 10, r: 10, b: 10, l: 10 },
    font:   { family: P.mono, size: 10, color: P.muted },
    scene: {
      bgcolor: P.surface,
      aspectmode: 'auto',
      xaxis: _axScene('X'), yaxis: _axScene('Y'), zaxis: _axScene('Z'),
      camera: { eye: { x: 1.6, y: 1.6, z: 0.9 } },
    },
    showlegend: false,
  };
}

function _axScene(title) {
  return {
    title: { text: title, font: { size: 10, color: P.text } },
    showgrid: true, gridcolor: P.border, gridwidth: 1,
    showline: false, zeroline: false,
    tickfont: { size: 8, color: P.muted },
    backgroundcolor: P.surface,
  };
}

function baseLayout2d() {
  return {
    paper_bgcolor: 'transparent',
    plot_bgcolor:  P.surface,
    margin: { t: 6, r: 80, b: 60, l: 70 },
    font:   { family: P.mono, size: 10, color: P.muted },
    xaxis: { showgrid: true, gridcolor: P.border, tickfont: { size: 9, color: P.muted },
             color: P.muted, showline: false },
    yaxis: { showgrid: true, gridcolor: P.border, tickfont: { size: 9, color: P.muted },
             color: P.muted, showline: false },
    showlegend: false,
  };
}

// ── bootstrap ──────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  try {
    const data = await fetch('/api/opportunities').then(r => r.json());
    if (!Array.isArray(data) || !data.length) throw new Error('empty');
    opportunitiesData = data;

    const sel = document.getElementById('ctrl-symbol');
    sel.innerHTML = data.map((d, i) =>
      `<option value="${i}">#${i+1} ${esc(d.symbol)} — ${esc(d.name)}</option>`
    ).join('');

    document.getElementById('status-line').textContent =
      `${data.length} signals loaded · ${new Date().toLocaleTimeString()}`;

    buildAllCharts();
  } catch (err) {
    document.getElementById('status-line').textContent = 'Signal data unavailable';
    console.error('[lab3d]', err);
  }
});

function onSymbolChange() {
  selectedIdx = parseInt(document.getElementById('ctrl-symbol').value, 10);
  buildDensitySurface();
}

function buildAllCharts() {
  buildDensitySurface();
  buildRegimeSurface();
  buildStressSurface();
}

// ── mode toggle (3D ↔ 2D heatmap) ─────────────────────────────────────────────

function setMode(slot, mode, btn) {
  chartMode[slot] = mode;
  // update sibling buttons
  const is3d = mode === '3d';
  document.getElementById(`btn${slot}-3d`).classList.toggle('active', is3d);
  document.getElementById(`btn${slot}-2d`).classList.toggle('active', !is3d);
  if (slot === 1) buildDensitySurface();
  if (slot === 2) buildRegimeSurface();
  if (slot === 3) buildStressSurface();
}

// ── camera presets ─────────────────────────────────────────────────────────────

const VIEWS = {
  perspective: { x: 1.6, y: 1.6, z: 0.9 },
  top:         { x: 0,   y: 0,   z: 2.5 },
  side:        { x: 2.5, y: 0,   z: 0.1 },
};

function setView(chartId, preset) {
  const el = document.getElementById(`chart-${chartId}`);
  if (!el || !el.layout || !el.layout.scene) return;
  Plotly.relayout(el, { 'scene.camera.eye': VIEWS[preset] || VIEWS.perspective });
}

// ═══════════════════════════════════════════════════════════════════════════════
// Chart 1 — Forecast Density Surface
// ═══════════════════════════════════════════════════════════════════════════════

function buildDensitySurface() {
  const el = document.getElementById('chart-density');
  const ld = document.getElementById('load-density');
  if (!el || !opportunitiesData.length) return;
  ld.classList.remove('hidden');

  const item    = opportunitiesData[selectedIdx] || opportunitiesData[0];
  const nFwd    = parseInt(document.getElementById('ctrl-horizon').value, 10) || 20;
  const space   = document.getElementById('ctrl-space').value;
  const mode    = chartMode[1];

  const last    = item.last || 100;
  const exp5d   = (item.expected_5d || 1.5) / 100;
  const sigAnn  = Math.max((item.realized_vol || 15) / 100, 0.03);
  const muAnn   = exp5d * (252 / 5);

  const horizons = Array.from({ length: nFwd }, (_, i) => i + 1);
  const nBuckets = 40;
  const rMin = -0.30, rMax = 0.30;
  const dr   = (rMax - rMin) / (nBuckets - 1);
  const returns = Array.from({ length: nBuckets }, (_, i) => rMin + i * dr);

  // Z: lognormal PDF probability mass per bucket
  const z = horizons.map(h => {
    const t    = h / 252;
    const muT  = (muAnn - 0.5 * sigAnn ** 2) * t;
    const sigT = sigAnn * Math.sqrt(t);
    return returns.map(r => {
      const x = Math.log(1 + r);
      if (!isFinite(x)) return 0;
      const pdf = Math.exp(-0.5 * ((x - muT) / sigT) ** 2)
                / (Math.abs(1 + r) * sigT * Math.sqrt(2 * Math.PI));
      return Math.max(0, +((pdf * dr).toFixed(5)));
    });
  });

  const xLabels = returns.map(r =>
    space === 'price'
      ? (last * (1 + r)).toFixed(2)
      : (r * 100).toFixed(1) + '%'
  );
  const yLabels = horizons.map(h => `${h}D`);

  const dirCs = item.direction === 'LONG' ? 'Greens' : 'Reds';

  let traces, layout;
  if (mode === '3d') {
    traces = [{
      type: 'surface', x: xLabels, y: yLabels, z,
      colorscale: dirCs, reversescale: false, showscale: true,
      opacity: 0.88,
      colorbar: { thickness: 10, len: 0.6,
        tickfont: { size: 8, color: P.muted }, tickformat: '.3f',
        title: { text: 'Prob', side: 'right', font: { size: 9, color: P.muted } } },
      hovertemplate: `${space === 'price' ? 'Price' : 'Return'}: %{x}<br>Horizon: %{y}<br>Density: %{z:.4f}<extra></extra>`,
      contours: { z: { show: true, usecolormap: true, project: { z: true }, width: 1 } },
    }];
    layout = baseLayout3d();
    layout.scene.xaxis.title.text = space === 'price' ? 'Price' : 'Return';
    layout.scene.yaxis.title.text = 'Horizon';
    layout.scene.zaxis.title.text = 'Prob Density';
    layout.title = {
      text: `<span style="font-size:9px;color:${P.muted}">${esc(item.symbol)} · σ=${(sigAnn*100).toFixed(1)}% · μ=${(muAnn*100).toFixed(0)}% ann · ${item.direction}</span>`,
      x: 0.5, xanchor: 'center', y: 0.97,
      font: { size: 10, color: P.muted },
    };
  } else {
    // 2D heatmap
    traces = [{
      type: 'heatmap', x: xLabels, y: yLabels, z,
      colorscale: dirCs, reversescale: false, showscale: true,
      colorbar: { thickness: 10, len: 0.7, tickfont: { size: 8, color: P.muted } },
      hovertemplate: `${space === 'price' ? 'Price' : 'Return'}: %{x}<br>Horizon: %{y}<br>Prob: %{z:.4f}<extra></extra>`,
    }];
    layout = baseLayout2d();
    layout.xaxis.title = { text: space === 'price' ? 'Price' : 'Return %', font: { size: 10, color: P.text } };
    layout.yaxis.title = { text: 'Horizon', font: { size: 10, color: P.text } };
  }

  // Update footer
  const foot = document.getElementById('foot-density');
  if (foot) foot.textContent =
    `GBM lognormal · μ=${(muAnn*100).toFixed(0)}% ann · σ=${(sigAnn*100).toFixed(1)}% ann · ${item.symbol} · ${item.direction}`;

  Plotly.react(el, traces, layout, { responsive: true, displayModeBar: true,
    modeBarButtonsToRemove: ['toImage','sendDataToCloud','select2d','lasso2d'] });
  ld.classList.add('hidden');
}

// ═══════════════════════════════════════════════════════════════════════════════
// Chart 2 — Regime × Asset Class Surface
// ═══════════════════════════════════════════════════════════════════════════════

function buildRegimeSurface() {
  const el = document.getElementById('chart-regime');
  const ld = document.getElementById('load-regime');
  if (!el || !opportunitiesData.length) return;
  ld.classList.remove('hidden');

  const metric = document.getElementById('ctrl-axis2').value || 'conviction';
  const mode   = chartMode[2];

  // Classify vol regimes by quartile
  const vols = opportunitiesData.map(d => d.realized_vol || 10).sort((a, b) => a - b);
  const q = n => vols[Math.floor(vols.length * n)];
  const regimes = ['Low Vol', 'Normal', 'Elevated', 'Extreme'];
  const regimeFn = v => v <= q(0.25) ? 0 : v <= q(0.5) ? 1 : v <= q(0.75) ? 2 : 3;

  const classes = [...new Set(opportunitiesData.map(d => d.class || 'Other'))].sort();
  const grid = regimes.map(() => classes.map(() => []));

  for (const d of opportunitiesData) {
    const ri = regimeFn(d.realized_vol || 10);
    const ci = classes.indexOf(d.class || 'Other');
    if (ci < 0) continue;
    const val = metric === 'rr'          ? (d.rr || 0)
              : metric === 'expected_5d' ? (d.expected_5d || 0)
              :                            (d.technical_conviction || d.conviction || 0);
    grid[ri][ci].push(val);
  }

  const z = grid.map(row => row.map(cell => cell.length
    ? +(cell.reduce((a, b) => a + b, 0) / cell.length).toFixed(2)
    : 0
  ));

  const zLabel = metric === 'rr' ? 'Avg R/R' : metric === 'expected_5d' ? 'Avg 5D%' : 'Avg Conv';
  const cs = [
    [0,    P.bg],
    [0.2,  '#122046'],
    [0.5,  '#1e4090'],
    [0.75, '#3b7ae8'],
    [1,    P.teal],
  ];

  let traces, layout;
  if (mode === '3d') {
    traces = [{
      type: 'surface', x: classes, y: regimes, z,
      colorscale: cs, showscale: true, opacity: 0.9,
      colorbar: { thickness: 10, len: 0.6, tickfont: { size: 8, color: P.muted },
        title: { text: zLabel, side: 'right', font: { size: 9, color: P.muted } } },
      hovertemplate: '%{x}<br>%{y}<br>' + zLabel + ': %{z:.1f}<extra></extra>',
      contours: { z: { show: true, usecolormap: true, project: { z: true }, width: 1 } },
    }];
    layout = baseLayout3d();
    layout.scene.xaxis.title.text = 'Asset Class';
    layout.scene.yaxis.title.text = 'Vol Regime';
    layout.scene.zaxis.title.text = zLabel;
  } else {
    traces = [{
      type: 'heatmap', x: classes, y: regimes, z,
      colorscale: cs, showscale: true,
      colorbar: { thickness: 10, len: 0.7, tickfont: { size: 8, color: P.muted } },
      hovertemplate: '%{x}<br>%{y}<br>' + zLabel + ': %{z:.1f}<extra></extra>',
      texttemplate: '%{z:.1f}', textfont: { size: 9, color: P.text },
    }];
    layout = baseLayout2d();
    layout.xaxis.title = { text: 'Asset Class', font: { size: 10, color: P.text } };
    layout.yaxis.title = { text: 'Vol Regime',  font: { size: 10, color: P.text } };
  }

  Plotly.react(el, traces, layout, { responsive: true, displayModeBar: true,
    modeBarButtonsToRemove: ['toImage','sendDataToCloud','select2d','lasso2d'] });
  ld.classList.add('hidden');
}

// ═══════════════════════════════════════════════════════════════════════════════
// Chart 3 — Portfolio Stress / P&L Surface
// ═══════════════════════════════════════════════════════════════════════════════

function buildStressSurface() {
  const el = document.getElementById('chart-stress');
  const ld = document.getElementById('load-stress');
  if (!el || !opportunitiesData.length) return;
  ld.classList.remove('hidden');

  const eqRange  = parseInt(document.getElementById('sl-eq').value,  10) || 30;
  const volRange = parseInt(document.getElementById('sl-vol').value, 10) || 150;
  const pSel     = document.getElementById('ctrl-portfolio').value || 'top5';
  const mode     = chartMode[3];

  const nPositions = pSel === 'top3' ? 3 : pSel === 'all' ? opportunitiesData.length : 5;
  const portfolio  = opportunitiesData.slice(0, nPositions).map(d => {
    const dir    = d.direction === 'LONG' ? 1 : -1;
    const w      = (d.suggested_size || 5) / 100;
    const cls    = d.class || 'Equity';
    const eqBeta = cls === 'ETF' || cls === 'Equity' ? 0.95
                 : cls === 'Futures'                  ? 0.55
                 : cls === 'Crypto'                   ? 0.45
                 : cls === 'Forex'                    ? 0.08 : 0.5;
    const volBeta = -dir * eqBeta * 0.35;
    return { dir, w, eqBeta, volBeta };
  });

  // Build shock grid
  const steps = 13;
  const eqShocks  = Array.from({ length: steps }, (_, i) => -eqRange  + i * 2 * eqRange  / (steps - 1));
  const volShocks = Array.from({ length: steps }, (_, i) => -volRange/2 + i * (3*volRange/2) / (steps - 1));

  const z = eqShocks.map(eq => {
    const ef = eq / 100;
    return volShocks.map(vs => {
      const vf = vs / 100;
      let pnl = 0;
      for (const pos of portfolio) {
        pnl += pos.w * (pos.dir * pos.eqBeta * ef + pos.volBeta * vf) * 100;
      }
      return +pnl.toFixed(3);
    });
  });

  const xLabels = volShocks.map(v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`);
  const yLabels = eqShocks.map(e  => `${e > 0 ? '+' : ''}${e.toFixed(0)}%`);

  // Custom diverging colorscale: red (loss) → dark → teal (gain)
  const cs = [
    [0,     '#4a0808'],
    [0.25,  '#7f1c1c'],
    [0.45,  '#0c1828'],
    [0.5,   '#101f34'],
    [0.55,  '#0c1828'],
    [0.75,  '#0a3020'],
    [1,     P.teal],
  ];

  let traces, layout;
  if (mode === '3d') {
    traces = [{
      type: 'surface', x: xLabels, y: yLabels, z,
      colorscale: cs, cmid: 0, showscale: true, opacity: 0.88,
      colorbar: { thickness: 10, len: 0.6, tickfont: { size: 8, color: P.muted },
        tickformat: '+.2f',
        title: { text: 'P&L %', side: 'right', font: { size: 9, color: P.muted } } },
      hovertemplate: 'Vol: %{x}<br>Equity: %{y}<br>P&L: %{z:+.2f}%<extra></extra>',
      contours: {
        z: { show: true, usecolormap: true, project: { z: true }, width: 1,
             highlightwidth: 2, highlightcolor: P.bright },
      },
    }];
    layout = baseLayout3d();
    layout.scene.xaxis.title.text = 'Vol Shock';
    layout.scene.yaxis.title.text = 'Equity Shock';
    layout.scene.zaxis.title.text = 'P&L %';
    layout.scene.camera = { eye: { x: 1.8, y: -1.6, z: 0.9 } };

    // Highlight zero-zero point as annotation
    layout.scene.annotations = [{
      x: xLabels[Math.floor(xLabels.length / 2)],
      y: yLabels[Math.floor(yLabels.length / 2)],
      z: 0,
      text: 'Today',
      font: { size: 9, color: P.bright },
      bgcolor: 'rgba(7,16,30,0.7)',
      bordercolor: P.border,
      arrowcolor: P.bright,
      arrowsize: 1,
      arrowwidth: 1,
    }];
  } else {
    // 2D heatmap with zone annotations
    traces = [{
      type: 'heatmap', x: xLabels, y: yLabels, z,
      colorscale: cs, zmid: 0, showscale: true,
      colorbar: { thickness: 10, len: 0.7, tickfont: { size: 8, color: P.muted },
        tickformat: '+.1f' },
      hovertemplate: 'Vol: %{x}<br>Equity: %{y}<br>P&L: %{z:+.2f}%<extra></extra>',
      texttemplate: '%{z:+.1f}', textfont: { size: 8 },
    }];

    // Find today's zero-zero position indices
    const zeroEq  = eqShocks.reduce((best, v, i) => Math.abs(v) < Math.abs(eqShocks[best]) ? i : best, 0);
    const zeroVol = volShocks.reduce((best, v, i) => Math.abs(v) < Math.abs(volShocks[best]) ? i : best, 0);

    layout = baseLayout2d();
    layout.xaxis.title = { text: 'Vol Shock', font: { size: 10, color: P.text } };
    layout.yaxis.title = { text: 'Equity Shock', font: { size: 10, color: P.text } };
    layout.annotations = [{
      x: xLabels[zeroVol], y: yLabels[zeroEq], text: '● NOW',
      font: { size: 9, color: P.bright }, showarrow: false,
      bgcolor: 'rgba(7,16,30,0.8)', bordercolor: P.border,
      xanchor: 'center', yanchor: 'middle',
    }];
  }

  // Update footer
  const foot = document.getElementById('foot-stress');
  if (foot) foot.textContent =
    `Eq ±${eqRange}% · Vol ${(-(volRange/2)).toFixed(0)}%/+${volRange}% · ${nPositions} positions · Factor model`;

  Plotly.react(el, traces, layout, { responsive: true, displayModeBar: true,
    modeBarButtonsToRemove: ['toImage','sendDataToCloud','select2d','lasso2d'] });
  ld.classList.add('hidden');
}

// ── utils ──────────────────────────────────────────────────────────────────────

function esc(s) {
  return s == null ? '' : String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
