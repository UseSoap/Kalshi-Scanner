"""Summaries of settled paper trades: is the realized hit rate beating the price?"""

from __future__ import annotations

import math
from pathlib import Path

from .storage import load_trades

BUCKETS = [(95, 96), (96, 97), (97, 98), (98, 99), (99, 100.01)]


def wilson_interval(wins: int, n: int, z: float = 1.96):
    """95% confidence interval for a hit rate. Wide intervals mean 'not enough data yet'."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def settled(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t["status"] == "settled"]


def summarize(trades: list[dict]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["won"] == "1")
    staked = sum(float(t["entry_price"]) * int(float(t["contracts"])) / 100.0 + float(t["fee_usd"]) for t in trades)
    pnl = sum(float(t["pnl_usd"]) for t in trades)
    implied = (sum(float(t["entry_price"]) for t in trades) / n / 100.0) if n else 0.0
    lo, hi = wilson_interval(wins, n)
    return {
        "n": n, "wins": wins, "hit_rate": wins / n if n else 0.0,
        "ci_low": lo, "ci_high": hi, "avg_implied": implied,
        "pnl_usd": pnl, "roi": pnl / staked if staked else 0.0,
    }


def by_bucket(trades: list[dict]) -> list[tuple[str, dict]]:
    rows = []
    for lo, hi in BUCKETS:
        group = [t for t in trades if lo <= float(t["entry_price"]) < hi]
        label = f"{lo}-{int(hi) if hi < 100 else 100}c"
        rows.append((label, summarize(group)))
    return rows


def by_series(trades: list[dict]) -> list[tuple[str, dict]]:
    names = sorted({t["series"] for t in trades})
    return [(name, summarize([t for t in trades if t["series"] == name])) for name in names]


def _fmt(label: str, s: dict) -> str:
    if s["n"] == 0:
        return f"{label:<12} n=0"
    return (f"{label:<12} n={s['n']:<4} hit={s['hit_rate']:.1%} "
            f"(95% CI {s['ci_low']:.1%}-{s['ci_high']:.1%})  implied={s['avg_implied']:.1%}  "
            f"pnl=${s['pnl_usd']:,.2f}  roi={s['roi']:.2%}")


def render(data_dir: Path | None = None) -> str:
    trades = load_trades(data_dir)
    done = settled(trades)
    lines = [f"Paper trades: {len(trades)} total, {len(done)} settled, "
             f"{sum(1 for t in trades if t['status'] == 'open')} open", ""]
    if not done:
        lines.append("Nothing settled yet. Let the scanner run for a few days.")
        return "\n".join(lines)
    lines.append(_fmt("ALL", summarize(done)))
    lines += ["", "By entry price:"]
    lines += ["  " + _fmt(label, s) for label, s in by_bucket(done)]
    lines += ["", "By series:"]
    lines += ["  " + _fmt(label, s) for label, s in by_series(done)]
    lines += ["", "Edge exists only if hit rate beats the implied probability by more than fees AND the",
              "confidence interval stays above the break-even rate. Small samples will mislead you."]
    return "\n".join(lines)
