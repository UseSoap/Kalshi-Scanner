"""Summaries of settled paper trades: is the realized hit rate beating the price?"""

from __future__ import annotations

import math
from pathlib import Path

from .scanner import sport_of
from .storage import load_trades

# Entry-price buckets in cents. The first catches anything under 90c (only if --min-price was lowered).
BUCKETS = [(0, 90), (90, 93), (93, 95), (95, 96), (96, 97), (97, 98), (98, 99), (99, 100.01)]


def bucket_label(lo: float, hi: float) -> str:
    if lo == 0:
        return f"<{int(hi)}c"
    return f"{lo}-{int(hi) if hi < 100 else 100}c"


def bucket_of(trade: dict) -> str | None:
    price = float(trade["entry_price"])
    for lo, hi in BUCKETS:
        if lo <= price < hi:
            return bucket_label(lo, hi)
    return None


def sport_label(trade: dict) -> str:
    return trade.get("sport") or sport_of(trade.get("series", ""))


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
        if lo == 0 and not group:
            continue
        rows.append((bucket_label(lo, hi), summarize(group)))
    return rows


def by_sport(trades: list[dict]) -> list[tuple[str, dict]]:
    names = sorted({sport_label(t) for t in trades})
    return [(name, summarize([t for t in trades if sport_label(t) == name])) for name in names]


def by_sport_and_bucket(trades: list[dict]) -> list[tuple[str, list[tuple[str, dict]]]]:
    """For each sport, the summary of each non-empty price bucket."""
    out = []
    for sport in sorted({sport_label(t) for t in trades}):
        mine = [t for t in trades if sport_label(t) == sport]
        rows = [(label, s) for label, s in by_bucket(mine) if s["n"]]
        out.append((sport, rows))
    return out


def fills_table(trades: list[dict]) -> list[str]:
    """Count of paper fills per sport and entry-price bucket, whether or not they have settled."""
    labels = [bucket_label(lo, hi) for lo, hi in BUCKETS]
    if not any(bucket_of(t) == labels[0] for t in trades):
        labels = labels[1:]
    sports = sorted({sport_label(t) for t in trades})
    width = max([len("Sport")] + [len(x) for x in sports + ["Total"]]) + 2
    header = "Sport".ljust(width) + "".join(l.rjust(8) for l in labels) + "Total".rjust(8) + "avg size".rjust(10)
    lines = [header]
    for sport in sports + ["Total"]:
        group = trades if sport == "Total" else [t for t in trades if sport_label(t) == sport]
        cells = "".join(str(sum(1 for t in group if bucket_of(t) == l) or "-").rjust(8) for l in labels)
        sizes = [int(float(t["contracts"])) for t in group]
        lines.append(sport.ljust(width) + cells + str(len(group)).rjust(8) + f"{sum(sizes) / len(sizes):.0f}".rjust(10))
    return lines


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def _fmt(label: str, s: dict, width: int = 24) -> str:
    if s["n"] == 0:
        return f"{label:<{width}} n=0"
    return (f"{label:<{width}} n={s['n']:<4} hit={s['hit_rate']:.1%} "
            f"(95% CI {s['ci_low']:.1%}-{s['ci_high']:.1%})  implied={s['avg_implied']:.1%}  "
            f"pnl={_money(s['pnl_usd'])}  roi={s['roi']:.2%}")


def render(data_dir: Path | None = None) -> str:
    trades = load_trades(data_dir)
    done = settled(trades)
    lines = [f"Paper trades: {len(trades)} total, {len(done)} settled, "
             f"{sum(1 for t in trades if t['status'] == 'open')} open", ""]
    if not trades:
        lines.append("No paper fills yet. Let the scanner run through a few game windows.")
        return "\n".join(lines)
    lines.append("Fills by sport and entry price (open and settled; 'avg size' is contracts per fill):")
    lines += ["  " + row for row in fills_table(trades)]
    lines.append("")
    if not done:
        lines.append("Nothing settled yet. Hit rates appear once games finish and Kalshi settles them.")
        return "\n".join(lines)
    lines.append(_fmt("ALL SETTLED", summarize(done)))
    lines += ["", "By entry price:"]
    lines += ["  " + _fmt(label, s) for label, s in by_bucket(done)]
    lines += ["", "By sport:"]
    lines += ["  " + _fmt(label, s) for label, s in by_sport(done)]
    lines += ["", "By sport and entry price:"]
    for sport, rows in by_sport_and_bucket(done):
        lines.append(f"  {sport}")
        lines += ["    " + _fmt(label, s) for label, s in rows]
    lines += ["", "Edge exists only if hit rate beats the implied probability by more than fees AND the",
              "confidence interval stays above the break-even rate. Small samples will mislead you.",
              "Fills are sized to what the book offered, so small fills pay proportionally more in",
              "rounded-up fees; judge hit rate vs implied first and treat roi at small sizes as pessimistic."]
    return "\n".join(lines)
