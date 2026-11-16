"""Build the board: every Kalshi game-winner contract next to Pinnacle's devigged fair price."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from kalshi_scanner.scanner import market_expiry, parse_ts, price_cents

from .config import SPORTS, Settings
from .devig import american, conservative_fair

OPEN = {"open", "active"}


# ---------------------------------------------------------------- name matching
ABBREV = {"la": ["los", "angeles"], "ny": ["new", "york"], "nj": ["new", "jersey"], "st": ["st"],
          "okc": ["oklahoma", "city"], "gs": ["golden", "state"], "sa": ["san", "antonio"]}


def _tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
    out = []
    for t in re.findall(r"[a-z0-9]+", s):
        out.extend(ABBREV.get(t, [t]))
    return out


def name_score(kalshi_name: str, book_name: str) -> float:
    kt, bt = _tokens(kalshi_name), _tokens(book_name)
    if not kt or not bt:
        return 0.0
    # Kalshi tokens are prefixes of sportsbook tokens, in order, each used once:
    # "Los Angeles L" ~ "Los Angeles Lakers" but not "Los Angeles Clippers"
    i = 0
    for k in kt:
        while i < len(bt) and not bt[i].startswith(k):
            i += 1
        if i == len(bt):
            break
        i += 1
    else:
        return 1.0
    return difflib.SequenceMatcher(None, " ".join(kt), " ".join(bt)).ratio()


def team_name(market: dict) -> str:
    name = (market.get("yes_sub_title") or "").strip()
    if name:
        return name
    title = market.get("title") or ""
    m = re.match(r"(?:Will )?(.+?) (?:wins?|to win)\b", title, re.I)
    return m.group(1) if m else title


def match_game(markets: list[dict], book_events: list[dict], min_score: float = 0.75):
    """Pair a two-contract Kalshi game with a sportsbook event.

    Returns (event, {ticker: sportsbook team name}) or (None, None). Names must match on both teams,
    and the Kalshi game must be expected to end 0-10 hours after the sportsbook start time, so the same
    matchup on another date is never paired.
    """
    expiry = market_expiry(markets[0])
    n0, n1 = team_name(markets[0]), team_name(markets[1])
    best, best_score, best_map = None, 0.0, None
    for ev in book_events:
        start = parse_ts(ev.get("commence_time"))
        if expiry and start and not (start <= expiry <= start + timedelta(hours=10)):
            continue
        h, a = ev["home_team"], ev["away_team"]
        s1 = name_score(n0, h) + name_score(n1, a)
        s2 = name_score(n0, a) + name_score(n1, h)
        score, mapping = ((s1, {markets[0]["ticker"]: h, markets[1]["ticker"]: a}) if s1 >= s2 else
                          (s2, {markets[0]["ticker"]: a, markets[1]["ticker"]: h}))
        score /= 2
        if score > best_score:
            best, best_score, best_map = ev, score, mapping
    if best_score < min_score:
        return None, None
    return best, best_map


# ---------------------------------------------------------------- fair prices
def sharp_fair(event: dict, books: list[str], now: datetime, max_age: float):
    """{team: (fair_prob, book, decimal, age_min, methods)} averaged over usable sharp books, plus oldest age seen."""
    per_team = defaultdict(list)
    oldest = None
    for bk in event.get("bookmakers", []):
        if bk.get("key") not in books:
            continue
        mk = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
        if not mk or len(mk.get("outcomes", [])) != 2:
            continue
        updated = parse_ts(mk.get("last_update") or bk.get("last_update"))
        age = (now - updated).total_seconds() / 60 if updated else 9999.0
        oldest = age if oldest is None else max(oldest, age)
        if age > max_age:
            continue
        dec = [float(o["price"]) for o in mk["outcomes"]]
        fair, methods = conservative_fair(dec)
        for i, o in enumerate(mk["outcomes"]):
            per_team[o["name"]].append((fair[i], bk["key"], dec[i], age, {k: v[i] for k, v in methods.items()}))
    out = {}
    for team, rows in per_team.items():
        out[team] = {
            "fair": sum(r[0] for r in rows) / len(rows),
            "books": "+".join(r[1] for r in rows),
            "decimal": rows[0][2],
            "age": max(r[3] for r in rows),
            "methods": rows[0][4],
        }
    return out, oldest


# ---------------------------------------------------------------- board
def ev_after_fee(fair: float, ask_c: float, fee_rate: float) -> float:
    p = ask_c / 100.0
    cost = p + fee_rate * p * (1 - p)
    return fair / cost - 1


def build_board(client, fetch_odds, cfg: Settings, now: datetime | None = None):
    """Return (rows, stats, unmatched, credits_remaining).

    rows: one dict per Kalshi team contract in a matched game (with fair price and EV when available).
    fetch_odds(sport_key) -> (events, credits_remaining).
    """
    now = now or datetime.now(timezone.utc)
    rows, stats, unmatched, credits = [], {}, [], None
    for alias in cfg.sports:
        if alias not in SPORTS:
            stats[alias] = {"error": "unknown sport"}
            continue
        odds_key, series, label = SPORTS[alias]
        events, credits = fetch_odds(odds_key)
        pregame = [e for e in events if (parse_ts(e.get("commence_time")) or now) > now]

        games = defaultdict(list)
        for m in client.list_markets(series_ticker=series, status="open"):
            if str(m.get("status", "open")).lower() in OPEN and m.get("event_ticker"):
                games[m["event_ticker"]].append(m)
        st = {"pinnacle_games": len(events), "pinnacle_pregame": len(pregame), "kalshi_games": len(games),
              "matched": 0, "stale": 0, "plus_ev": 0}
        stats[alias] = st

        for event_ticker, markets in sorted(games.items()):
            if len(markets) != 2:
                continue
            ev, mapping = match_game(markets, pregame)
            if not ev:
                unmatched.append(f"{label}: {markets[0].get('title') or event_ticker} [{event_ticker}]")
                continue
            st["matched"] += 1
            fair_by_team, oldest = sharp_fair(ev, cfg.sharp_books, now, cfg.max_line_age)
            if not fair_by_team and oldest is not None:
                st["stale"] += 1
            for m in markets:
                team = mapping[m["ticker"]]
                ask = price_cents(m, "yes_ask")
                if ask is None:
                    nb = price_cents(m, "no_bid")
                    ask = None if nb is None else round(100 - nb, 4)
                if ask is not None and not (0 < ask < 100):
                    ask = None
                f = fair_by_team.get(team)
                row = {
                    "sport": label, "alias": alias, "event_ticker": event_ticker, "ticker": m["ticker"],
                    "game": f"{ev['away_team']} @ {ev['home_team']}", "team": team,
                    "start_utc": ev["commence_time"], "kalshi_ask_c": ask,
                    "fair_prob": round(f["fair"], 4) if f else None,
                    "fair_american": american(f["fair"]) if f else "",
                    "sharp_books": f["books"] if f else "", "sharp_decimal": f["decimal"] if f else None,
                    "line_age_min": round(f["age"], 1) if f else (round(oldest, 1) if oldest is not None else None),
                    "ev_pct": None, "status": "",
                }
                for k, v in (f["methods"] if f else {}).items():
                    row[f"fair_{k}"] = round(v, 4)
                if not f:
                    row["status"] = "no Pinnacle line" if oldest is None else "stale line"
                elif ask is None:
                    row["status"] = "no Kalshi ask"
                else:
                    ev_ = ev_after_fee(f["fair"], ask, cfg.fee_rate)
                    row["ev_pct"] = round(ev_ * 100, 2)
                    row["status"] = "+EV" if ev_ >= cfg.min_ev else ("small edge" if ev_ > 0 else "no edge")
                    if ev_ >= cfg.min_ev:
                        st["plus_ev"] += 1
                rows.append(row)
    return rows, stats, unmatched, credits
