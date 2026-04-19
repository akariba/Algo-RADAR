"""
Cross-Asset Opportunity Radar — Flask entry point

Routes:
  GET /                         → main page
  GET /api/health               → service status (IBKR connection state)
  GET /api/market/tape          → live tape (14 symbols via IBKR)
  GET /api/instrument?ticker=X  → full signal + chart + news (IBKR bars)
  GET /api/opportunities        → ranked top-12 opportunities (yfinance signals)

Data source priority:
  tape quotes        → IBKR only (live or delayed depending on subscription)
  instrument chart   → IBKR historical bars first, yfinance fallback
  instrument quote   → IBKR live quote overlaid on chart data
  opportunities      → yfinance (parallel, no IBKR lock contention)
  headlines          → yfinance news
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Fix for Python 3.10+ — create event loop before ib_insync import
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

import services.ibkr_client as ibkr
from services.backtest import run_backtest, run_wfo
from services.contract_map import mapped_symbols
from services.signals import (
    build_trade_structure,
    build_why_now,
    fetch_signals,
    fetch_signals_from_bars,
    fetch_signals_from_bars as _fetch_bars,
)

_VALID_TF = {"5m", "1h", "4h", "1d", "1w"}

# ── app setup ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"]                  = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"]     = os.environ.get("DATABASE_URL", "sqlite:///sybil_dev.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["AWS_REGION"]                  = os.environ.get("AWS_REGION", "eu-central-1")
app.config["SES_SENDER_EMAIL"]            = os.environ.get("SES_SENDER_EMAIL", "noreply@sybilradar.com")
app.config["APP_BASE_URL"]                = os.environ.get("APP_BASE_URL", "http://localhost:5055")
app.config["EMAIL_TOKEN_EXPIRY"]          = int(os.environ.get("EMAIL_TOKEN_EXPIRY", 86400))
CORS(app)

from auth.models import db
from auth.routes import auth_bp, limiter
db.init_app(app)
limiter.init_app(app)
app.register_blueprint(auth_bp)
with app.app_context():
    db.create_all()

# ── universe ──────────────────────────────────────────────────────────────────

_UNIVERSE: list[dict] = json.loads(
    (Path(__file__).parent / "data" / "universe.json").read_text()
)
_UNIVERSE_MAP: dict[str, dict] = {inst["symbol"]: inst for inst in _UNIVERSE}


def _display(symbol: str) -> str:
    """Return clean display ticker (e.g. 'CL' for 'CL=F', 'BTC' for 'BTC-USD')."""
    return _UNIVERSE_MAP.get(symbol, {}).get("display", symbol)

TAPE_SYMBOLS = [
    "SPY", "QQQ", "DIA", "IWM",
    "GLD", "SLV",
    "GC=F", "SI=F",
    "CL=F", "BZ=F",
    "BTC-USD", "ETH-USD",
    "DX-Y.NYB", "^VIX",
]

# ── in-memory TTL cache ───────────────────────────────────────────────────────

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str, ttl: int):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < ttl:
            return entry["data"]
    return None


def _cache_set(key: str, data):
    with _cache_lock:
        _cache[key] = {"ts": time.time(), "data": data}


# ── news helpers ──────────────────────────────────────────────────────────────

def _score_sentiment(text: str) -> float:
    pos = ["surge", "rally", "gain", "rise", "beats", "strong", "record", "bull",
           "jump", "soar", "outperform", "upgrade", "buy", "positive", "growth"]
    neg = ["fall", "drop", "decline", "loss", "cut", "miss", "weak", "bear",
           "crash", "risk", "warn", "downgrade", "sell", "negative", "concern",
           "slump", "plunge", "tumble", "disappoints"]
    t = text.lower()
    score = sum(0.3 for w in pos if w in t) - sum(0.3 for w in neg if w in t)
    return round(max(-1.0, min(1.0, score)), 2)


def _sentiment_label(score: float) -> str:
    if score >= 0.25:
        return "bullish"
    if score <= -0.25:
        return "bearish"
    return ""


def _generate_news_bullets(title: str, risk_tag: str, label: str, name: str) -> list[str]:
    """Rule-based 2-bullet expansion for headline inline detail."""
    tag_context = {
        "policy":       "Central bank or fiscal policy development — may shift rate expectations or liquidity conditions.",
        "earnings":     "Corporate earnings event — check revenue and guidance revisions for sector sentiment impact.",
        "geo-risk":     "Geopolitical development — assess supply disruption risk and risk-off sentiment pressure.",
        "supply-chain": "Supply or production disruption — monitor downstream pricing and margin compression.",
        "regulatory":   "Regulatory or compliance action — headline risk may trigger sector-wide repricing.",
        "corporate":    "Corporate action (M&A or restructuring) — evaluate deal probability and spread implications.",
        "market":       "Market structure or macro observation — verify directional relevance before acting.",
    }
    b1 = tag_context.get(risk_tag, "General market headline — confirm relevance to current setup.")
    if label == "bullish":
        b2 = f"Positive tone — may provide near-term upside catalyst for {name}. Confirm alignment with current technical setup before adding size."
    elif label == "bearish":
        b2 = f"Negative tone — monitor for follow-through selling in {name}. Consider whether this headline invalidates the current entry thesis."
    else:
        b2 = f"Neutral or ambiguous tone — provides context but does not directly confirm the current bias for {name}."
    return [b1, b2]


def _tag_news(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ["fed", "rate", "inflation", "gdp", "fomc", "treasury",
                              "yield", "central bank", "powell", "monetary", "fiscal"]):
        return "policy"
    if any(w in t for w in ["earnings", "eps", "revenue", "beats", "misses",
                              "guidance", "quarter", "profit", "margin"]):
        return "earnings"
    if any(w in t for w in ["war", "conflict", "military", "sanction", "opec",
                              "geopolit", "attack", "crisis", "strike", "tension"]):
        return "geo-risk"
    if any(w in t for w in ["supply", "shortage", "shipment", "port", "logistics",
                              "production", "capacity", "inventory", "disruption"]):
        return "supply-chain"
    if any(w in t for w in ["sec", "regulation", "lawsuit", "fine", "investigation",
                              "regulatory", "compliance", "probe"]):
        return "regulatory"
    if any(w in t for w in ["merger", "acquisition", "deal", "buyout", "ipo",
                              "spinoff", "split"]):
        return "corporate"
    return "market"


import requests as _requests

# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/api/ai/research", methods=["POST"])
def ai_research():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    context  = (data.get("context")  or "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Perplexity API key not configured"}), 503

    system_prompt = (
        "You are a concise, institutional-grade market research assistant embedded in "
        "a cross-asset trading radar platform. Answer in 3-5 sentences maximum. "
        "Focus on actionable market insights, macro context, and risk factors. "
        "Avoid disclaimers. Be direct and data-driven."
    )
    if context:
        system_prompt += f"\n\nCurrent instrument context: {context}"

    try:
        resp = _requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": question},
                ],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=20,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"]
        return jsonify({"answer": answer})
    except Exception as exc:
        logger.warning("[ai] perplexity error: %s", exc)
        return jsonify({"error": str(exc)}), 502


_STATS_FILE = Path(__file__).parent / "data" / "stats.json"
_stats_lock = threading.Lock()

def _load_stats() -> dict:
    try:
        if _STATS_FILE.exists():
            return json.loads(_STATS_FILE.read_text())
    except Exception:
        pass
    return {"visits": 0}

def _inc_visits():
    with _stats_lock:
        s = _load_stats()
        s["visits"] = s.get("visits", 0) + 1
        try:
            _STATS_FILE.write_text(json.dumps(s))
        except Exception:
            pass

@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/app")
def index():
    threading.Thread(target=_inc_visits, daemon=True).start()
    return render_template("index.html")

@app.route("/api/stats")
def stats():
    return jsonify(_load_stats())


@app.route("/lab/3d")
def lab_3d():
    return render_template("lab_3d.html")


@app.route("/research/backtest")
def research_backtest():
    return render_template("backtest.html")


@app.route("/api/backtest")
def api_backtest():
    params = {
        "strategy":   request.args.get("strategy",   "regime_adaptive_trend"),
        "ticker":     request.args.get("ticker",      "SPY").upper(),
        "benchmark":  request.args.get("benchmark",   "SPY").upper(),
        "start":      request.args.get("start",       ""),
        "end":        request.args.get("end",         ""),
        "fast":       int(request.args.get("fast",    12)),
        "slow":       int(request.args.get("slow",    26)),
        "stop_atr":   float(request.args.get("stop_atr",   2.0)),
        "target_atr": float(request.args.get("target_atr", 4.0)),
        "vol_filter": request.args.get("vol_filter",  "true").lower() == "true",
        "is_pct":     float(request.args.get("is_pct", 0.70)),
    }
    try:
        result = run_backtest(params)
    except Exception as exc:
        logger.exception("[backtest] error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify(result)


@app.route("/api/backtest/wfo")
def api_backtest_wfo():
    """Walk-Forward Optimisation endpoint — returns per-fold metrics + stitched OOS equity."""
    params = {
        "strategy":   request.args.get("strategy",   "regime_adaptive_trend"),
        "ticker":     request.args.get("ticker",      "SPY").upper(),
        "start":      request.args.get("start",       ""),
        "end":        request.args.get("end",         ""),
        "fast":       int(request.args.get("fast",    12)),
        "slow":       int(request.args.get("slow",    26)),
        "stop_atr":   float(request.args.get("stop_atr",   2.0)),
        "target_atr": float(request.args.get("target_atr", 4.0)),
        "vol_filter": request.args.get("vol_filter",  "true").lower() == "true",
        "n_folds":    int(request.args.get("n_folds",   5)),
        "oos_ratio":  float(request.args.get("oos_ratio", 0.30)),
        "wfo_mode":   request.args.get("wfo_mode",    "rolling"),
    }
    try:
        result = run_wfo(params)
    except Exception as exc:
        logger.exception("[wfo] error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify(result)


@app.route("/api/health")
def health():
    status = ibkr.get_connection_status()
    return jsonify({
        "status":            "ok" if status["connected"] else "degraded",
        "ibkr_connected":    status["connected"],
        "ibkr_host":         status["host"],
        "ibkr_port":         status["port"],
        "ibkr_client_id":    status["client_id"],
        "universe_size":     len(_UNIVERSE),
        "supported_symbols": mapped_symbols(),
        "app_port":          int(os.environ.get("APP_PORT", "5055")),
    })


@app.route("/api/market/tape")
def tape():
    cached = _cache_get("tape", ttl=30)
    if cached:
        return jsonify(cached)

    logger.info("[tape] fetching %d symbols via IBKR", len(TAPE_SYMBOLS))
    quotes = ibkr.get_bulk_quotes(TAPE_SYMBOLS)

    results = []
    for sym, q in zip(TAPE_SYMBOLS, quotes):
        inst = _UNIVERSE_MAP.get(sym, {"symbol": sym, "name": sym})
        # ── yfinance fallback for symbols IBKR can't serve (e.g. PAXOS crypto) ──
        if q.get("status") == "error" or q.get("price") is None:
            try:
                import yfinance as yf
                tk   = yf.Ticker(sym)
                hist = tk.history(period="5d", interval="1d", auto_adjust=True)
                if hist is not None and len(hist) >= 2:
                    p0   = float(hist["Close"].iloc[-2])
                    p1   = float(hist["Close"].iloc[-1])
                    chg  = p1 - p0
                    pct  = chg / p0 if p0 else 0.0
                    q = {"price": round(p1, 4), "change": round(chg, 4),
                         "change_pct": round(pct, 6), "source": "yfinance", "status": "ok"}
            except Exception:
                pass
        results.append({
            "symbol":     inst.get("display", sym),
            "yf_symbol":  sym,
            "name":       inst.get("name", sym),
            "price":      q.get("price"),
            "change":     q.get("change", 0.0),
            "change_pct": q.get("change_pct", 0.0),
            "source":     q.get("source", "ibkr"),
            "status":     q.get("status", "error"),
            "error":      q.get("error", ""),
        })

    logger.info("[tape] done — %d items", len(results))
    if results:
        _cache_set("tape", results)
    return jsonify(results)


@app.route("/api/instrument")
def instrument():
    symbol = request.args.get("ticker", "SPY").upper()
    tf     = request.args.get("tf", "1d").lower()
    if tf not in _VALID_TF:
        tf = "1d"

    # Resolve display aliases (e.g. "DXY" → "DX-Y.NYB")
    inst = _UNIVERSE_MAP.get(symbol)
    if not inst:
        for k, v in _UNIVERSE_MAP.items():
            if v.get("display", "").upper() == symbol:
                inst   = v
                symbol = k
                break
    if not inst:
        inst = {"symbol": symbol, "name": symbol}

    cached = _cache_get(f"instrument:{symbol}:{tf}", ttl=300)
    if cached:
        return jsonify(cached)

    name = inst.get("name", symbol)

    # ── 1. Try IBKR historical bars ──────────────────────────────────────────
    bars = ibkr.get_historical_bars_tf(symbol, tf)
    sig  = fetch_signals_from_bars(bars, symbol, name, tf) if bars else None

    # ── 2. Fallback to yfinance ──────────────────────────────────────────────
    if sig is None:
        logger.info("[instrument] IBKR bars unavailable for %s tf=%s — falling back to yfinance", symbol, tf)
        sig = fetch_signals(symbol, name, tf)

    if sig is None:
        return jsonify({
            "error": f"No data available for {name} ({symbol}) on {tf}"
        }), 404

    # Overwrite symbol in signal with clean display code
    sig["symbol"]     = _display(symbol)
    sig["yf_symbol"]  = symbol

    # ── 3. Overlay IBKR live quote ───────────────────────────────────────────
    live = ibkr.get_live_quote(symbol)
    if live.get("price"):
        sig["last"]       = live["price"]
        sig["change"]     = live["change"]
        sig["change_pct"] = live["change_pct"]
        sig["market_data_status"] = live["status"]        # "live" | "delayed"
    else:
        sig["market_data_status"] = live.get("status", "error")   # "error" | "unsupported"
        if live.get("error"):
            sig["market_data_note"] = live["error"]

    sig["why_now"]         = build_why_now(sig)
    sig["trade_structure"] = build_trade_structure(sig)

    # ── 4. Headlines via yfinance ────────────────────────────────────────────
    try:
        import yfinance as yf
        from datetime import datetime
        raw_news   = yf.Ticker(symbol).news or []
        news_items = []
        for n in raw_news[:10]:
            # yfinance ≥ 0.2.x nests everything inside n["content"]
            content = n.get("content", n)
            title   = content.get("title", "") or n.get("title", "")
            if not title:
                continue
            # URL: prefer clickThroughUrl → canonicalUrl → legacy "link"
            url = ""
            for url_key in ("clickThroughUrl", "canonicalUrl"):
                obj = content.get(url_key)
                if isinstance(obj, dict):
                    url = obj.get("url", "")
                elif isinstance(obj, str):
                    url = obj
                if url:
                    break
            if not url:
                url = content.get("link", "") or n.get("link", "")
            if not url:
                continue          # skip items without a clickable URL
            # publisher
            provider = content.get("provider")
            if isinstance(provider, dict):
                publisher = provider.get("displayName", "")
            else:
                publisher = content.get("publisher", "") or n.get("publisher", "")
            # timestamp — new format: ISO string; old format: unix int
            pub_raw = content.get("pubDate") or n.get("providerPublishTime", 0)
            try:
                if isinstance(pub_raw, str) and pub_raw:
                    published = int(datetime.fromisoformat(
                        pub_raw.replace("Z", "+00:00")).timestamp())
                else:
                    published = int(pub_raw or 0)
            except Exception:
                published = 0
            score    = _score_sentiment(title)
            label    = _sentiment_label(score)
            risk_tag = _tag_news(title)
            bullets  = _generate_news_bullets(title, risk_tag, label, name)
            news_items.append({
                "title":     title,
                "publisher": publisher,
                "link":      url,
                "published": published,
                "sentiment": score,
                "label":     label,
                "risk_tag":  risk_tag,
                "bullets":   bullets,
            })
        sig["news"] = news_items
    except Exception:
        sig["news"] = []

    _cache_set(f"instrument:{symbol}", sig)
    return jsonify(sig)


@app.route("/api/opportunities")
def opportunities():
    cached = _cache_get("opportunities", ttl=300)
    if cached:
        return jsonify(cached)

    tradeable = [
        inst for inst in _UNIVERSE
        if inst["symbol"] not in ("DX-Y.NYB", "^VIX")
    ]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_signals, inst["symbol"], inst["name"]): inst
            for inst in tradeable
        }
        for fut in as_completed(futures):
            try:
                sig = fut.result(timeout=20)
            except Exception:
                continue
            if sig and "error" not in sig:
                results.append(sig)

    results.sort(key=lambda x: x.get("tradeability_score", 0), reverse=True)

    out = [
        {
            "symbol":               _display(r["symbol"]),
            "yf_symbol":            r["symbol"],
            "name":                 r["name"],
            "class":                _UNIVERSE_MAP.get(r["symbol"], {}).get("class", "—"),
            "direction":            r["direction"],
            "bias":                 r.get("bias", r["direction"]),
            "conviction":           r.get("technical_conviction", r.get("conviction", 0)),
            "technical_conviction": r.get("technical_conviction", r.get("conviction", 0)),
            "tradeability_score":   r.get("tradeability_score", 0),
            "actionability_state":  r.get("actionability_state", "MEDIUM"),
            "entry_quality":        r.get("entry_quality", "acceptable"),
            "setup_type":           r.get("setup_type", "trend continuation"),
            "trigger_text":         r.get("trigger_text", ""),
            "expected_5d":          r["expected_5d"],
            "suggested_size":       r["suggested_size"],
            "rr":                   r["rr"],
            "reason":               r["reason"],
            "tags":                 r["tags"],
            "change_pct":           r["change_pct"],
            "last":                 r["last"],
            "realized_vol":         r.get("realized_vol", 15),
            "data_source":          r.get("data_source", "yfinance"),
        }
        for r in results[:12]
    ]

    if out:
        _cache_set("opportunities", out)
    return jsonify(out)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port    = int(os.environ.get("PORT", os.environ.get("APP_PORT", "5055")))
    status  = ibkr.get_connection_status()
    ib_ok   = "CONNECTED ✓" if status["connected"] else "DISCONNECTED — page loads, check TWS"
    print(f"""
╔══════════════════════════════════════════════════╗
║     Cross-Asset Opportunity Radar  v2            ║
╠══════════════════════════════════════════════════╣
║  http://localhost:{port:<5}                          ║
║  IBKR  {status['host']}:{status['port']}  cid={status['client_id']}              ║
║  IBKR : {ib_ok:<40}║
╚══════════════════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=port, debug=False)
