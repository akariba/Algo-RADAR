"""
IBKR market data client — ib_insync 0.9.x synchronous API.

Threading model
───────────────
ib_insync's asyncio transport (TCP socket) is bound to the event loop of the
thread that called IB.connect().  Flask uses a thread pool, so naively calling
IB methods from different request threads uses *different* event loops —
the transport's data_received callbacks never fire on those loops and all
futures hang.

Fix: a single dedicated ThreadPoolExecutor(max_workers=1) ensures that
*every* IB call (connect, reqHistoricalData, reqHistoricalDataAsync, …) always
executes in the same OS thread and therefore the same asyncio event loop.
Flask request threads submit lambdas to this executor and block on the result.

util.startLoop() / patchAsyncio() applies nest_asyncio so that
run_until_complete() can be nested (needed when reqHistoricalDataAsync calls
asyncio.wait_for inside an already-running loop via asyncio.gather).

Data approach
─────────────
- Tape bulk quotes   → parallel reqHistoricalDataAsync + asyncio.gather
                       (3-day daily bars; last close = price)
- Instrument quote   → reqHistoricalData sync (3-day bars; last close)
- Chart data         → reqHistoricalData sync (90-day bars)
- Opportunities      → yfinance only (parallel, no IB lock contention)

Public API
──────────
  get_connection_status() -> dict
  get_bulk_quotes(symbols) -> list[dict]
  get_live_quote(symbol)   -> dict
  get_historical_bars(symbol) -> list[BarData] | None
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ib_insync import IB, util

from .contract_map import get_note, resolve

logger = logging.getLogger(__name__)

# Apply nest_asyncio so run_until_complete() can be nested inside the worker.
util.startLoop()

# ── config ────────────────────────────────────────────────────────────────────
IBKR_HOST      = os.environ.get("IBKR_HOST",               "127.0.0.1")
IBKR_PORT      = int(os.environ.get("IBKR_PORT",           "4001"))
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID",      "1"))
IBKR_TIMEOUT   = int(os.environ.get("IBKR_CONNECT_TIMEOUT", "6"))

# Single dedicated IB worker thread — guarantees one event loop for the
# transport lifetime.  All public functions submit work here.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ibkr")
# Outer lock: prevents two Flask threads from queuing overlapping IB jobs.
_lock = threading.Lock()

_ib: IB = IB()

_last_connect_attempt: float = 0.0
_RECONNECT_COOLDOWN = 30.0


# ── helpers (all called inside the executor thread) ───────────────────────────

def _safe_float(v) -> Optional[float]:
    """Return float or None; treats IBKR sentinel -1.0 as missing."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f) or f == -1.0:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _ensure_connected() -> bool:
    """Called inside the executor thread.  Reconnect if disconnected."""
    global _last_connect_attempt
    if _ib.isConnected():
        return True
    now = time.time()
    if now - _last_connect_attempt < _RECONNECT_COOLDOWN:
        return False
    _last_connect_attempt = now
    try:
        logger.info("[ibkr] connecting %s:%d cid=%d",
                    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID)
        _ib.connect(
            host=IBKR_HOST,
            port=IBKR_PORT,
            clientId=IBKR_CLIENT_ID,
            timeout=IBKR_TIMEOUT,
            readonly=True,
        )
        _ib.reqMarketDataType(3)   # accept delayed as fallback
        logger.info("[ibkr] connected")
        return True
    except Exception as exc:
        logger.warning("[ibkr] connection failed: %s", exc)
        return False


def _bars_to_quote(symbol: str, bars) -> dict:
    """Last bar close → normalised price dict with day-over-day change."""
    if not bars:
        return _error_quote("No historical bars returned")
    price = _safe_float(bars[-1].close) or _safe_float(bars[-1].open)
    if price is None:
        return _error_quote("No usable price in historical bar")
    change = change_pct = 0.0
    if len(bars) >= 2:
        prev = _safe_float(bars[-2].close)
        if prev and prev != 0:
            change     = round(price - prev, 6)
            change_pct = round(change / prev * 100, 4)
    return {
        "price":      round(price, 6),
        "change":     round(change, 6),
        "change_pct": round(change_pct, 2),
        "source":     "ibkr",
        "status":     "delayed",
    }


def _error_quote(msg: str) -> dict:
    return {"price": None, "change": 0.0, "change_pct": 0.0,
            "source": "ibkr", "status": "error", "error": msg}


def _unsupported_quote(symbol: str) -> dict:
    return {"price": None, "change": 0.0, "change_pct": 0.0,
            "source": "ibkr", "status": "unsupported",
            "error": get_note(symbol) or f"No IBKR contract mapping for {symbol}"}


# ── async gather (runs inside executor thread's event loop) ───────────────────

async def _gather_hist(tasks_info: list[tuple]) -> list:
    """
    Parallel reqHistoricalDataAsync for N contracts.
    tasks_info: [(sym, contract, what_to_show, use_rth), ...]
    Returns list of BarDataList | Exception in the same order.
    """
    coros = [
        _ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="3 D",
            barSizeSetting="1 day",
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=False,
            timeout=15,
        )
        for _, contract, what_to_show, use_rth in tasks_info
    ]
    return await asyncio.gather(*coros, return_exceptions=True)


