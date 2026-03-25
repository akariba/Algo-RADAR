"""
services/backtest.py — Walk-forward backtest engine for RADAR research page.

Execution model (no look-ahead):
  ─ Signals computed at bar t CLOSE using only data available through t
  ─ Trades entered at bar t+1 OPEN (next-bar execution)
  ─ Stop/target checked intrabar (low vs stop for longs; high vs stop for shorts)
  ─ If price gaps through stop: exit at OPEN of gapping bar (conservative)
  ─ Transaction cost: 0.05% per side = 0.10% round trip (includes slippage estimate)
  ─ Position sizing: 100% of equity, one position at a time
  ─ IS/OOS split: chronological, default 70% in-sample / 30% out-of-sample

Strategies:
  regime_adaptive_trend  — EMA cross + RSI filter + optional vol gate
  mean_reversion         — RSI/BB extremes, fade-the-move
  momentum_breakout      — N-day channel breakout with vol confirmation
  volatility_filtered    — Same as RAT but only trades below vol threshold

All strategies: entry on next bar open, ATR-based stop and target.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)

COST_PER_SIDE = 0.0005   # 0.05% per leg = 0.10% round trip


# ── indicator series ─────────────────────────────────────────────────────────

def _ema_s(values: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi_s(closes: np.ndarray, period: int = 14) -> np.ndarray:
    n      = len(closes)
    result = np.full(n, 50.0)
    if n <= period:
        return result
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas,  0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = gains[:period].mean()
    avg_l  = losses[:period].mean()
    for i in range(period, n - 1):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        result[i + 1] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return result


def _atr_s(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
           period: int = 14) -> np.ndarray:
    n  = len(closes)
    tr = np.zeros(n)
    tr[1:] = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]),
                   np.abs(lows[1:]  - closes[:-1])),
    )
    atr = np.zeros(n)
    if n <= period:
        return atr
    atr[period] = tr[1:period + 1].mean()
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _bb_s(closes: np.ndarray, period: int = 20) -> tuple:
    n    = len(closes)
    mid  = np.full(n, float(closes[0]))
    up   = np.full(n, float(closes[0]))
    lo   = np.full(n, float(closes[0]))
    for i in range(period - 1, n):
        w      = closes[i - period + 1:i + 1]
        m      = w.mean()
        s      = w.std()
        mid[i] = m
        up[i]  = m + 2 * s
        lo[i]  = m - 2 * s
    return mid, up, lo


def _rvol_s(closes: np.ndarray, window: int = 20) -> np.ndarray:
    n   = len(closes)
    out = np.full(n, 0.20)
    if n < window + 1:
        return out
    log_ret = np.diff(np.log(closes))
    for i in range(window, n):
        out[i] = float(log_ret[i - window:i].std() * np.sqrt(252))
    return out


def _regime_s(ema12: np.ndarray, ema26: np.ndarray, rsi: np.ndarray,
              rvol: np.ndarray) -> list:
    regimes = []
    for i in range(len(ema12)):
        spread = (ema12[i] - ema26[i]) / ema26[i] if ema26[i] > 0 else 0
        v      = rvol[i]
        r      = rsi[i]
        if abs(spread) > 0.01 and 40 < r < 70:
            regimes.append('trending')
        elif r > 65 or r < 35:
            regimes.append('extended')
        elif v > 0.30:
            regimes.append('stress')
        else:
            regimes.append('ranging')
    return regimes


# ── strategy signal generators ───────────────────────────────────────────────
# Return (raw_signals, stop_dists, target_dists)
# raw_signals[i]: 1=long, -1=short, 0=flat  (generated at bar i close)
# stop_dists[i]:  ATR-based distance from entry
# target_dists[i]: ATR-based distance to target

def _sig_rat(closes, highs, lows, opens, volumes, fast, slow,
             stop_atr, target_atr, vol_filter) -> tuple:
    ema_f = _ema_s(closes, fast)
    ema_s = _ema_s(closes, slow)
    rsi   = _rsi_s(closes, 14)
    atr   = _atr_s(highs, lows, closes, 14)
    rvol  = _rvol_s(closes, 20)
    n     = len(closes)
    sigs  = np.zeros(n, dtype=int)
    for i in range(slow, n):
        if vol_filter and rvol[i] > 0.40:
            continue
        if atr[i] == 0:
            continue
        if ema_f[i] > ema_s[i] and rsi[i] > 50:
            sigs[i] = 1
        elif ema_f[i] < ema_s[i] and rsi[i] < 50:
            sigs[i] = -1
    return sigs, atr * stop_atr, atr * target_atr


def _sig_mean_rev(closes, highs, lows, opens, volumes, fast, slow,
                  stop_atr, target_atr, vol_filter) -> tuple:
    rsi        = _rsi_s(closes, 14)
    atr        = _atr_s(highs, lows, closes, 14)
    _, bb_up, bb_lo = _bb_s(closes, 20)
    n          = len(closes)
    sigs       = np.zeros(n, dtype=int)
    for i in range(20, n):
        if atr[i] == 0:
            continue
        if rsi[i] < 30 and closes[i] < bb_lo[i]:
            sigs[i] = 1
        elif rsi[i] > 70 and closes[i] > bb_up[i]:
            sigs[i] = -1
    return sigs, atr * stop_atr, atr * target_atr


def _sig_breakout(closes, highs, lows, opens, volumes, fast, slow,
                  stop_atr, target_atr, vol_filter) -> tuple:
    atr  = _atr_s(highs, lows, closes, 14)
    rvol = _rvol_s(closes, 20)
    n    = len(closes)
    sigs = np.zeros(n, dtype=int)
    ch   = 20
    for i in range(ch, n):
        if atr[i] == 0:
            continue
        vol_ok = not vol_filter or rvol[i] < 0.45
        if not vol_ok:
            continue
        prev_high = highs[i - ch:i].max()
        prev_low  = lows[i - ch:i].min()
        if closes[i] > prev_high:
            sigs[i] = 1
        elif closes[i] < prev_low:
            sigs[i] = -1
    return sigs, atr * stop_atr, atr * target_atr


def _sig_vft(closes, highs, lows, opens, volumes, fast, slow,
             stop_atr, target_atr, vol_filter) -> tuple:
    """Volatility Filtered Trend — same as RAT but vol gate at 30% instead of 40%."""
    ema_f = _ema_s(closes, fast)
    ema_s = _ema_s(closes, slow)
    rsi   = _rsi_s(closes, 14)
    atr   = _atr_s(highs, lows, closes, 14)
    rvol  = _rvol_s(closes, 20)
    n     = len(closes)
    sigs  = np.zeros(n, dtype=int)
    for i in range(slow, n):
        if rvol[i] > 0.30:          # tighter vol gate
            continue
        if atr[i] == 0:
            continue
        if ema_f[i] > ema_s[i] and rsi[i] > 50:
            sigs[i] = 1
        elif ema_f[i] < ema_s[i] and rsi[i] < 50:
            sigs[i] = -1
    return sigs, atr * stop_atr, atr * target_atr


_STRAT_FN = {
    'regime_adaptive_trend':  _sig_rat,
    'mean_reversion':         _sig_mean_rev,
    'momentum_breakout':      _sig_breakout,
    'volatility_filtered':    _sig_vft,
}


# ── trade simulator ──────────────────────────────────────────────────────────

def _simulate(closes, highs, lows, opens, dates,
              regimes, raw_sigs, stop_dists, tgt_dists) -> tuple:
    """
    Execute trades bar-by-bar.
    Signal at bar i close → enter at bar i+1 OPEN.
    Returns (equity_series, trades_list).
    """
    n        = len(closes)
    equity   = np.ones(n, dtype=float)
    position = None   # None | dict
    trades   = []

    for i in range(1, n):
        was_flat = position is None

        # ── check exits for open position ─────────────────────────────────
        if position is not None:
            side   = position['side']
            stop_p = position['stop']
            tgt_p  = position['target']
            entry  = position['entry']
            exit_p = None
            reason = None

            if side == 1:   # LONG
                if opens[i] <= stop_p:          # gap-down through stop
                    exit_p, reason = opens[i], 'stop_gap'
                elif lows[i] <= stop_p:
                    exit_p, reason = stop_p, 'stop'
                elif highs[i] >= tgt_p:
                    exit_p, reason = tgt_p, 'target'
                elif raw_sigs[i - 1] == -1:
                    exit_p, reason = opens[i], 'reversal'
            else:            # SHORT
                if opens[i] >= stop_p:
                    exit_p, reason = opens[i], 'stop_gap'
                elif highs[i] >= stop_p:
                    exit_p, reason = stop_p, 'stop'
                elif lows[i] <= tgt_p:
                    exit_p, reason = tgt_p, 'target'
                elif raw_sigs[i - 1] == 1:
                    exit_p, reason = opens[i], 'reversal'

            if exit_p is not None:
                ret = side * (exit_p / entry - 1) - 2 * COST_PER_SIDE
                equity[i] = equity[i - 1] * (1 + ret)
                trades.append({
                    'date':         str(dates[i])[:10],
                    'symbol':       position.get('symbol', ''),
                    'side':         'LONG' if side == 1 else 'SHORT',
                    'entry':        round(float(entry), 4),
                    'exit':         round(float(exit_p), 4),
                    'return_pct':   round(float(ret * 100), 3),
                    'pnl':          round(float(ret * position['equity_in']), 4),
                    'holding_days': int(i - position['entry_idx']),
                    'exit_reason':  reason,
                    'regime':       position.get('regime', 'unknown'),
                })
                position = None
            else:
                # Mark to market
                equity[i] = equity[i - 1] * (closes[i] / closes[i - 1]) ** side
        else:
            equity[i] = equity[i - 1]

        # ── enter new position (from previous bar's signal) ───────────────
        if was_flat and raw_sigs[i - 1] != 0 and stop_dists[i - 1] > 0:
            side    = int(raw_sigs[i - 1])
            ep      = float(opens[i])
            stop_p  = ep - side * float(stop_dists[i - 1])
            tgt_p   = ep + side * float(tgt_dists[i - 1])
            position = {
                'side':      side,
                'entry':     ep,
                'stop':      stop_p,
                'target':    tgt_p,
                'entry_idx': i,
                'equity_in': float(equity[i]),
                'symbol':    '',
                'regime':    regimes[i - 1] if i > 0 else 'unknown',
            }

    # Close any open position at final bar
    if position is not None:
        side  = position['side']
        entry = position['entry']
        ep    = float(closes[-1])
        ret   = side * (ep / entry - 1) - 2 * COST_PER_SIDE
        equity[-1] = equity[-2] * (1 + ret)
        trades.append({
            'date':         str(dates[-1])[:10],
            'symbol':       position.get('symbol', ''),
            'side':         'LONG' if side == 1 else 'SHORT',
            'entry':        round(float(entry), 4),
            'exit':         round(float(ep), 4),
            'return_pct':   round(float(ret * 100), 3),
            'pnl':          round(float(ret * position['equity_in']), 4),
            'holding_days': int((len(closes) - 1) - position['entry_idx']),
            'exit_reason':  'end_of_period',
            'regime':       position.get('regime', 'unknown'),
        })

    return equity, trades


# ── drawdown helper ───────────────────────────────────────────────────────────

def _drawdown(equity: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(equity)
    return np.where(peak > 0, (equity - peak) / peak, 0.0)


# ── monthly/quarterly helpers ─────────────────────────────────────────────────

def _monthly_returns(equity: np.ndarray, dates) -> list:
    buckets: dict = {}
    for i, d in enumerate(dates):
        key = str(d)[:7]   # "YYYY-MM"
        buckets[key] = i
    ret = []
    keys = sorted(buckets.keys())
    for j in range(1, len(keys)):
        i0 = buckets[keys[j - 1]]
        i1 = buckets[keys[j]]
        if i0 < len(equity) and i1 < len(equity):
            ret.append(float(equity[i1] / equity[i0] - 1))
    return ret


# ── metrics block ─────────────────────────────────────────────────────────────

def _metrics_block(equity: np.ndarray, dates) -> dict:
    if len(equity) < 2:
        return {}
    n_years   = len(equity) / 252
    daily_ret = np.diff(np.log(np.maximum(equity, 1e-10)))
    total_ret = float(equity[-1] / equity[0] - 1)
    cagr      = float((equity[-1] / equity[0]) ** (1 / n_years) - 1) if n_years > 0 else 0.0
    vol_ann   = float(daily_ret.std() * np.sqrt(252))
    neg_ret   = daily_ret[daily_ret < 0]
    downside  = float(neg_ret.std() * np.sqrt(252)) if len(neg_ret) > 0 else 1e-10
    sharpe    = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    sortino   = float(daily_ret.mean() * 252 / downside) if downside > 0 else 0.0
    dd        = _drawdown(equity)
    max_dd    = float(dd.min())
    avg_dd    = float(dd[dd < 0].mean()) if (dd < 0).any() else 0.0
    monthly   = _monthly_returns(equity, dates)
    pos_months = sum(1 for m in monthly if m > 0) / len(monthly) if monthly else 0.5
    best_m    = float(max(monthly)) if monthly else 0.0
    worst_m   = float(min(monthly)) if monthly else 0.0
    return {
        'total_return':        _r(total_ret),
        'cagr':                _r(cagr),
        'sharpe':              _r(sharpe),
        'sortino':             _r(sortino),
        'annualized_vol':      _r(vol_ann),
        'max_drawdown':        _r(max_dd),
        'average_drawdown':    _r(avg_dd),
        'best_month':          _r(best_m),
        'worst_month':         _r(worst_m),
        'positive_months_pct': _r(pos_months),
    }


def _trade_stats(trades: list) -> dict:
    if not trades:
        return {
            'win_rate': 0, 'avg_win': 0, 'avg_loss': 0, 'median_trade': 0,
            'avg_holding_days': 0, 'positive_months_pct': 0, 'positive_quarters_pct': 0,
            'profit_factor': 0, 'n_trades': 0,
        }
    rets     = [t['return_pct'] for t in trades]
    wins     = [r for r in rets if r > 0]
    losses   = [r for r in rets if r <= 0]
    pf       = (sum(wins) / abs(sum(losses))) if losses and sum(wins) > 0 else 0.0
    hold_days = [t['holding_days'] for t in trades]
    return {
        'n_trades':             len(trades),
        'win_rate':             _r(len(wins) / len(rets)),
        'avg_win':              _r(sum(wins) / len(wins))  if wins   else 0,
        'avg_loss':             _r(sum(losses) / len(losses)) if losses else 0,
        'median_trade':         _r(float(np.median(rets))),
        'avg_holding_days':     _r(sum(hold_days) / len(hold_days)),
        'profit_factor':        _r(pf),
        'positive_months_pct':  0,
        'positive_quarters_pct': 0,
    }


def _regime_breakdown(trades: list) -> list:
    buckets: dict = {}
    for t in trades:
        r = t.get('regime', 'unknown')
        if r not in buckets:
            buckets[r] = {'pnl': 0.0, 'trade_count': 0}
        buckets[r]['pnl']         += t['return_pct']
        buckets[r]['trade_count'] += 1
    return [{'regime': k, 'pnl': _r(v['pnl']), 'trade_count': v['trade_count']}
            for k, v in sorted(buckets.items())]


# ── notes generator (deterministic, no LLM) ──────────────────────────────────

def _generate_notes(metrics: dict, trade_stats: dict, regime_breakdown: list) -> dict:
    sh    = metrics.get('sharpe', 0) or 0
    dd    = metrics.get('max_drawdown', 0) or 0
    deg   = metrics.get('degradation_pct', 0.5) or 0.5
    wr    = trade_stats.get('win_rate', 0) or 0
    pf    = trade_stats.get('profit_factor', 0) or 0
    ovf   = metrics.get('overfit_risk', 'UNKNOWN')
    cagr  = metrics.get('cagr', 0) or 0
    vol   = metrics.get('annualized_vol', 0) or 0
    oos_sh = metrics.get('oos_sharpe', 0) or 0

    # Summary
    if sh > 1.5 and ovf == 'LOW':
        summary = (f"Strong risk-adjusted performance with Sharpe {sh:.2f} and low overfitting risk. "
                   f"CAGR of {cagr*100:.1f}% with max drawdown {dd*100:.1f}% is an institutional-grade profile. "
                   f"OOS Sharpe of {oos_sh:.2f} suggests generalisability — worth continuing to validate.")
    elif sh > 0.8 and ovf in ('LOW', 'MEDIUM'):
        summary = (f"Acceptable risk-adjusted returns (Sharpe {sh:.2f}, CAGR {cagr*100:.1f}%). "
                   f"Max drawdown {dd*100:.1f}% is within manageable range. "
                   f"OOS Sharpe of {oos_sh:.2f} shows {('reasonable' if deg < 0.45 else 'notable')} performance decay — monitor regime sensitivity before deploying.")
    elif sh <= 0 or cagr <= 0:
        summary = (f"Strategy does not produce positive risk-adjusted returns in this period. "
                   f"Sharpe {sh:.2f} and CAGR {cagr*100:.1f}% indicate the current configuration should be rejected or reparametrised.")
    else:
        summary = (f"Marginal performance (Sharpe {sh:.2f}, CAGR {cagr*100:.1f}%). "
                   f"Results are below institutional deployment threshold. "
                   f"High overfitting risk ({ovf}) warrants scepticism — OOS period is critical to review.")

    # Strengths
    strengths = []
    if sh > 1.0:
        strengths.append(f"Sharpe ratio of {sh:.2f} exceeds the 1.0 threshold typically required for deployment consideration")
    if abs(dd) < 0.15:
        strengths.append(f"Maximum drawdown of {dd*100:.1f}% is well-controlled — drawdown discipline is the first gate for any live strategy")
    if pf > 1.5:
        strengths.append(f"Profit factor of {pf:.2f} indicates gross wins significantly exceed gross losses")
    if wr > 0.55:
        strengths.append(f"Win rate of {wr*100:.0f}% is above the 50% baseline — useful for psychological consistency in live trading")
    if vol < 0.18 and sh > 0.8:
        strengths.append(f"Annualised volatility of {vol*100:.1f}% is low while maintaining a respectable Sharpe — efficient use of risk budget")
    if not strengths:
        strengths.append("No significant strengths identified at current parameter settings — consider reviewing strategy design")

    # Weaknesses
    weaknesses = []
    if abs(dd) > 0.20:
        weaknesses.append(f"Maximum drawdown of {dd*100:.1f}% is above institutional tolerance — position sizing or stop logic needs improvement")
    if deg > 0.45:
        weaknesses.append(f"IS-to-OOS performance degradation of {deg*100:.0f}% suggests overfitting or regime dependency — walk-forward validation required")
    if wr < 0.40:
        weaknesses.append(f"Win rate of {wr*100:.0f}% is low — requires high avg-win/avg-loss ratio to compensate; review distribution skew")
    if sh < 0.5 and sh > 0:
        weaknesses.append(f"Sharpe of {sh:.2f} is too low for institutional deployment — returns do not justify the risk taken")
    if pf < 1.0 and pf > 0:
        weaknesses.append(f"Profit factor below 1.0 means gross losses exceed gross wins after costs — strategy is unprofitable in aggregate")
    if ovf == 'HIGH':
        weaknesses.append("High overfitting risk: in-sample and out-of-sample Sharpe diverge significantly — parameters may be curve-fitted")
    # Fill if short
    if len(weaknesses) < 2:
        weaknesses.append("Insufficient trade count to draw statistically robust conclusions — extend the backtest period")

    # Next tests
    next_tests = [
        "Walk-forward optimisation: re-run with rolling 12-month IS window and 3-month OOS to confirm stability across market regimes",
        f"Monte Carlo permutation: shuffle trade sequence 1,000× and compare strategy equity to random baseline to confirm edge significance",
        "Regime stress test: isolate performance during 2020 COVID crash, 2022 rate-hike cycle, and 2024 AI momentum phase — check for regime fragility",
    ]

    return {
        'summary':    summary,
        'strengths':  strengths[:3],
        'weaknesses': weaknesses[:3],
        'next_tests': next_tests[:3],
    }


# ── utilities ─────────────────────────────────────────────────────────────────

def _r(v) -> Optional[float]:
    """Round to 4dp; return None for NaN/Inf."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _clean_trades(trades: list, symbol: str, inst_name: str) -> list:
    out = []
    for t in trades:
        t['symbol']          = symbol
        t['instrument_name'] = inst_name
        # sanitise
        t['return_pct']  = _r(t.get('return_pct'))
        t['pnl']         = _r(t.get('pnl'))
        t['entry']       = _r(t.get('entry'))
        t['exit']        = _r(t.get('exit'))
        out.append(t)
    return out


