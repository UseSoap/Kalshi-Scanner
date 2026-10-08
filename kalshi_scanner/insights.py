"""Extra statistics for the paper-trade report.

Break-even and edge, win/loss size, daily P&L and drawdown, how much data is enough, fill quality,
entry timing, open exposure and the scan funnel. Everything here is a pure function over rows as
read from CSV: every value is a string and older rows may have blanks, so each helper skips what it
cannot read instead of failing. Formatting lives in report.py.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .scanner import parse_ts

REPORT_TZ = "America/Chicago"


def num(value, default=None):
    """Read a CSV cell as a float; blanks and junk give `default`."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def wilson_interval(wins: float, n: int, z: float = 1.96):
    """95% confidence interval for a hit rate. Wide intervals mean 'not enough data yet'."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# --- break-even and verdict ---------------------------------------------------------------

def cost_of(trade: dict):
    """Dollars risked on a trade (stake plus fee), or None if the row is unreadable."""
    price, size = num(trade.get("entry_price")), num(trade.get("contracts"))
    if price is None or size is None:
        return None
    return price * size / 100.0 + num(trade.get("fee_usd"), 0.0)


def edge_verdict(hit: float, lo: float, hi: float, breakeven: float) -> str:
    """One plain-English line comparing the hit-rate interval with the break-even rate."""
    if lo > breakeven:
        return (f"EDGE SO FAR - the whole 95% interval ({lo:.1%}-{hi:.1%}) is above the "
                f"{breakeven:.1%} break-even. Keep collecting data to be sure.")
    if hi < breakeven:
        return (f"NO EDGE - the whole 95% interval ({lo:.1%}-{hi:.1%}) is below the "
                f"{breakeven:.1%} break-even.")
    side = "above" if hit >= breakeven else "below"
    return (f"INCONCLUSIVE - hit rate {hit:.1%} is {side} the {breakeven:.1%} break-even, but the "
            f"95% interval ({lo:.1%}-{hi:.1%}) spans it.")


# --- win/loss size, daily results, drawdown -----------------------------------------------

def win_loss_stats(trades: list[dict]):
    wins, losses = [], []
    for t in trades:
        pnl = num(t.get("pnl_usd"))
        if pnl is None:
            continue
        (wins if t.get("won") == "1" else losses).append(pnl)
    if not wins and not losses:
        return None
    gross_win, gross_loss = sum(wins), -sum(losses)
    avg_win = gross_win / len(wins) if wins else None
    avg_loss = gross_loss / len(losses) if losses else None
    return {
        "wins": len(wins), "losses": len(losses),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "largest_loss": -min(losses) if losses else None,
        "gross_win": gross_win, "gross_loss": gross_loss,
        "wins_per_loss": avg_loss / avg_win if avg_win and avg_loss else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
    }


def _local_day(trade: dict, tz: ZoneInfo):
    """The game day (US Central) a trade belongs to: its listed end, else settlement, else entry."""
    for key in ("expiry", "settled_at", "first_seen"):
        ts = parse_ts(trade.get(key))
        if ts:
            return ts.astimezone(tz).date().isoformat()
    return None


def daily_results(trades: list[dict], tz_name: str = REPORT_TZ) -> list[dict]:
    tz = ZoneInfo(tz_name)
    days: dict[str, list[tuple[bool, float]]] = defaultdict(list)
    for t in trades:
        pnl, day = num(t.get("pnl_usd")), _local_day(t, tz)
        if pnl is not None and day:
            days[day].append((t.get("won") == "1", pnl))
    rows, cumulative = [], 0.0
    for day in sorted(days):
        items = days[day]
        pnl = sum(p for _, p in items)
        cumulative += pnl
        rows.append({"day": day, "n": len(items), "wins": sum(1 for w, _ in items if w),
                     "pnl": pnl, "cumulative": cumulative})
    return rows


def _settled_at(trade: dict) -> datetime:
    ts = parse_ts(trade.get("settled_at")) or parse_ts(trade.get("expiry")) or parse_ts(trade.get("first_seen"))
    return ts or datetime.min.replace(tzinfo=timezone.utc)


def equity_stats(trades: list[dict]):
    """Cumulative P&L in settlement order, with peak-to-trough drawdown and the current streak."""
    cumulative = peak = max_dd = 0.0
    streak_kind, streak_len, count = None, 0, 0
    for t in sorted(trades, key=_settled_at):
        pnl = num(t.get("pnl_usd"))
        if pnl is None:
            continue
        count += 1
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
        kind = "win" if t.get("won") == "1" else "loss"
        streak_kind, streak_len = (kind, streak_len + 1) if kind == streak_kind else (kind, 1)
    if not count:
        return None
    return {"cumulative": cumulative, "peak": peak, "max_drawdown": max_dd,
            "drawdown": peak - cumulative, "streak_kind": streak_kind, "streak_len": streak_len}


# --- how much data is enough --------------------------------------------------------------

def trades_to_separate(hit: float, breakeven: float, max_n: int = 50_000):
    """Fewest trades at which the 95% interval around `hit` excludes `breakeven` (None if never)."""
    if abs(hit - breakeven) < 1e-9:
        return None
    for n in range(1, max_n + 1):
        lo, hi = wilson_interval(hit * n, n)
        if (hit > breakeven and lo > breakeven) or (hit < breakeven and hi < breakeven):
            return n
    return None


def trades_per_day(trades: list[dict]):
    stamps = [ts for ts in (parse_ts(t.get("first_seen")) for t in trades) if ts]
    if len(stamps) < 2:
        return None
    span_days = (max(stamps) - min(stamps)).total_seconds() / 86400.0
    return len(stamps) / max(span_days, 1.0)


# --- groupings used for extra tables ------------------------------------------------------

SIDE_ORDER = ["Bought YES", "Bought NO"]
SPREAD_ORDER = ["1c or less", "over 1 to 2c", "over 2c"]
TIMING_ORDER = ["after listed end", "0-30 min before", "30-90 min before", "90+ min before"]


def side_label(trade: dict):
    side = (trade.get("side") or "").lower()
    return {"yes": SIDE_ORDER[0], "no": SIDE_ORDER[1]}.get(side)


def spread_label(trade: dict):
    spread = num(trade.get("spread"))
    if spread is None:
        return None
    return SPREAD_ORDER[0] if spread <= 1.0 else SPREAD_ORDER[1] if spread <= 2.0 else SPREAD_ORDER[2]


def timing_label(trade: dict):
    """Entry time relative to the game's listed end (the market's expected expiration)."""
    seen, end = parse_ts(trade.get("first_seen")), parse_ts(trade.get("expiry"))
    if not seen or not end:
        return None
    minutes = (end - seen).total_seconds() / 60.0
    if minutes < 0:
        return TIMING_ORDER[0]
    return TIMING_ORDER[1] if minutes < 30 else TIMING_ORDER[2] if minutes < 90 else TIMING_ORDER[3]


# --- fill quality -------------------------------------------------------------------------

def fill_quality(trades: list[dict]):
    """Fill size, fees, spread, and how each entry price compares with the quote that flagged it.

    `best_ask` is the market-list quote at scan time. `entry_price` is the average fill price from
    the order book, read a moment later. So the gap (entry minus quote) is not pure slippage: it can
    go either way, because prices move between the two reads as well as with book depth. A negative
    gap means the paper fill was cheaper than the quote, which flatters the paper results.
    """
    above, below, equal = [], [], 0
    gap_usd, sizes, spreads = 0.0, [], []
    fees = gross_stake = total_contracts = 0.0
    for t in trades:
        price, ask, size = num(t.get("entry_price")), num(t.get("best_ask")), num(t.get("contracts"))
        if size is None:
            continue
        sizes.append(size)
        total_contracts += size
        fees += num(t.get("fee_usd"), 0.0)
        if price is not None:
            gross_stake += price * size / 100.0
            if ask is not None:
                gap = price - ask
                gap_usd += gap * size / 100.0
                if gap > 1e-9:
                    above.append(gap)
                elif gap < -1e-9:
                    below.append(gap)
                else:
                    equal += 1
        spread = num(t.get("spread"))
        if spread is not None:
            spreads.append(spread)
    if not sizes:
        return None
    biggest = max(sizes)
    return {
        "n": len(sizes), "with_quote": len(above) + len(below) + equal,
        "above": len(above), "below": len(below), "equal": equal,
        "avg_above_c": sum(above) / len(above) if above else None,
        "avg_below_c": sum(below) / len(below) if below else None,
        "worst_below_c": min(below) if below else None,
        "vs_quote_usd": gap_usd,   # negative = fills were cheaper than the quotes, in total
        "partial": sum(1 for s in sizes if s < biggest), "biggest": biggest,
        "fees": fees, "fee_pct_of_stake": fees / gross_stake if gross_stake else None,
        "fee_lift_pts": fees / total_contracts * 100.0 if total_contracts else None,
        "avg_spread_c": sum(spreads) / len(spreads) if spreads else None,
    }


# --- open positions, scan funnel, freshness -----------------------------------------------

def open_exposure(trades: list[dict]):
    open_trades = [t for t in trades if t.get("status") == "open"]
    at_risk = max_profit = 0.0
    upcoming = []
    for t in open_trades:
        cost, price, size = cost_of(t), num(t.get("entry_price")), num(t.get("contracts"))
        if cost is None or price is None or size is None:
            continue
        at_risk += cost
        max_profit += size * (100.0 - price) / 100.0 - num(t.get("fee_usd"), 0.0)
        upcoming.append({"title": t.get("title") or t.get("ticker") or "?", "side": t.get("side") or "",
                         "price": price, "contracts": size, "expiry": parse_ts(t.get("expiry"))})
    upcoming.sort(key=lambda u: u["expiry"] or datetime.max.replace(tzinfo=timezone.utc))
    return {"count": len(open_trades), "at_risk": at_risk, "max_profit": max_profit, "upcoming": upcoming}


_DEPTH_RANK = {"ok": 3, "unchecked": 3, "thin": 2}   # anything else (unknown) ranks 1


def games_funnel(games: list[dict], snapshots: list[dict], trades: list[dict], tz_name: str = REPORT_TZ):
    """Per game day: games observed, games that reached the entry floor, were fillable, and were traded.

    Counts games, not contracts: the two sides of a game are the same bet. `games` is data/games.csv, which
    records every game seen, including the ones that never reached the floor. Days before that log began
    only know about games that did reach it (from the snapshots), so their "observed" count is None.
    """
    tz = ZoneInfo(tz_name)

    def blank(day=None, logged=False, peak=None):
        return {"day": day, "logged": logged, "rank": 0, "traded": False, "peak": peak}

    def day_of(row):
        ts = parse_ts(row.get("expiry"))
        return ts.astimezone(tz).date().isoformat() if ts else None

    per: dict[str, dict] = {g["event_ticker"]: blank(g.get("day") or None, True, num(g.get("peak_ask"))) for g in games}
    for r in snapshots:
        if r.get("event_ticker"):
            g = per.setdefault(r["event_ticker"], blank())
            g["day"] = g["day"] or day_of(r)
            g["rank"] = max(g["rank"], _DEPTH_RANK.get(r.get("depth_status") or "unchecked", 1))
    for t in trades:
        if t.get("event_ticker"):
            g = per.setdefault(t["event_ticker"], blank())
            g["day"] = g["day"] or day_of(t)
            g["traded"] = True
            g["rank"] = max(g["rank"], 3)          # a paper trade means the contract was fillable

    days: dict[str, dict] = defaultdict(lambda: {"logged": 0, "hit": 0, "fillable": 0, "traded": 0})
    for g in per.values():
        if not g["day"]:
            continue
        d = days[g["day"]]
        d["logged"] += g["logged"]
        d["hit"] += g["rank"] > 0
        d["fillable"] += g["rank"] == 3
        d["traded"] += g["traded"]
    if not days:
        return None
    rows = []
    for day in sorted(days):
        d = days[day]
        unlogged_hits = sum(1 for g in per.values() if g["day"] == day and not g["logged"] and g["rank"] > 0)
        rows.append({"day": day, "observed": d["logged"] + unlogged_hits if d["logged"] else None,
                     "hit": d["hit"], "fillable": d["fillable"], "traded": d["traded"]})
    misses = [g["peak"] for g in per.values() if g["logged"] and g["rank"] == 0 and g["peak"] is not None]
    logged_days = [r["day"] for r in rows if r["observed"] is not None]
    return {"days": rows, "first_logged_day": logged_days[0] if logged_days else None,
            "never_hit": len(misses), "avg_peak": sum(misses) / len(misses) if misses else None,
            "max_peak": max(misses) if misses else None}


def mirror_savings(trades: list[dict]):
    """How the cheaper-side rule has played out: how often the other side was available, and what it saved."""
    compared = better = below_floor = 0
    gaps, saved_usd = [], 0.0
    for t in trades:
        if t.get("via_mirror") == "1":
            below_floor += 1
        entry, partner, size = num(t.get("entry_price")), num(t.get("partner_fill")), num(t.get("contracts"))
        if entry is None or partner is None or size is None:
            continue
        compared += 1
        if partner - entry > 1e-9:
            better += 1
            gaps.append(partner - entry)
            saved_usd += (partner - entry) * size / 100.0
    if not compared and not below_floor:
        return None
    return {"compared": compared, "cheaper": better, "avg_saving_c": sum(gaps) / len(gaps) if gaps else None,
            "saved_usd": saved_usd, "below_floor": below_floor}


def latest(rows: list[dict], field: str):
    stamps = [ts for ts in (parse_ts(r.get(field)) for r in rows) if ts]
    return max(stamps) if stamps else None


def describe_when(when: datetime, now: datetime, tz_name: str = REPORT_TZ) -> str:
    """'Oct 08 12:13 AM CDT (4h ago)'."""
    local = when.astimezone(ZoneInfo(tz_name)).strftime("%b %d %I:%M %p %Z")
    minutes = max(0, int((now - when).total_seconds() // 60))
    if minutes < 60:
        age = f"{minutes}m ago"
    elif minutes < 48 * 60:
        age = f"{minutes // 60}h ago"
    else:
        age = f"{minutes // 1440}d ago"
    return f"{local} ({age})"
