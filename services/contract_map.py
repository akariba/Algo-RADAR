"""
Frontend symbol → IBKR Contract mapping.
No IB connection needed here — pure static lookup.

Returns (contract, what_to_show, use_rth) or None if unmapped.
"""
from __future__ import annotations

from typing import Optional

from ib_insync import Contract, Crypto, Forex, Index, Stock

# Tuple: (ib_contract, what_to_show, use_rth)
_MAP: dict[str, Optional[tuple]] = {
    # ── ETFs / Equities (SMART routing) ───────────────────────────────────────
    "SPY":      (Stock("SPY",  "SMART", "USD"),  "TRADES",    True),
    "QQQ":      (Stock("QQQ",  "SMART", "USD"),  "TRADES",    True),
    "DIA":      (Stock("DIA",  "SMART", "USD"),  "TRADES",    True),
    "IWM":      (Stock("IWM",  "SMART", "USD"),  "TRADES",    True),
    "GLD":      (Stock("GLD",  "SMART", "USD"),  "TRADES",    True),
    "SLV":      (Stock("SLV",  "SMART", "USD"),  "TRADES",    True),
    "TLT":      (Stock("TLT",  "SMART", "USD"),  "TRADES",    True),
    "XLE":      (Stock("XLE",  "SMART", "USD"),  "TRADES",    True),
    "XLF":      (Stock("XLF",  "SMART", "USD"),  "TRADES",    True),
    "XLK":      (Stock("XLK",  "SMART", "USD"),  "TRADES",    True),
    "XLV":      (Stock("XLV",  "SMART", "USD"),  "TRADES",    True),
    "AAPL":     (Stock("AAPL", "SMART", "USD"),  "TRADES",    True),
    "NVDA":     (Stock("NVDA", "SMART", "USD"),  "TRADES",    True),
    "TSLA":     (Stock("TSLA", "SMART", "USD"),  "TRADES",    True),
    "MSFT":     (Stock("MSFT", "SMART", "USD"),  "TRADES",    True),
    "XOM":      (Stock("XOM",  "SMART", "USD"),  "TRADES",    True),
    "SMH":      (Stock("SMH",  "SMART", "USD"),  "TRADES",    True),
    # ── Continuous Futures (CONTFUT) ──────────────────────────────────────────
    # Front-month continuous series — back-adjusted by IBKR.
    # Futures trade outside regular hours (useRTH=False).
    "GC=F":     (Contract(secType="CONTFUT", symbol="GC", exchange="COMEX", currency="USD"), "TRADES", False),
    "SI=F":     (Contract(secType="CONTFUT", symbol="SI", exchange="COMEX", currency="USD"), "TRADES", False),
    "CL=F":     (Contract(secType="CONTFUT", symbol="CL", exchange="NYMEX", currency="USD"), "TRADES", False),
    "BZ=F":     (Contract(secType="CONTFUT", symbol="BZ", exchange="NYMEX", currency="USD"), "TRADES", False),
    "DX-Y.NYB": (Contract(secType="CONTFUT", symbol="DX", exchange="NYBOT", currency="USD"), "TRADES", False),
    # ── Forex ─────────────────────────────────────────────────────────────────
    "EURUSD=X": (Forex("EURUSD"),                               "MIDPOINT", False),
    # ── Index ─────────────────────────────────────────────────────────────────
    # VIX index — live quote available with CBOE market data subscription.
    "^VIX":     (Index("VIX", "CBOE", "USD"),                   "TRADES",   True),
    # ── Crypto ────────────────────────────────────────────────────────────────
    # Requires IBKR Crypto market data subscription (PAXOS exchange).
    # Will return status="unsupported" if account lacks permissions.
    "BTC-USD":  (Crypto("BTC", "PAXOS", "USD"),                 "AGGTRADES", False),
    "ETH-USD":  (Crypto("ETH", "PAXOS", "USD"),                 "AGGTRADES", False),
}

# Honest notes shown in the UI when a contract is unsupported at runtime
_NOTES: dict[str, str] = {
    "BTC-USD":  "Requires IBKR Crypto market data subscription (PAXOS)",
    "ETH-USD":  "Requires IBKR Crypto market data subscription (PAXOS)",
    "^VIX":     "VIX is a CBOE index; live data requires CBOE market data subscription",
    "DX-Y.NYB": "DX continuous futures on NYBOT (ICE); requires US futures market data",
    "EURUSD=X": "EUR/USD midpoint pricing via IBKR FX",
}


def resolve(symbol: str) -> Optional[tuple]:
    """Return (contract, what_to_show, use_rth) or None if symbol not mapped."""
    return _MAP.get(symbol)


def get_note(symbol: str) -> str:
    """Human-readable note for edge-case or unsupported contracts."""
    return _NOTES.get(symbol, "")


def mapped_symbols() -> list[str]:
    return [s for s, v in _MAP.items() if v is not None]
