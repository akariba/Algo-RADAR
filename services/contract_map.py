"""
Frontend symbol → IBKR Contract mapping.
No IB connection needed here — pure static lookup.

Returns (contract, what_to_show, use_rth) or None if unmapped.
"""
from __future__ import annotations

from typing import Optional

try:
    from ib_insync import Contract, Crypto, Forex, Index, Stock
    def _stock(sym):  return Stock(sym,  "SMART", "USD")
    def _contfut(sym, exch): return Contract(secType="CONTFUT", symbol=sym, exchange=exch, currency="USD")
    _IB_OK = True
except ImportError:
    _IB_OK = False

# Tuple: (ib_contract, what_to_show, use_rth)
# When ib_insync is unavailable all entries resolve to None → yfinance fallback
if _IB_OK:
    _MAP: dict[str, Optional[tuple]] = {
        "SPY":      (_stock("SPY"),  "TRADES", True),
        "QQQ":      (_stock("QQQ"),  "TRADES", True),
        "DIA":      (_stock("DIA"),  "TRADES", True),
        "IWM":      (_stock("IWM"),  "TRADES", True),
        "GLD":      (_stock("GLD"),  "TRADES", True),
        "SLV":      (_stock("SLV"),  "TRADES", True),
        "TLT":      (_stock("TLT"),  "TRADES", True),
        "XLE":      (_stock("XLE"),  "TRADES", True),
        "XLF":      (_stock("XLF"),  "TRADES", True),
        "XLK":      (_stock("XLK"),  "TRADES", True),
        "XLV":      (_stock("XLV"),  "TRADES", True),
        "AAPL":     (_stock("AAPL"), "TRADES", True),
        "NVDA":     (_stock("NVDA"), "TRADES", True),
        "TSLA":     (_stock("TSLA"), "TRADES", True),
        "MSFT":     (_stock("MSFT"), "TRADES", True),
        "XOM":      (_stock("XOM"),  "TRADES", True),
        "SMH":      (_stock("SMH"),  "TRADES", True),
        "GC=F":     (_contfut("GC", "COMEX"), "TRADES", False),
        "SI=F":     (_contfut("SI", "COMEX"), "TRADES", False),
        "CL=F":     (_contfut("CL", "NYMEX"), "TRADES", False),
        "BZ=F":     (_contfut("BZ", "NYMEX"), "TRADES", False),
        "DX-Y.NYB": (_contfut("DX", "NYBOT"), "TRADES", False),
        "EURUSD=X": (Forex("EURUSD"),                       "MIDPOINT",  False),
        "^VIX":     (Index("VIX", "CBOE", "USD"),           "TRADES",    True),
        "BTC-USD":  (Crypto("BTC", "PAXOS", "USD"),         "AGGTRADES", False),
        "ETH-USD":  (Crypto("ETH", "PAXOS", "USD"),         "AGGTRADES", False),
    }
else:
    # No ib_insync — all symbols fall back to yfinance
    _MAP: dict[str, Optional[tuple]] = {}

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