# ── main entry point ──────────────────────────────────────────────────────────

def run_backtest(params: dict) -> dict:
    """
    Execute backtest and return full result dict matching the API contract.

    Required params keys:
      strategy, ticker, benchmark, start, end, preset,
      fast (int), slow (int), stop_atr (float), target_atr (float),
      vol_filter (bool), is_pct (float 0–1)
    """
    strategy    = params.get('strategy',   'regime_adaptive_trend')
    ticker      = params.get('ticker',     'SPY').upper()
    benchmark   = params.get('benchmark',  'SPY').upper()
    start       = params.get('start',      '')
    end         = params.get('end',        '')
    fast        = max(2, int(params.get('fast',   12)))
    slow        = max(fast + 1, int(params.get('slow', 26)))
    stop_atr    = max(0.1, float(params.get('stop_atr',   2.0)))
    target_atr  = max(0.1, float(params.get('target_atr', 4.0)))
    vol_filter  = str(params.get('vol_filter', 'true')).lower() == 'true'
    is_pct      = max(0.50, min(0.90, float(params.get('is_pct', 0.70))))

    # Preset overrides
    preset = params.get('preset', 'default')
    if preset == 'conservative':
        stop_atr, target_atr, vol_filter = 1.5, 3.0, True
    elif preset == 'aggressive':
        stop_atr, target_atr, vol_filter = 3.0, 6.0, False

    if strategy not in _STRAT_FN:
        strategy = 'regime_adaptive_trend'

    STRATEGY_NAMES = {
        'regime_adaptive_trend': 'Regime Adaptive Trend',
        'mean_reversion':        'Mean Reversion',
        'momentum_breakout':     'Momentum Breakout',
        'volatility_filtered':   'Volatility Filtered Trend',
    }

    # ── download price data ───────────────────────────────────────────────────
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(start=start or None, end=end or None,
                          period='max' if not start else None,
                          interval='1d', auto_adjust=True)
    except Exception as exc:
        return {'ok': False, 'error': f"Price download failed for {ticker}: {exc}"}

    if hist is None or hist.empty or len(hist) < 60:
        return {'ok': False, 'error': f"Insufficient data for {ticker} ({len(hist) if hist is not None else 0} bars, need ≥60)"}

    closes  = hist['Close'].values.astype(float)
    highs   = hist['High'].values.astype(float)
    lows    = hist['Low'].values.astype(float)
    opens   = hist['Open'].values.astype(float)
    volumes = hist['Volume'].values.astype(float)
    dates   = [str(d)[:10] for d in hist.index]
    n       = len(closes)

    # ── benchmark data ────────────────────────────────────────────────────────
    bench_equity = np.ones(n)
    try:
        bk       = yf.Ticker(benchmark if benchmark != ticker else 'SPY')
        bk_hist  = bk.history(start=dates[0], end=dates[-1],
                               interval='1d', auto_adjust=True)
        if bk_hist is not None and len(bk_hist) >= 10:
            bk_c = bk_hist['Close'].values.astype(float)
            bk_d = [str(d)[:10] for d in bk_hist.index]
            # Align to strategy dates
            bk_map = dict(zip(bk_d, bk_c))
            bk_prices = np.array([bk_map.get(d, np.nan) for d in dates], dtype=float)
            # Forward fill NaN
            for i in range(1, n):
                if np.isnan(bk_prices[i]):
                    bk_prices[i] = bk_prices[i - 1]
            first_valid = next((p for p in bk_prices if not np.isnan(p)), bk_prices[0])
            bk_prices  = np.where(np.isnan(bk_prices), first_valid, bk_prices)
            bench_equity = bk_prices / bk_prices[0]
    except Exception:
        pass

    # ── compute indicators and signals ───────────────────────────────────────
    ema12   = _ema_s(closes, 12)
    ema26   = _ema_s(closes, 26)
    rsi     = _rsi_s(closes, 14)
    rvol    = _rvol_s(closes, 20)
    regimes = _regime_s(ema12, ema26, rsi, rvol)

    sig_fn         = _STRAT_FN[strategy]
    raw_sigs, stop_dists, tgt_dists = sig_fn(
        closes, highs, lows, opens, volumes,
        fast, slow, stop_atr, target_atr, vol_filter
    )

    # ── simulate trades ───────────────────────────────────────────────────────
    equity, trades = _simulate(closes, highs, lows, opens, dates,
                               regimes, raw_sigs, stop_dists, tgt_dists)

    inst_name = ticker  # could look up from universe; keep simple here
    trades    = _clean_trades(trades, ticker, inst_name)

    # ── IS / OOS split ────────────────────────────────────────────────────────
    is_end   = int(n * is_pct)
    is_eq    = equity[:is_end]
    oos_eq   = equity[is_end:]

    is_m     = _metrics_block(is_eq,  dates[:is_end])
    oos_m    = _metrics_block(oos_eq, dates[is_end:])
    is_sh    = is_m.get('sharpe',  0) or 0
    oos_sh   = oos_m.get('sharpe', 0) or 0
    deg      = ((is_sh - oos_sh) / is_sh) if is_sh > 0 else 0.5
    deg      = max(-1.0, min(1.0, deg))
    overfit  = ('HIGH'   if deg > 0.60 or oos_sh < 0.3
                else 'LOW' if deg < 0.30 and oos_sh >= 0.8
                else 'MEDIUM')

    full_m      = _metrics_block(equity, dates)
    trade_stats = _trade_stats(trades)
    regime_bd   = _regime_breakdown(trades)

    full_m.update({
        'is_total_return':  is_m.get('total_return'),
        'oos_total_return': oos_m.get('total_return'),
        'is_sharpe':        is_m.get('sharpe'),
        'oos_sharpe':       oos_m.get('sharpe'),
        'degradation_pct':  _r(deg),
        'overfit_risk':     overfit,
        'profit_factor':    trade_stats.get('profit_factor'),
        'win_rate':         trade_stats.get('win_rate'),
    })

    notes = _generate_notes(full_m, trade_stats, regime_bd)

    # ── build output arrays (limit size to 2000 points) ──────────────────────
    step = max(1, n // 2000)
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)

    dd_strat = _drawdown(equity)
    dd_bench = _drawdown(bench_equity)

    eq_curve = [
        {'date':         dates[i],
         'strategy':     _r(float(equity[i])),
         'benchmark':    _r(float(bench_equity[i])),
         'regime':       regimes[i]}
        for i in idxs
    ]

    dd_curve = [
        {'date':          dates[i],
         'strategy_dd':   _r(float(dd_strat[i])),
         'benchmark_dd':  _r(float(dd_bench[i]))}
        for i in idxs
    ]

    is_split_date = dates[is_end] if is_end < n else dates[-1]

    return {
        'ok': True,
        'meta': {
            'strategy_name':    STRATEGY_NAMES.get(strategy, strategy),
            'strategy_id':      strategy,
            'selection_type':   'single',
            'symbol':           ticker,
            'instrument_name':  inst_name,
            'benchmark':        benchmark,
            'run_id':           datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'data_status':      'derived_from_historical_prices',
            'n_bars':           n,
            'start_date':       dates[0],
            'end_date':         dates[-1],
            'is_split_date':    is_split_date,
            'cost_per_side_pct': COST_PER_SIDE * 100,
            'execution':        'next_bar_open',
            'preset':           preset,
        },
        'equity_curve':    eq_curve,
        'drawdown':        dd_curve,
        'trades':          trades,
        'distribution':    {
            **trade_stats,
            'positive_months_pct':  full_m.get('positive_months_pct'),
        },
        'metrics':         full_m,
        'regime_breakdown': regime_bd,
        'notes':            notes,
    }
