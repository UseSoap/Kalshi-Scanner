"""Sharp-book moneylines from The Odds API (https://the-odds-api.com)."""

from __future__ import annotations

import requests

BASE = "https://api.the-odds-api.com/v4"


class OddsApiError(RuntimeError):
    pass


def fetch_moneylines(sport_key: str, api_key: str, books: list[str], session=None) -> tuple[list[dict], int | None]:
    """Return (events, credits_remaining). One call costs 1 credit per market per region."""
    http = session or requests
    try:
        r = http.get(f"{BASE}/sports/{sport_key}/odds", timeout=20, params={
            "apiKey": api_key, "bookmakers": ",".join(books), "markets": "h2h",
            "oddsFormat": "decimal", "dateFormat": "iso"})
    except requests.RequestException as exc:
        raise OddsApiError(f"Odds API request failed: {exc}") from exc
    if r.status_code == 401:
        raise OddsApiError("Odds API rejected the key (HTTP 401). Check the ODDS_API_KEY secret.")
    if r.status_code == 429 or r.status_code == 402:
        raise OddsApiError(f"Odds API quota/rate limit hit (HTTP {r.status_code}).")
    if r.status_code != 200:
        raise OddsApiError(f"Odds API HTTP {r.status_code}: {r.text[:200]}")
    remaining = r.headers.get("x-requests-remaining")
    try:
        remaining = int(float(remaining)) if remaining is not None else None
    except ValueError:
        remaining = None
    return r.json(), remaining
