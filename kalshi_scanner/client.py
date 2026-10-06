"""Minimal client for Kalshi's public (unauthenticated) market-data endpoints."""

from __future__ import annotations

import time
from typing import Iterator

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiError(RuntimeError):
    pass


class KalshiClient:
    def __init__(self, base_url: str = BASE_URL, session: requests.Session | None = None,
                 timeout: float = 20.0, pause: float = 0.12, max_retries: int = 4):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.pause = pause
        self.max_retries = max_retries

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout,
                                        headers={"Accept": "application/json"})
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise KalshiError(f"GET {path} failed: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    raise KalshiError(f"GET {path} -> HTTP {resp.status_code}")
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code != 200:
                raise KalshiError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
            time.sleep(self.pause)
            return resp.json()
        raise KalshiError(f"GET {path} exhausted retries")

    def list_markets(self, **params) -> Iterator[dict]:
        """Yield every market matching `params`, following pagination cursors."""
        params = {"limit": 1000, **params}
        while True:
            data = self._get("/markets", params)
            for market in data.get("markets", []):
                yield market
            cursor = data.get("cursor")
            if not cursor:
                return
            params["cursor"] = cursor

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}")["market"]

    def list_series(self, category: str = "Sports") -> list[dict]:
        return self._get("/series", {"category": category}).get("series", [])
