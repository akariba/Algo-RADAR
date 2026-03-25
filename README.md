<div align="center">

# ⚡ SYBIL Algo-RADAR

### Cross-Asset Opportunity Radar — Live Trading Intelligence Platform

[![Python](https://img.shields.io/badge/Python-3.11+-3b7ae8?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-00bfa0?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![Plotly](https://img.shields.io/badge/Plotly-2.26-e05252?style=for-the-badge&logo=plotly&logoColor=white)](https://plotly.com)
[![IBKR](https://img.shields.io/badge/IBKR-ib__insync-d4a03a?style=for-the-badge)](https://ib-insync.readthedocs.io)
[![License](https://img.shields.io/badge/License-MIT-4a6080?style=for-the-badge)](LICENSE)

> **Real-time cross-asset signal scanner** with IBKR live data, multi-timeframe charts, GBM forecast cones, order-flow analysis, 3D research surfaces, and a full no-look-ahead backtest engine — all in a single Flask app with zero frontend framework dependencies.

</div>

---

## 📺 Live Demo

```
http://localhost:5055          → Main Radar
http://localhost:5055/lab/3d   → 3D Research Lab
http://localhost:5055/research/backtest → Strategy Backtest
```

---

## 🖥️ Screenshots

### Main Opportunity Radar
> Live tape strip · ranked opportunity list · multi-timeframe chart · GBM forecast cone · volume & order flow · trade structure · news sentiment

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ RADAR │ SPY -4.71% │ QQQ +0.41% │ GLD +2.59% │ CL -1.55% │ BTC ERR │ IBKR │
├──────────────────┬──────────────────────────────────────────────────────────┤
│ OPPORTUNITY      │  #1  WTI Crude Oil Futures  CL          90.87  -1.60%   │
│ RADAR            │ ─────────────────────────────────────────────────────── │
│                  │  BIAS: LONG │ SETUP: Trend Continuation │ ACTION: LOW   │
│  1  WTI   LONG   │  ENTRY QUALITY: STRONG │ TRIGGER: hold above 85.08     │
│  2  GLD   LONG   │                                                         │
│  3  SLV   LONG   │  ┌─ 1D ──── 1W ──── 1H ──── 5M ────[GBM]──[VOL]──┐   │
│  4  GC=F  LONG   │  │                         ╱╲                      │   │
│  5  SI=F  LONG   │  │     ────────────────╱╲╱  ╲   GBM ±2σ cone      │   │
│  6  SMH   SHORT  │  │  ╱────                   ╲──────────────────    │   │
│  7  QQQ   SHORT  │  │ ╱ candlesticks + EMAs                           │   │
│  8  SPY   SHORT  │  └─────────────────────────────────────────────────┘   │
│  9  SMH   SHORT  │  ▓▓▓▓░░▓▓▓▓░░░▓▓▓  ← Volume (green/red bars)          │
│ 10  XLK   SHORT  │  ────────────────   ← Cumulative Delta (order flow)    │
├──────────────────┴──────────────────────────────────────────────────────────┤
│ LAST: 90.87 │ SESSION: -1.60% │ REAL.VOL: 91.3% │ ATR: 11.57 │ SZ: 3.9%  │
│ ENTRY: 87.98–92.61 │ STOP: 73.52 │ T1: 114.01 │ T2: 124.42 │ R/R: 2x    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3D Research Lab
> Density surface · Regime surface · Stress test surface · 2D/3D toggle · shock sliders

```
┌──────────────────────────────────────────────────────────────────────────┐
│  3D LAB — Experimental Research Views                                    │
├───────────┬──────────────────────┬──────────────────────┬───────────────┤
│ Controls  │  Return Density      │  Regime Surface      │  Stress Test  │
│           │  3D Surface          │  Vol × Regime Grid   │  Shock Matrix │
│ Instrument│   ╱╲  ╱╲            │   ████░░░░████       │  ┌─────────┐  │
│ [SPY ▾]   │  ╱  ╲╱  ╲ GBM      │   ████████████       │  │ NOW ●   │  │
│           │ ╱   density  ╲      │   ░░░░████████       │  │  stress │  │
│ Horizon   │╱    surface   ╲     │   trend│range│stress  │  │  bands  │  │
│ [20d ▾]   │                     │                       │  └─────────┘  │
│           │ [3D] [2D]           │ [3D] [2D]            │ [3D] [2D]     │
│ EQ shock  │ [⊙] [↑] [→]        │ metric: [conviction▾] │ eq:  ──●── 0%│
│ ──●── 0%  │                     │                       │ vol: ──●── 0%│
└───────────┴──────────────────────┴──────────────────────┴───────────────┘
```

### Backtest Research Page
> Strategy validation · IS/OOS split · equity curve · drawdown · trade distribution · metrics · research notes

```
┌─────────────┬────────────────────────────┬──────────────────────────────┐
│  CONTROLS   │  EQUITY CURVE              │  DRAWDOWN                    │
│             │                            │                              │
│ Strategy    │  1.35 ┤         ╱strategy  │   0% ┤──────────────────    │
│ [RAT ▾]     │  1.20 ┤    ╱───╱           │  -5% ┤      ╲──╱           │
│             │  1.05 ┤───╱    benchmark── │ -15% ┤  ╲──╱    ╲──────    │
│ Ticker      │  1.00 ┤IS split│OOS →      │ -25% ┤                      │
│ [SPY]       │       └────────────────    │      └────────────────       │
│             ├────────────────────────────┴──────────────────────────────┤
│ Benchmark   │  TRADES & DISTRIBUTION     │  METRICS & RESEARCH NOTES    │
│ [SPY]       │  [Distribution][Log][Regime│  [Metrics][Notes]            │
│             │                            │                              │
│ Range       │  Wins ███░░ 54%            │  Sharpe:     1.40            │
│ [5Y ▾]      │  Loss ░░███ 46%            │  CAGR:       12.0%           │
│             │                            │  Max DD:    -18.0%           │
│ [Run ▶]     │  Trades: 77 | WR: 54%      │  IS Sharpe:  1.70            │
│             │  Avg Win: +1.8%            │  OOS Sharpe: 0.80            │
│ IS: 70%     │  Profit Factor: 1.62       │  Overfit:    MEDIUM          │
└─────────────┴────────────────────────────┴──────────────────────────────┘
```

---

## ✨ Features

### 🎯 Live Opportunity Radar
- **26-instrument universe** across equities, ETFs, commodities, crypto, forex
- **Tradeability scoring** — ranks opportunities by actionability, not just momentum
- **Regime detection** — trend / range / stress classification per instrument
- **Actionability bar** — Bias · Setup Type · Actionability · Entry Quality · Trigger
- **Trade structure** — Entry zone · Stop (invalidation) · T1 · T2 · R/R · Risk note

### 📊 Multi-Timeframe Charts
- **5 timeframes**: 5M · 1H · 1D · 1W
- **GBM forecast cone** — correct ±1σ/±2σ lognormal diffusion (sqrt-scaled, not linear)
- **Volume subplot** — green/red bars + cumulative delta order-flow line
- **Indicators** — EMA 12/26 · Bollinger Bands · Support/Resistance levels
- **Toggles** — GBM on/off · Volume on/off

### 🔴 Live Market Data
- **IBKR ib_insync** — live or delayed quotes, historical bars
- **yfinance fallback** — seamless if IBKR unavailable
- **Market tape** — 14-symbol scrolling ticker strip with live prices
- **News sentiment** — per-instrument headlines with tag classification (policy / earnings / geo-risk / earnings)

### 🧪 3D Research Lab (`/lab/3d`)
- **Return Density Surface** — GBM-derived probability distribution over time horizon
- **Regime Surface** — metric heatmap across vol-regime quartiles
- **Stress Test Surface** — portfolio P&L under equity × vol shock grid
- **2D/3D toggle** per chart · camera presets · shock range sliders · "NOW" annotation

### 📈 Backtest Engine (`/research/backtest`)
- **4 strategies**: Regime Adaptive Trend · Mean Reversion · Momentum Breakout · Volatility Filtered Trend
- **No look-ahead bias** — signal at bar T close → execute at bar T+1 open
- **Transaction costs** — 0.05% per side (0.10% round-trip)
- **IS/OOS split** — default 70/30 chronological, configurable
- **Full metrics** — Sharpe · Sortino · CAGR · Max DD · Overfit Risk flag
- **Deterministic research notes** — threshold-based summary, strengths, weaknesses, next tests

---

## 🏗️ Architecture

```
radar_v1/
├── app.py                    # Flask entry point — all routes
├── services/
│   ├── signals.py            # Signal engine (EMA, RSI, BB, regime, trade structure)
│   ├── ibkr_client.py        # IBKR ib_insync wrapper (historical bars, live quotes)
│   ├── backtest.py           # Walk-forward backtest engine (no look-ahead)
│   ├── contract_map.py       # IBKR contract mappings for universe symbols
│   └── massive_client.py     # Optional external data client
├── templates/
│   ├── index.html            # Main radar page
│   ├── lab_3d.html           # 3D research lab
│   └── backtest.html         # Backtest research page
├── static/
│   ├── css/app.css           # SYBIL design token system + full UI
│   ├── css/backtest.css      # Backtest page styles
│   ├── js/app.js             # Radar frontend (Plotly charts, GBM, tape, signals)
│   ├── js/backtest.js        # Backtest frontend (equity curve, distribution, metrics)
│   └── js/lab3d.js           # 3D lab frontend (surface/heatmap toggle, sliders)
├── data/
│   └── universe.json         # 26-instrument universe definition
├── .env.example              # Environment variable template
└── requirements.txt          # Python dependencies
```

---

## 🚀 Quick Start

### 1. Clone & install

```bash
git clone https://github.com/akariba/Algo-RADAR.git
cd Algo-RADAR
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
IBKR_HOST=127.0.0.1
IBKR_PORT=4001          # TWS paper: 7497 | TWS live: 7496 | IB Gateway: 4001/4002
IBKR_CLIENT_ID=1
IBKR_CONNECT_TIMEOUT=6
APP_PORT=5055
```

### 3. Start IBKR (optional but recommended)

Open **TWS** or **IB Gateway** and enable API connections:
- TWS: `Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients`
- Port: `4001` (Gateway) or `7496/7497` (TWS live/paper)

> Without IBKR, the app automatically falls back to **yfinance** for all data.

### 4. Run

```bash
python3.11 app.py
```

Open `http://localhost:5055`

---

## 📡 API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Main radar page |
| `/lab/3d` | GET | 3D research lab |
| `/research/backtest` | GET | Backtest research page |
| `/api/health` | GET | IBKR connection status |
| `/api/market/tape` | GET | Live tape (14 symbols) |
| `/api/opportunities` | GET | Ranked top-12 opportunities |
| `/api/instrument?ticker=SPY&tf=1d` | GET | Full signal + chart + news |
| `/api/backtest?strategy=regime_adaptive_trend&ticker=SPY` | GET | Run backtest |

### Backtest parameters

| Param | Default | Options |
|---|---|---|
| `strategy` | `regime_adaptive_trend` | `mean_reversion` · `momentum_breakout` · `volatility_filtered` |
| `ticker` | `SPY` | Any yfinance symbol |
| `benchmark` | `SPY` | Any yfinance symbol |
| `start` | 5Y ago | `YYYY-MM-DD` |
| `end` | today | `YYYY-MM-DD` |
| `fast` | `12` | EMA fast period |
| `slow` | `26` | EMA slow period |
| `stop_atr` | `2.0` | ATR stop multiplier |
| `target_atr` | `4.0` | ATR target multiplier |
| `is_pct` | `0.70` | IS/OOS split ratio |
| `vol_filter` | `true` | Volatility gate on/off |

---

## 🎨 Design System

Built on the **SYBIL design token system**:

```css
--sybil-bg-main:    #07101e   /* deep navy background */
--sybil-accent-1:   #3b7ae8   /* electric blue — primary action */
--sybil-accent-2:   #00bfa0   /* teal — brand identity */
--sybil-text-main:  #b4caeb   /* primary text */
--sybil-pos:        #2db87a   /* long / profit */
--sybil-danger:     #e05252   /* short / loss / stop */
```

---

## 📦 Requirements

```
flask>=3.0
flask-cors
ib_insync
yfinance
numpy
pandas
python-dotenv
```

---

## ⚙️ Signal Engine

The signal engine (`services/signals.py`) computes per-instrument:

| Signal | Method |
|---|---|
| Direction bias | EMA 12/26 cross + RSI filter |
| Technical conviction | Multi-factor score (0–100) |
| Tradeability score | Conviction × location × regime × R/R |
| Actionability state | WAIT / LOW / MEDIUM / HIGH / PRIME |
| Entry quality | poor / acceptable / good / strong |
| Setup type | trend continuation / mean-reversion / breakout / range-fade |
| Regime | trend / ranging / stress |
| Trade structure | Entry zone · Stop · T1 · T2 · R/R · Risk note |
| GBM expected 5D | Lognormal drift projection |
| Realized vol | Annualised from log returns |

---

## ⚠️ Disclaimer

This software is for **research and educational purposes only**.
It does not constitute financial advice.
Past backtest performance does not guarantee future results.
Use at your own risk. Always paper-trade before going live.

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

<div align="center">

**Built with Python · Flask · Plotly · ib_insync · yfinance**

*SYBIL Algo-RADAR — serious tools for serious research*

</div>
