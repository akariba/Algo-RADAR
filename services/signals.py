"""
Signal engine — pure numpy, no external ML.

Entry points:
  fetch_signals(symbol, name)
      → downloads via yfinance (used by /api/opportunities parallel sweep)

  fetch_signals_from_bars(bars, symbol, name)
      → accepts ib_insync BarData list (used by /api/instrument IBKR path)

  build_trade_structure(signal)
      → entry / invalidation / targets / risk note / trigger
  build_why_now(signal)
      → 3 trader-oriented bullets

Actionability model
───────────────────
  location_penalty     multiplier 0.1–1.0 (penalises poor entries)
  regime_alignment     multiplier 0.4–1.0 (penalises counter-regime bias)
  entry_quality        strong | acceptable | weak | wait
  tradeability_score   0–100  (replaces raw conviction as ranking key)
  actionability_state  HIGH | MEDIUM | LOW | WAIT
  setup_type           trend continuation | mean reversion | breakout |
                       failed breakout | range fade | event driven
  trigger_text         plain-English entry trigger
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


# ── math helpers ──────────────────────────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 2):])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
    )
    if len(tr) == 0:
        return 0.0
    n = min(period, len(tr))
    return float(tr[-n:].mean())


def _fmt(v: float) -> str:
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 10:
        return f"{v:.2f}"
    if v >= 1:
        return f"{v:.3f}"
    return f"{v:.4f}"


# ── actionability helpers ─────────────────────────────────────────────────────

def _calc_setup_type(
    direction: str,
    rsi: float,
    regime: str,
    vol_surge: float,
    pct_from_high: float,
    pct_from_low: float,
    bb_std: float,
    bb_mid: float,
) -> str:
    """
    Determine the primary setup type from market context.
    pct_from_high is negative (price below 20D high).
    pct_from_low is positive (price above 20D low).
    """
    near_high = pct_from_high > -1.5  # within 1.5% of 20D high
    near_low  = pct_from_low  < 1.5   # within 1.5% of 20D low

    if vol_surge > 2.5:
        return "event driven"
    if bb_mid > 0 and (bb_std / bb_mid) < 0.008:
        return "breakout"
    if rsi > 70 and direction == "SHORT":
        return "mean reversion"
    if rsi < 30 and direction == "LONG":
        return "mean reversion"
    if near_high and direction == "SHORT":
        return "failed breakout"   # shorting at 20D high = fading a potential breakout
    if near_low and direction == "LONG":
        return "mean reversion"    # buying near 20D low = bounce setup
    if regime == "RANGING" and (near_high or near_low):
        return "range fade"
    if regime == "TRENDING":
        return "trend continuation"
    return "trend continuation"


def _calc_location_penalty(
    direction: str,
    setup_type: str,
    rsi: float,
    pct_from_high: float,
    pct_from_low: float,
) -> float:
    """
    Returns a multiplier 0.1–1.0.
    1.0 = ideal entry location.  0.1 = terrible location, should WAIT.
    """
    penalty = 1.0

    # Mean reversion is EXPECTED to be at extremes — no penalty for RSI extremes
    is_mean_rev = setup_type == "mean reversion"

    if direction == "LONG":
        # Near 20D high = poor long location (buying into resistance)
        # pct_from_high is negative; -1.5 means within 1.5% of high
        if pct_from_high > -0.5:
            penalty *= 0.25   # at resistance — very poor
        elif pct_from_high > -1.5:
            penalty *= 0.55
        elif pct_from_high > -3.0:
            penalty *= 0.80

        # Near 20D low = good long location (support) — slight bonus for mean reversion
        if pct_from_low < 1.0 and is_mean_rev:
            penalty = min(1.0, penalty * 1.2)

        # Overbought RSI = bad for trend-continuation longs
        if not is_mean_rev:
            if rsi > 75:
                penalty *= 0.40
            elif rsi > 70:
                penalty *= 0.60
            elif rsi > 65:
                penalty *= 0.80

    elif direction == "SHORT":
        # Near 20D low = poor short location (shorting into support)
        if pct_from_low < 0.5:
            penalty *= 0.25   # at support — very poor
        elif pct_from_low < 1.5:
            penalty *= 0.55
        elif pct_from_low < 3.0:
            penalty *= 0.80

        # Near 20D high = good short location — bonus for mean reversion
        if pct_from_high > -1.0 and is_mean_rev:
            penalty = min(1.0, penalty * 1.2)

        # Oversold RSI = bad for shorts (high bounce risk)
        if not is_mean_rev:
            if rsi < 25:
                penalty *= 0.35
            elif rsi < 30:
                penalty *= 0.55
            elif rsi < 35:
                penalty *= 0.75

    return round(max(0.10, min(1.0, penalty)), 3)


def _calc_entry_quality(location_penalty: float, rsi: float, direction: str) -> str:
    if location_penalty >= 0.80:
        return "strong"
    if location_penalty >= 0.60:
        return "acceptable"
    if location_penalty >= 0.40:
        return "weak"
    return "wait"


def _calc_regime_alignment(direction: str, regime: str, rsi: float, ema_spread: float) -> float:
    """Returns 0.4–1.0 multiplier for how well the direction fits the regime."""
    if regime == "TRENDING":
        if (direction == "LONG" and ema_spread > 0) or (direction == "SHORT" and ema_spread < 0):
            return 1.0   # with trend
        return 0.45      # counter-trend
    if regime == "EXTENDED":
        if (rsi > 65 and direction == "SHORT") or (rsi < 35 and direction == "LONG"):
            return 0.90  # mean-reversion direction
        return 0.65      # trend-following into extension
    if regime == "COILING":
        return 0.60      # direction unclear until breakout
    # RANGING
    return 0.70


def _calc_tradeability(
    conviction: int,
    location_penalty: float,
    regime_alignment: float,
    entry_quality: str,
    vol_surge: float,
) -> int:
    """
    Compute tradeability_score 0–100.
    This is the ranking key for the Opportunity Radar.
    """
    base = conviction * 0.70             # conviction tops out at 70 base
    score = base * location_penalty      # location is the hardest gate
    score *= regime_alignment            # regime fit
    eq_mult = {"strong": 1.25, "acceptable": 1.0, "weak": 0.65, "wait": 0.30}
    score *= eq_mult.get(entry_quality, 1.0)
    if vol_surge > 2.0:
        score = min(100, score * 1.15)
    elif vol_surge > 1.5:
        score = min(100, score * 1.08)
    return int(round(max(0, min(100, score))))


def _calc_actionability_state(tradeability: int, entry_quality: str) -> str:
    if entry_quality == "wait":
        return "WAIT"
    if tradeability >= 68:
        return "HIGH"
    if tradeability >= 48:
        return "MEDIUM"
    if tradeability >= 28:
        return "LOW"
    return "WAIT"


def _calc_trigger_text(
    direction: str,
    setup_type: str,
    entry_quality: str,
    last: float,
    support: float,
    resist: float,
    atr: float,
) -> str:
    """Concise plain-English entry trigger or wait condition."""
    if entry_quality == "wait":
        if direction == "SHORT":
            return f"wait — support break below {_fmt(support)} needed first"
        else:
            return f"wait — resistance break above {_fmt(resist)} needed first"

    if setup_type == "trend continuation":
        if direction == "LONG":
            lvl = round(last - atr * 0.5, 4)
            return f"hold above {_fmt(lvl)}"
        else:
            lvl = round(last - atr * 0.5, 4)   # breakdown confirmation level
            return f"break below {_fmt(lvl)}"
    elif setup_type == "mean reversion":
        if direction == "LONG":
            lvl = round(last + atr * 0.25, 4)
            return f"reclaim {_fmt(lvl)} on volume"
        else:
            lvl = round(last - atr * 0.25, 4)
            return f"reject {_fmt(lvl)} — confirm close below"
    elif setup_type == "breakout":
        if direction == "LONG":
            return f"break above {_fmt(resist)} with volume"
        else:
            return f"break below {_fmt(support)} with volume"
    elif setup_type == "failed breakout":
        if direction == "SHORT":
            lvl = round(last - atr * 0.3, 4)
            return f"confirm close below {_fmt(lvl)}"
        else:
            lvl = round(last + atr * 0.3, 4)
            return f"confirm close above {_fmt(lvl)}"
    elif setup_type == "range fade":
        if direction == "SHORT":
            return f"reject near resistance {_fmt(resist)}"
        else:
            return f"bounce near support {_fmt(support)}"
    else:
        if direction == "LONG":
            return f"hold above {_fmt(round(last - atr * 0.5, 4))}"
        return f"break below {_fmt(round(last + atr * 0.5, 4))}"


# ── core computation ───────────────────────────────────────────────────────────

_TF_YF: dict[str, tuple[str, str]] = {
    "5m": ("5d",   "5m"),
    "1h": ("60d",  "1h"),
    "4h": ("60d",  "1h"),   # yfinance has no 4h; use 1h bars
    "1d": ("6mo",  "1d"),
    "1w": ("2y",   "1wk"),
}

# Bars per trading year — used to annualise vol correctly per timeframe
_BARS_PER_YEAR: dict[str, int] = {
    "5m": 78 * 252,    # 6.5h × 12 bars/h × 252 trading days
    "1h": 7  * 252,    # ~7 trading hours/day
    "4h": 2  * 252,    # ~2 × 4h sessions/day
    "1d": 252,
    "1w": 52,
}


def _compute(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    opens: np.ndarray,
    volumes: np.ndarray,
    dates: list[str],
    symbol: str,
    name: str,
    data_source: str = "yfinance",
    bars_per_year: int = 252,
    timeframe: str = "1d",
) -> Optional[dict]:
    if len(closes) < 20:
        return None

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    rsi   = _rsi(closes, 14)
    atr   = _atr(highs, lows, closes, 14)

    bb_len = min(20, len(closes))
    bb_mid = float(closes[-bb_len:].mean())
    bb_std = float(closes[-bb_len:].std())
    bb_up  = bb_mid + 2 * bb_std
    bb_lo  = bb_mid - 2 * bb_std

    lookback = min(22, len(closes))
    if len(closes) >= lookback + 1:
        log_ret      = np.diff(np.log(closes[-(lookback + 1):]))
        realized_vol = float(log_ret.std() * np.sqrt(bars_per_year))
    else:
        realized_vol = 0.20

    ret_5d = float(closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0

    valid_volumes = volumes[volumes > 0]
    if len(valid_volumes) >= 20:
        vol_avg20 = float(valid_volumes[-20:].mean())
        vol_surge = float(volumes[-1] / vol_avg20) if vol_avg20 > 0 and volumes[-1] > 0 else 1.0
    else:
        vol_surge = 1.0

    ema_spread = float((ema12[-1] - ema26[-1]) / ema26[-1]) if ema26[-1] != 0 else 0.0

    # ── direction ─────────────────────────────────────────────────────────────
    if ema_spread > 0.002 and rsi > 48:
        direction = "LONG"
    elif ema_spread < -0.002 and rsi < 52:
        direction = "SHORT"
    elif ret_5d > 0.01:
        direction = "LONG"
    elif ret_5d < -0.01:
        direction = "SHORT"
    else:
        direction = "LONG" if ema_spread >= 0 else "SHORT"

    # ── technical conviction (renamed from old "conviction") ──────────────────
    ema_score          = min(1.0, abs(ema_spread) / 0.05) * 100
    rsi_score          = (abs(rsi - 50) / 50) * 100
    vol_score          = min(100.0, (min(vol_surge, 3.0) / 3.0) * 100)
    mom_score          = min(100.0, (abs(ret_5d) / 0.05) * 100)
    technical_conviction = int(0.35 * ema_score + 0.25 * rsi_score + 0.20 * vol_score + 0.20 * mom_score)
    technical_conviction = max(0, min(100, technical_conviction))

    # ── regime ────────────────────────────────────────────────────────────────
    if technical_conviction >= 70:
        regime = "TRENDING"
    elif rsi < 35 or rsi > 65:
        regime = "EXTENDED"
    elif (bb_std / bb_mid < 0.008) if bb_mid > 0 else False:
        regime = "COILING"
    else:
        regime = "RANGING"

    # ── price levels ──────────────────────────────────────────────────────────
    lb       = min(20, len(closes) - 1)
    support  = round(float(lows[-lb:].min()),  4)
    resist   = round(float(highs[-lb:].max()), 4)

    n20           = min(20, len(closes))
    high_20d      = float(highs[-n20:].max())
    low_20d       = float(lows[-n20:].min())
    pct_from_high = round((closes[-1] - high_20d) / high_20d * 100, 2) if high_20d else 0.0
    pct_from_low  = round((closes[-1] - low_20d)  / low_20d  * 100, 2) if low_20d  else 0.0

    last = float(closes[-1])

    # ── actionability engine ──────────────────────────────────────────────────
    setup_type = _calc_setup_type(
        direction, rsi, regime, vol_surge,
        pct_from_high, pct_from_low, bb_std, bb_mid,
    )
    location_penalty = _calc_location_penalty(
        direction, setup_type, rsi, pct_from_high, pct_from_low,
    )
    entry_quality    = _calc_entry_quality(location_penalty, rsi, direction)
    regime_alignment = _calc_regime_alignment(direction, regime, rsi, ema_spread)
    tradeability_score = _calc_tradeability(
        technical_conviction, location_penalty, regime_alignment, entry_quality, vol_surge,
    )
    actionability_state = _calc_actionability_state(tradeability_score, entry_quality)
    trigger_text = _calc_trigger_text(
        direction, setup_type, entry_quality, last, support, resist, atr,
    )
    catalyst_support = (
        "high"   if vol_surge > 2.0 else
        "medium" if vol_surge > 1.4 else
        "low"
    )

    # ── sizing & targets ──────────────────────────────────────────────────────
    if atr > 0 and last > 0:
        atr_pct        = atr / last
        suggested_size = round(min(0.01 / (atr_pct * 2) * 100, 20.0), 1)
    else:
        suggested_size = 5.0

    rr = round((atr * 4) / (atr * 2), 1) if atr > 0 else 2.0

    # ── misc ──────────────────────────────────────────────────────────────────
    bar_vol     = realized_vol / np.sqrt(bars_per_year)
    expected_5d = round(float(bar_vol * np.sqrt(5) * 100), 2)   # 5-bar move, not always 5 calendar days

    tags: list[str] = []
    if abs(ema_spread) > 0.01 and technical_conviction >= 60:
        tags.append("trend")
    if (rsi < 35 or rsi > 70) and technical_conviction < 70:
        tags.append("mean-reversion")
    if abs(ret_5d) > 0.04:
        tags.append("momentum")
    if vol_surge > 2.0:
        tags.append("vol-event")
    if actionability_state == "WAIT":
        tags.append("wait")

    prev       = closes[-2] if len(closes) >= 2 else closes[-1]
    change     = round(float(closes[-1] - prev), 4)
    change_pct = round(float(change / prev * 100), 2) if prev else 0.0

    return {
        "symbol":               symbol,
        "name":                 name,
        "last":                 round(last, 4),
        "change":               change,
        "change_pct":           change_pct,
        # direction / setup
        "direction":            direction,
        "bias":                 direction if actionability_state != "WAIT" else direction,
        "setup_type":           setup_type,
        "trigger_text":         trigger_text,
        # scoring
        "technical_conviction": technical_conviction,
        "conviction":           technical_conviction,   # backward compat alias
        "tradeability_score":   tradeability_score,
        "actionability_state":  actionability_state,
        "entry_quality":        entry_quality,
        "location_penalty":     location_penalty,
        "regime_alignment":     round(regime_alignment, 3),
        "catalyst_support":     catalyst_support,
        # original fields
        "expected_5d":          expected_5d,
        "suggested_size":       suggested_size,
        "rr":                   rr,
        "reason":               _build_reason(direction, ema_spread, rsi, vol_surge, ret_5d, regime, entry_quality, pct_from_high, pct_from_low),
        "tags":                 tags,
        "regime":               regime,
        "rsi":                  round(rsi, 1),
        "realized_vol":         round(realized_vol * 100, 1),
        "atr":                  round(atr, 4),
        "ema_spread":           round(ema_spread * 100, 3),
        "vol_surge":            round(vol_surge, 2),
        "ret_5d":               round(ret_5d * 100, 2),
        "bb_std":               round(bb_std, 4),
        "bb_mid":               round(bb_mid, 4),
        "support":              support,
        "resistance":           resist,
        "high_20d":             round(high_20d, 4),
        "low_20d":              round(low_20d, 4),
        "pct_from_high":        pct_from_high,
        "pct_from_low":         pct_from_low,
        "data_source":          data_source,
        "timeframe":            timeframe,
        "chart": {
            "dates":    dates,
            "opens":    [round(float(v), 4) for v in opens],
            "highs":    [round(float(v), 4) for v in highs],
            "lows":     [round(float(v), 4) for v in lows],
            "closes":   [round(float(v), 4) for v in closes],
            "volumes":  [int(max(v, 0)) for v in volumes],
            "ema12":    [round(float(v), 4) for v in ema12],
            "ema26":    [round(float(v), 4) for v in ema26],
            "bb_upper": round(bb_up,  4),
            "bb_lower": round(bb_lo,  4),
            "bb_mid":   round(bb_mid, 4),
        },
    }


# ── public entry points ───────────────────────────────────────────────────────

def fetch_signals_from_bars(bars, symbol: str, name: str, tf: str = "1d") -> Optional[dict]:
    """Build signals from an ib_insync BarData list."""
    if not bars or len(bars) < 20:
        return None
    intraday = tf not in ("1d", "1w")
    try:
        closes  = np.array([float(b.close)  for b in bars], dtype=float)
        highs   = np.array([float(b.high)   for b in bars], dtype=float)
        lows    = np.array([float(b.low)    for b in bars], dtype=float)
        opens   = np.array([float(b.open)   for b in bars], dtype=float)
        volumes = np.array([max(int(getattr(b, "volume", 0)), 0) for b in bars], dtype=float)
        # Intraday: preserve time component for Plotly date axis
        if intraday:
            dates = [str(b.date)[:19].replace(" ", "T") for b in bars]
        else:
            dates = [str(b.date)[:10] for b in bars]
    except Exception as exc:
        logger.warning("[signals] ibkr bar conversion %s tf=%s: %s", symbol, tf, exc)
        return None
    bpy = _BARS_PER_YEAR.get(tf, 252)
    return _compute(closes, highs, lows, opens, volumes, dates, symbol, name,
                    data_source="ibkr", bars_per_year=bpy, timeframe=tf)


def fetch_signals(symbol: str, name: str, tf: str = "1d") -> Optional[dict]:
    """Download via yfinance and compute signals (default 1d, supports all TFs)."""
    period, interval = _TF_YF.get(tf, ("6mo", "1d"))
    try:
        tk   = yf.Ticker(symbol)
        hist = tk.history(period=period, interval=interval, auto_adjust=True)
    except Exception as exc:
        logger.warning("[signals] yf download failed %s tf=%s: %s", symbol, tf, exc)
        return None

    if hist is None or hist.empty or len(hist) < 20:
        return None

    closes  = hist["Close"].values.astype(float)
    highs   = hist["High"].values.astype(float)
    lows    = hist["Low"].values.astype(float)
    opens   = hist["Open"].values.astype(float)
    volumes = hist["Volume"].values.astype(float)

    intraday = tf not in ("1d", "1w")
    if intraday:
        try:
            dates = [ts.strftime("%Y-%m-%dT%H:%M:%S") for ts in hist.index]
        except Exception:
            dates = [str(d)[:19] for d in hist.index]
    else:
        dates = [str(d)[:10] for d in hist.index]

    bpy = _BARS_PER_YEAR.get(tf, 252)
    return _compute(closes, highs, lows, opens, volumes, dates, symbol, name,
                    data_source="yfinance", bars_per_year=bpy, timeframe=tf)


# ── trade structure ───────────────────────────────────────────────────────────

def build_trade_structure(signal: dict) -> dict:
    last              = float(signal.get("last", 0))
    atr               = float(signal.get("atr",  0))
    support           = float(signal.get("support", 0))
    resist            = float(signal.get("resistance", 0))
    direction         = signal.get("direction", "LONG")
    size_pct          = signal.get("suggested_size", 5.0)
    rr                = signal.get("rr", 2.0)
    regime            = signal.get("regime", "RANGING")
    rsi               = float(signal.get("rsi", 50))
    actionability     = signal.get("actionability_state", "MEDIUM")
    entry_quality     = signal.get("entry_quality", "acceptable")
    setup_type        = signal.get("setup_type", "trend continuation")
    trigger_text      = signal.get("trigger_text", "")
    pct_from_high     = float(signal.get("pct_from_high", -5.0))
    pct_from_low      = float(signal.get("pct_from_low",  5.0))

    if last == 0 or atr == 0:
        return {"error": "Insufficient price data for trade structure."}

    # ── entry / target levels ─────────────────────────────────────────────────
    if direction == "LONG":
        entry_lo     = round(last - atr * 0.25, 4)
        entry_hi     = round(last + atr * 0.15, 4)
        invalidation = round(max(support - atr * 0.3, last - atr * 1.5), 4)
        target1      = round(last + atr * 2.0, 4)
        target2      = round(resist + atr * 0.5, 4) if resist > last else round(last + atr * 3.5, 4)
    else:
        entry_lo     = round(last - atr * 0.15, 4)
        entry_hi     = round(last + atr * 0.25, 4)
        invalidation = round(min(resist + atr * 0.3, last + atr * 1.5), 4)
        target1      = round(last - atr * 2.0, 4)
        target2      = round(support - atr * 0.5, 4) if support < last else round(last - atr * 3.5, 4)

    # ── risk note reacts to actionability and location ────────────────────────
    if actionability == "WAIT":
        if direction == "SHORT" and pct_from_low < 1.5:
            risk_note = (
                f"Shorting into support near {_fmt(support)} — high bounce risk. "
                f"Structure is bearish but entry location is poor. {trigger_text.capitalize()}."
            )
        elif direction == "LONG" and pct_from_high > -1.5:
            risk_note = (
                f"Buying near resistance at {_fmt(resist)} — high rejection risk. "
                f"Setup exists but location is poor. {trigger_text.capitalize()}."
            )
        elif rsi < 30 and direction == "SHORT":
            risk_note = (
                f"RSI {rsi:.0f} — deeply oversold. Short structure valid but bounce risk is elevated. "
                f"{trigger_text.capitalize()}."
            )
        elif rsi > 70 and direction == "LONG":
            risk_note = (
                f"RSI {rsi:.0f} — overbought. Long structure valid but mean-reversion risk elevated. "
                f"{trigger_text.capitalize()}."
            )
        else:
            risk_note = f"Entry conditions not met — {trigger_text}. Reduce or avoid until trigger fires."
    elif entry_quality == "weak":
        risk_note = "Weak entry location — half size maximum. Use tight stop. Wait for trigger confirmation."
    elif regime == "TRENDING" and rsi > 65 and direction == "LONG":
        risk_note = "Trend extended — scale in thirds, avoid chasing further acceleration."
    elif regime == "TRENDING" and rsi < 35 and direction == "SHORT":
        risk_note = "Downtrend momentum strong — do not fade; wait for exhaustion candle to exit longs."
    elif regime == "EXTENDED" and rsi > 70:
        risk_note = "Overbought extreme — stop must be tight; mean-reversion risk dominates."
    elif regime == "EXTENDED" and rsi < 30:
        risk_note = "Oversold — high reward but further downside possible; partial size on first entry."
    elif regime == "COILING":
        risk_note = "Volatility compression — wait for confirmed breakout candle before full size."
    else:
        risk_note = "Standard regime — ATR stop, scale out 50% at T1, trail rest to T2."

    return {
        "direction":           direction,
        "entry_zone":          f"{_fmt(entry_lo)} – {_fmt(entry_hi)}",
        "invalidation":        _fmt(invalidation),
        "target1":             _fmt(target1),
        "target2":             _fmt(target2),
        "size_pct":            size_pct,
        "rr":                  rr,
        "risk_note":           risk_note,
        "trigger_text":        trigger_text,
        "actionability_state": actionability,
        "entry_quality":       entry_quality,
        "setup_type":          setup_type,
    }


# ── why now — trader-oriented bullets ────────────────────────────────────────

def build_why_now(signal: dict) -> list[dict]:
    ema_spread          = signal.get("ema_spread", 0)
    rsi                 = signal.get("rsi", 50)
    regime              = signal.get("regime", "RANGING")
    technical_conviction = signal.get("technical_conviction", 50)
    vol_surge           = signal.get("vol_surge", 1.0)
    change_pct          = signal.get("change_pct", 0)
    realized_v          = signal.get("realized_vol", 20)
    direction           = signal.get("direction", "LONG")
    expected            = signal.get("expected_5d", 1.5)
    name                = signal.get("name", signal.get("symbol", ""))
    pct_from_high       = signal.get("pct_from_high", 0)
    pct_from_low        = signal.get("pct_from_low", 0)
    ret_5d              = signal.get("ret_5d", 0)
    setup_type          = signal.get("setup_type", "trend continuation")
    actionability       = signal.get("actionability_state", "MEDIUM")
    entry_quality       = signal.get("entry_quality", "acceptable")
    support             = signal.get("support", 0)
    resistance          = signal.get("resistance", 0)
    catalyst            = signal.get("catalyst_support", "low")

    spread_dir = "above" if ema_spread > 0 else "below"
    spread_abs = abs(ema_spread)

    # ── Bullet 1: Quant / Regime — structure + location + what it means ───────
    bias_word = "Bullish" if direction == "LONG" else "Bearish"

    if actionability == "WAIT":
        if direction == "SHORT" and pct_from_low < 1.5:
            q_text = (
                f"Bearish structure on {name} — EMA(12) {spread_abs:.1f}% below EMA(26), RSI {rsi:.0f}. "
                f"However, price is only {pct_from_low:.1f}% above its 20-day low ({_fmt(support)}). "
                f"Shorting into support at this level is poor trade location — the bounce risk outweighs the setup."
            )
        elif direction == "LONG" and pct_from_high > -1.5:
            q_text = (
                f"Bullish structure on {name} — EMA(12) {spread_abs:.1f}% above EMA(26), RSI {rsi:.0f}. "
                f"However, price is within {abs(pct_from_high):.1f}% of its 20-day high ({_fmt(resistance)}). "
                f"Chasing here risks buying into resistance — wait for a pullback or confirmed breakout."
            )
        else:
            q_text = (
                f"{bias_word} structure present on {name} — EMA spread {ema_spread:+.1f}%, RSI {rsi:.0f}, "
                f"regime: {regime.lower()}. Entry conditions are not met yet — "
                f"structure is valid but location and timing argue for patience."
            )
    elif regime == "TRENDING":
        mom_word = "intact" if (direction == "LONG" and rsi > 50) or (direction == "SHORT" and rsi < 50) else "diverging"
        q_text = (
            f"{bias_word} trend on {name}: EMA(12) {spread_abs:.1f}% {spread_dir} EMA(26), "
            f"momentum {mom_word} (RSI {rsi:.0f}). "
            f"Technical conviction {technical_conviction}/100 — {entry_quality} entry location."
        )
    elif regime == "EXTENDED" and rsi > 65:
        q_text = (
            f"Overbought condition on {name}: RSI {rsi:.0f}, price {abs(pct_from_high):.1f}% from 20-day high. "
            f"{'Mean-reversion short setup — price is stretched, stop placement is critical.' if direction == 'SHORT' else 'Trend-following long near extended zone — mean-reversion risk elevated, tight stop required.'}"
        )
    elif regime == "EXTENDED" and rsi < 35:
        q_text = (
            f"Oversold washout on {name}: RSI {rsi:.0f}, price {abs(pct_from_low):.1f}% from 20-day low. "
            f"{'Bounce setup — selling pressure appears exhausted near current support.' if direction == 'LONG' else 'Bearish structure remains but price is deeply oversold — counter-squeeze risk is real.'}"
        )
    elif regime == "COILING":
        q_text = (
            f"Volatility compression on {name}: Bollinger bands contracting, EMA spread {ema_spread:+.1f}%. "
            f"RSI {rsi:.0f} — neutral ahead of directional resolution. "
            f"Directional energy is building; {direction.lower()} bias ahead of breakout."
        )
    else:
        range_pos = "upper third" if pct_from_high > -2.0 else ("lower third" if pct_from_low < 2.0 else "mid-range")
        q_text = (
            f"Range-bound {name}: EMA spread {ema_spread:+.1f}%, RSI {rsi:.0f}, "
            f"price in {range_pos} of 20-day range. "
            f"Setup is {setup_type.replace('_', ' ')} — conviction {technical_conviction}/100."
        )

    # ── Bullet 2: News / Catalyst — volume flow, directional confirmation ─────
    flow_word = "aligned with" if (change_pct > 0) == (direction == "LONG") else "diverges from"
    if catalyst == "high":
        n_text = (
            f"Exceptional volume on {name}: {vol_surge:.1f}× the 20-day average. "
            f"Session move {change_pct:+.2f}% — flow {flow_word} the directional signal. "
            f"{'This is likely institutional. Execution risk is higher on thin conditions post-spike.' if vol_surge > 3.0 else 'Institutional participation likely — confirms setup conviction.'}"
        )
    elif catalyst == "medium":
        n_text = (
            f"Elevated volume ({vol_surge:.1f}× average) with session move {change_pct:+.2f}%. "
            f"5-day return {ret_5d:+.1f}% — flow {flow_word} the signal. "
            f"Not a clear catalyst event, but activity is above baseline — worth confirming against headlines."
        )
    else:
        n_text = (
            f"Volume {vol_surge:.1f}× 20-day average — no unusual flow. "
            f"Session {change_pct:+.2f}%, 5-day return {ret_5d:+.1f}%. "
            f"This is a technically driven setup with no apparent flow catalyst. "
            f"Check headlines for undiscovered news before committing."
        )

    # ── Bullet 3: Macro / Event — vol regime and execution context ────────────
    vol_ctx = (
        f"elevated at {realized_v:.0f}% annualised — widen stops by 1×ATR and reduce size" if realized_v > 28
        else f"compressed at {realized_v:.0f}% annualised — breakout moves will be fast when they fire" if realized_v < 12
        else f"normal at {realized_v:.0f}% annualised — standard ATR stops apply"
    )
    if actionability == "WAIT":
        m_text = (
            f"Realised vol is {vol_ctx}. "
            f"Model prices ±{expected:.1f}% over the next 5 sessions. "
            f"Given the WAIT state, do not pre-position — the setup is structurally valid but entry is premature."
        )
    elif actionability == "HIGH":
        m_text = (
            f"Realised vol is {vol_ctx}. "
            f"Model prices ±{expected:.1f}% over 5 sessions. "
            f"Macro tape is {'supportive of the directional bias' if technical_conviction >= 60 else 'ambiguous — proceed with reduced size until regime firms up'}."
        )
    else:
        m_text = (
            f"Realised vol is {vol_ctx}. "
            f"Model prices ±{expected:.1f}% over the next 5 sessions. "
            f"{'Monitor macro tape for confirmation — this setup needs external catalyst to achieve full target.' if actionability == 'MEDIUM' else 'Macro context is ambiguous — reduce size to minimum until a clear catalyst emerges.'}"
        )

    return [
        {"tag": "Quant / Regime",   "text": q_text},
        {"tag": "News / Sentiment", "text": n_text},
        {"tag": "Macro / Event",    "text": m_text},
    ]


# ── reason one-liner ──────────────────────────────────────────────────────────

def _build_reason(
    direction: str,
    ema_spread: float,
    rsi: float,
    vol_surge: float,
    ret_5d: float,
    regime: str,
    entry_quality: str = "acceptable",
    pct_from_high: float = -5.0,
    pct_from_low: float = 5.0,
) -> str:
    wait_suffix = " — poor entry location, wait for trigger" if entry_quality == "wait" else ""

    if regime == "TRENDING":
        if direction == "LONG":
            if abs(ema_spread) > 0.02:
                return f"Strong trend continuation — EMA spread widening, momentum intact{wait_suffix}"
            return f"Trend continuation — bullish EMA alignment with volume backing{wait_suffix}"
        else:
            if abs(ema_spread) > 0.02:
                return f"Accelerating downtrend — EMA spread widening, seller control{wait_suffix}"
            return f"Trend continuation short — bearish EMA structure confirmed{wait_suffix}"
    elif regime == "EXTENDED":
        if rsi > 70:
            return f"Mean-reversion short — RSI {rsi:.0f} overbought, stretched above 20D bands{wait_suffix}"
        elif rsi < 30:
            return f"Mean-reversion bounce — RSI {rsi:.0f} oversold near 20D support{wait_suffix}"
        elif rsi > 60:
            return f"Extended but not overbought — trend followers still active{wait_suffix}"
        else:
            return f"Oversold counter-trend setup — bearish structure, tight stop required{wait_suffix}"
    elif regime == "COILING":
        return f"Volatility compression — directional breakout building{wait_suffix}"
    else:
        if entry_quality == "wait":
            if direction == "SHORT" and pct_from_low < 1.5:
                return "Bearish structure but at support — shorting here is poor location; wait for break"
            if direction == "LONG" and pct_from_high > -1.5:
                return "Bullish structure but at resistance — buying here risks rejection; wait for breakout"
        if abs(ret_5d) > 0.04:
            return "Range breakout attempt — momentum pushing against prior boundary"
        if rsi < 38:
            return "Range-low bounce — improving risk/reward at lower band"
        if rsi > 62:
            return "Range-high fade — mean-reversion candidate at upper band"
        return f"Range trade — EMA {ema_spread * 100:+.1f}%, RSI {rsi:.0f}, no dominant trend"