# ── public API ────────────────────────────────────────────────────────────────

def get_connection_status() -> dict:
    """Non-blocking status check; attempts lazy connect inside executor."""
    with _lock:
        connected = _executor.submit(_ensure_connected).result(timeout=10)
    return {
        "connected": connected,
        "host":      IBKR_HOST,
        "port":      IBKR_PORT,
        "client_id": IBKR_CLIENT_ID,
    }


def get_bulk_quotes(symbols: list[str]) -> list[dict]:
    """
    Parallel last-session prices for all symbols via reqHistoricalDataAsync.
    Runs entirely inside the dedicated IB executor thread.
    """
    def _task() -> list[dict]:
        if not _ensure_connected():
            return [_error_quote("IBKR not connected") for _ in symbols]

        tasks_info: list[tuple] = []
        unsupported_set: set[str] = set()
        for sym in symbols:
            mapping = resolve(sym)
            if mapping is None:
                unsupported_set.add(sym)
            else:
                contract, what_to_show, use_rth = mapping
                tasks_info.append((sym, contract, what_to_show, use_rth))

        if not tasks_info:
            return [_unsupported_quote(s) for s in symbols]

        try:
            loop = asyncio.get_event_loop()
            all_bars = loop.run_until_complete(_gather_hist(tasks_info))
        except Exception as exc:
            logger.warning("[ibkr] bulk historical gather failed: %s", exc)
            return [_error_quote("Historical fetch failed") for _ in symbols]

        bars_map: dict[str, object] = {}
        for (sym, _, _, _), result in zip(tasks_info, all_bars):
            if isinstance(result, Exception):
                logger.warning("[ibkr] hist %s: %s", sym, result)
                bars_map[sym] = None
            else:
                bars_map[sym] = result
                logger.debug("[ibkr] %s: %d bars", sym,
                             len(result) if result else 0)

        results = []
        for sym in symbols:
            if sym in unsupported_set:
                results.append(_unsupported_quote(sym))
            elif not bars_map.get(sym):
                results.append(_error_quote("No historical data from IBKR"))
            else:
                results.append(_bars_to_quote(sym, bars_map[sym]))
        return results

    with _lock:
        return _executor.submit(_task).result(timeout=35)


def get_live_quote(symbol: str) -> dict:
    """Latest price for one instrument via 3-day daily bars."""
    def _task() -> dict:
        if not _ensure_connected():
            return _error_quote("IBKR not connected")
        mapping = resolve(symbol)
        if mapping is None:
            return _unsupported_quote(symbol)
        contract, what_to_show, use_rth = mapping
        try:
            bars = _ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="3 D",
                barSizeSetting="1 day",
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                timeout=15,
            )
            if not bars:
                return _error_quote("No historical data from IBKR")
            logger.debug("[ibkr] live quote %s: last=%s", symbol, bars[-1].close)
            return _bars_to_quote(symbol, bars)
        except Exception as exc:
            logger.warning("[ibkr] live quote %s: %s", symbol, exc)
            return _error_quote(str(exc))

    with _lock:
        return _executor.submit(_task).result(timeout=25)


_TF_IBKR: dict[str, tuple[str, str]] = {
    "5m":  ("5 mins",  "2 D"),
    "1h":  ("1 hour",  "20 D"),
    "4h":  ("4 hours", "30 D"),
    "1d":  ("1 day",   "180 D"),
    "1w":  ("1 week",  "2 Y"),
}


def get_historical_bars(symbol: str) -> Optional[list]:
    """90-day daily bars for chart data (legacy, uses 1d default)."""
    return get_historical_bars_tf(symbol, "1d")


def get_historical_bars_tf(symbol: str, tf: str = "1d") -> Optional[list]:
    """
    Historical OHLCV bars for the given timeframe.
    tf: "5m" | "1h" | "4h" | "1d" | "1w"
    Returns ib_insync BarData list, or None on failure / unsupported symbol.
    """
    bar_size, duration = _TF_IBKR.get(tf, _TF_IBKR["1d"])

    def _task() -> Optional[list]:
        if not _ensure_connected():
            return None
        mapping = resolve(symbol)
        if mapping is None:
            return None
        contract, what_to_show, use_rth = mapping
        # Intraday bars require useRTH=True for clean data
        effective_rth = True if tf in ("5m", "1h", "4h") else use_rth
        try:
            bars = _ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=effective_rth,
                formatDate=1,
                timeout=35,
            )
            if not bars:
                logger.warning("[ibkr] no bars returned for %s tf=%s", symbol, tf)
                return None
            logger.info("[ibkr] %d bars for %s tf=%s", len(bars), symbol, tf)
            return bars
        except Exception as exc:
            logger.warning("[ibkr] historical %s tf=%s: %s", symbol, tf, exc)
            return None

    with _lock:
        return _executor.submit(_task).result(timeout=45)
