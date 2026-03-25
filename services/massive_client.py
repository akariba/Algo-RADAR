"""
Massive market data API client.

Base URL  : https://api.massive.com   (override via MASSIVE_BASE_URL)
Auth      : Authorization: Bearer {MASSIVE_API_KEY}
Fallback  : returns {"error": "..."} dict — caller decides what to do

Endpoint patterns tried in order for quote():
  GET /v1/quote?ticker={symbol}
  GET /v1/market/quote?ticker={symbol}
  GET /v1/quotes/{symbol}
  GET /quote?symbol={symbol}
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


class MassiveClient:
    def __init__(self) -> None:
        self.base_url = os.environ.get(
            "MASSIVE_BASE_URL", "https://api.massive.com"
        ).rstrip("/")
        self.api_key = os.environ.get("MASSIVE_API_KEY", "")
        self.timeout = 8
        self._session = requests.Session()
        if self.api_key:
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                    "User-Agent": "radar-v1/1.0",
                }
            )

    # ── internal ────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f"{self.base_url}{path}"
        logger.info("[massive] GET %s params=%s", url, params)
        try:
            resp = self._session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            logger.info("[massive] OK %s", url)
            return data
        except requests.HTTPError as exc:
            body = exc.response.text[:300] if exc.response is not None else ""
            status = exc.response.status_code if exc.response is not None else 0
            logger.warning("[massive] HTTP %s %s — %s", status, url, body)
            return {"error": f"HTTP {status}", "detail": body}
        except requests.ConnectionError as exc:
            logger.warning("[massive] connection error %s — %s", url, exc)
            return {"error": "connection_error", "detail": str(exc)}
        except requests.Timeout:
            logger.warning("[massive] timeout %s", url)
            return {"error": "timeout"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[massive] unexpected %s — %s", url, exc)
            return {"error": str(exc)}

    # ── public ──────────────────────────────────────────────────────────────

    def quote(self, ticker: str) -> dict:
        """
        Fetch live quote for one ticker.
        Returns normalised dict or {"error": "..."}.
        """
        if not self.api_key:
            return {"error": "no_api_key"}

        attempts = [
            ("/v1/quote",        {"ticker": ticker}),
            ("/v1/market/quote", {"ticker": ticker}),
            (f"/v1/quotes/{ticker}", None),
            ("/quote",           {"symbol": ticker}),
        ]
        for path, params in attempts:
            result = self._get(path, params)
            if "error" not in result:
                return result

        logger.warning("[massive] all endpoints failed for %s", ticker)
        return {"error": "all_endpoints_failed", "ticker": ticker}

    def bulk_quotes(self, tickers: list[str]) -> list[dict]:
        """
        Fetch quotes for multiple tickers in one call.
        Returns list or [] on failure.
        """
        if not self.api_key:
            return []
        result = self._get("/v1/quotes", {"tickers": ",".join(tickers)})
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "error" not in result:
            return result.get("data") or result.get("quotes") or []
        return []
