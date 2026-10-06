"""Simulate stacking same-day favorites into a synthetic parlay near even odds.

This is a *synthetic* parlay: the combined price is the product of the single-leg
entry prices. Kalshi's real combo markets include market-maker margin, so real
prices will be worse than this simulation. Treat results as an upper bound.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .scanner import parse_ts
from .storage import load_trades


def build_combo(legs: list[dict], target_prob: float = 0.5, tolerance: float = 0.1):
    """Greedily add the highest-priced legs, one per event, until combined implied prob <= target.

    Returns the chosen legs, or None if the day cannot reach target_prob within tolerance.
    """
    chosen, seen_events, combined = [], set(), 1.0
    for leg in sorted(legs, key=lambda t: -float(t["entry_price"])):
        if leg["event_ticker"] in seen_events:
            continue
        p = float(leg["entry_price"]) / 100.0
        chosen.append(leg)
        seen_events.add(leg["event_ticker"])
        combined *= p
        if combined <= target_prob:
            break
    if combined > target_prob or combined < target_prob - tolerance:
        return None
    return chosen


def simulate(trades: list[dict], target_prob: float = 0.5, tz_name: str = "America/Chicago") -> list[dict]:
    """One combo per day (by local expiry date), using settled legs only."""
    tz = ZoneInfo(tz_name)
    days: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t["status"] != "settled":
            continue
        expiry = parse_ts(t["expiry"])
        days[expiry.astimezone(tz).date().isoformat()].append(t)

    results = []
    for day, legs in sorted(days.items()):
        combo = build_combo(legs, target_prob)
        if combo is None:
            continue
        prob = 1.0
        for leg in combo:
            prob *= float(leg["entry_price"]) / 100.0
        won = all(leg["won"] == "1" for leg in combo)
        # Per $1 staked: a win returns 1/prob - 1, a loss costs the whole $1. No fees modeled.
        results.append({"day": day, "legs": len(combo), "implied_prob": prob,
                        "won": won, "pnl_per_dollar": (1 / prob - 1) if won else -1.0})
    return results


def render(data_dir: Path | None = None, target_prob: float = 0.5) -> str:
    results = simulate(load_trades(data_dir), target_prob)
    if not results:
        return "Combo simulation: no day has enough settled legs yet."
    n = len(results)
    wins = sum(r["won"] for r in results)
    avg_implied = sum(r["implied_prob"] for r in results) / n
    pnl = sum(r["pnl_per_dollar"] for r in results)
    lines = [f"Combo simulation (target ~{target_prob:.0%}, one combo/day, no fees, no combo margin):",
             f"  days={n}  hit={wins}/{n} ({wins / n:.1%})  avg implied={avg_implied:.1%}  "
             f"pnl per $1/day = {pnl:+.2f}", ""]
    for r in results[-10:]:
        lines.append(f"  {r['day']}  legs={r['legs']:<3} implied={r['implied_prob']:.1%}  "
                     f"{'WON ' if r['won'] else 'LOST'}  {r['pnl_per_dollar']:+.2f}")
    return "\n".join(lines)
