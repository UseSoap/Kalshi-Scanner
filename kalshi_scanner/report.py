"""Summaries of settled paper trades: is the realized hit rate beating the price?"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .insights import (
    REPORT_TZ, SIDE_ORDER, SPREAD_ORDER, TIMING_ORDER, daily_results, describe_when, edge_verdict,
    equity_stats, fill_quality, games_funnel, latest, mirror_savings, open_exposure, side_label, spread_label,
    timing_label, trades_per_day, trades_to_separate, win_loss_stats, wilson_interval,  # noqa: F401
)
from .scanner import sport_of
from .storage import load_games, load_snapshots, load_trades

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


def settled(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t["status"] == "settled"]


def summarize(trades: list[dict]) -> dict:
    """Hit rate, interval, P&L and ROI for a group of trades.

    `breakeven` is the hit rate at which the group's expected P&L is zero after fees: the money at
    risk (stake plus fee) divided by the contracts bought, since each contract pays $1 if it wins.
    `whit` is the hit rate weighted by contracts (winning contracts / contracts bought), which is what dollars
    follow: a win on a thin fill counts for less than a loss on a full one. `edge` is `whit` minus break-even,
    so it agrees with P&L. The plain `hit_rate` and its interval still count every trade once.
    """
    n = len(trades)
    wins = sum(1 for t in trades if t["won"] == "1")
    staked = sum(float(t["entry_price"]) * int(float(t["contracts"])) / 100.0 + float(t["fee_usd"]) for t in trades)
    contracts = sum(int(float(t["contracts"])) for t in trades)
    won_contracts = sum(int(float(t["contracts"])) for t in trades if t["won"] == "1")
    pnl = sum(float(t["pnl_usd"]) for t in trades)
    implied = (sum(float(t["entry_price"]) for t in trades) / n / 100.0) if n else 0.0
    lo, hi = wilson_interval(wins, n)
    breakeven = staked / contracts if contracts else 0.0
    whit = won_contracts / contracts if contracts else 0.0
    return {
        "n": n, "wins": wins, "hit_rate": wins / n if n else 0.0, "whit": whit,
        "ci_low": lo, "ci_high": hi, "avg_implied": implied,
        "breakeven": breakeven, "edge": (whit - breakeven) if n else 0.0,
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


def group_rows(trades: list[dict], key_fn, order: list[str]) -> list[tuple[str, dict]]:
    """Summaries for each non-empty group, in a fixed order. Trades `key_fn` can't place are left out."""
    rows = []
    for label in order:
        group = [t for t in trades if key_fn(t) == label]
        if group:
            rows.append((label, summarize(group)))
    return rows


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


# --- formatting ---------------------------------------------------------------------------
# Negative numbers print in parentheses, and positives get a trailing space so digits line up.

def _money(x: float) -> str:
    x = round(x, 2) + 0.0          # collapses -0.001 to 0.0 so it never prints as "$-0.00"
    return f"(${abs(x):,.2f})" if x < 0 else f"${x:,.2f}"


def _pct(x: float) -> str:
    x = round(x, 4) + 0.0
    return f"({abs(x):.2%})" if x < 0 else f"{x:.2%}"


def _pts(x: float) -> str:
    """Percentage points, one decimal."""
    x = round(x, 1) + 0.0
    return f"({abs(x):.1f})" if x < 0 else f"{x:.1f}"


def _col(text: str) -> str:
    return text if text.endswith(")") else text + " "


LABEL_W = 24
BANNER_W = 100


def banner(title: str, width: int = BANNER_W) -> list[str]:
    """A section header: a blank line, a rule, the title in capitals, a rule."""
    return ["", "=" * width, title.upper(), "=" * width]


def table_header(width: int = LABEL_W) -> str:
    return " " * width + (f" {'n':>4} {'W-L':>7} {'hit':>6} {'wtd hit':>8} {'95% CI':>12} {'implied':>8} {'b/e':>6}"
                          f" {'edge':>6} {'P&L':>11} {'ROI':>9}")


def table_row(label: str, s: dict, width: int = LABEL_W) -> str:
    if s["n"] == 0:
        return f"{label:<{width}} {0:>4}"
    record = f"{s['wins']}-{s['n'] - s['wins']}"
    interval = f"{s['ci_low'] * 100:.1f}-{s['ci_high'] * 100:.1f}%"
    return (f"{label:<{width}} {s['n']:>4} {record:>7} {s['hit_rate']:>6.1%} {s['whit']:>8.1%} {interval:>12} "
            f"{s['avg_implied']:>8.1%} {s['breakeven']:>6.1%} {_col(_pts(s['edge'] * 100)):>6} "
            f"{_col(_money(s['pnl_usd'])):>11} {_col(_pct(s['roi'])):>9}")


def _table(rows: list[tuple[str, dict]], indent: str = "  ", width: int = LABEL_W) -> list[str]:
    return [indent + table_header(width)] + [indent + table_row(label, s, width) for label, s in rows]


# --- sections -----------------------------------------------------------------------------

def _freshness_lines(trades: list[dict], snapshots: list[dict], now: datetime) -> list[str]:
    items = [("Last paper trade opened", latest(trades, "first_seen")),
             ("Last settlement", latest(trades, "settled_at")),
             ("Last candidate logged", latest(snapshots, "ts"))]
    lines = [f"  {name + ':':<26}{describe_when(ts, now)}" for name, ts in items if ts]
    return ["Data freshness (nothing is logged when no games are on):"] + lines if lines else []


def _open_lines(trades: list[dict]) -> list[str]:
    ex = open_exposure(trades)
    if not ex["count"]:
        return ["Open positions: none", ""]
    tz = ZoneInfo(REPORT_TZ)
    lines = [f"Open positions: {ex['count']} trades, {_money(ex['at_risk'])} at risk, "
             f"{_money(ex['max_profit'])} profit if all win. Next to resolve (listed game end):"]
    for u in ex["upcoming"][:5]:
        when = u["expiry"].astimezone(tz).strftime("%b %d %I:%M %p %Z") if u["expiry"] else "unknown"
        lines.append(f"  {when:<22} {u['title'][:34]:<34} {u['side'].upper():<3} {u['price']:.1f}c x{int(u['contracts'])}")
    if len(ex["upcoming"]) > 5:
        lines.append(f"  ... and {len(ex['upcoming']) - 5} more")
    return lines + [""]


def _win_loss_lines(done: list[dict]) -> list[str]:
    w = win_loss_stats(done)
    if not w:
        return []
    avg_win = _money(w["avg_win"]) if w["avg_win"] is not None else "n/a"
    avg_loss = _money(-w["avg_loss"]) if w["avg_loss"] is not None else "n/a"
    largest = _money(-w["largest_loss"]) if w["largest_loss"] is not None else "n/a"
    lines = ["Win/loss size:",
             f"  Record {w['wins']}W-{w['losses']}L   avg win {avg_win}   avg loss {avg_loss}   largest loss {largest}"]
    if w["wins_per_loss"]:
        lines.append(f"  One average loss wipes out {w['wins_per_loss']:.1f} average wins, so the hit rate has to be "
                     f"very high to make money")
    if w["profit_factor"] is not None:
        lines.append(f"  Profit factor {w['profit_factor']:.2f} (gross wins {_money(w['gross_win'])} / gross losses "
                     f"{_money(-w['gross_loss'])}); above 1.00 means profitable")
    elif not w["losses"]:
        lines.append("  No losses yet.")
    return lines + [""]


def _daily_lines(done: list[dict]) -> list[str]:
    rows = daily_results(done)
    if not rows:
        return []
    lines = ["Results by game day (US Central, most recent 10):",
             f"  {'Date':<12}{'Trades':>7}{'W-L':>8}{'P&L':>13}{'Cumulative':>15}"]
    for r in rows[-10:]:
        lines.append(f"  {r['day']:<12}{r['n']:>7}{str(r['wins']) + '-' + str(r['n'] - r['wins']):>8}"
                     f"{_col(_money(r['pnl'])):>13}{_col(_money(r['cumulative'])):>15}")
    best, worst = max(rows, key=lambda r: r["pnl"]), min(rows, key=lambda r: r["pnl"])
    lines.append(f"  Best day {best['day']} {_money(best['pnl'])}; worst day {worst['day']} {_money(worst['pnl'])}")
    eq = equity_stats(done)
    if eq:
        now_dd = "at its high-water mark" if eq["drawdown"] < 0.005 else f"{_money(eq['drawdown'])} below its peak"
        lines.append(f"  Max drawdown {_money(-eq['max_drawdown'])} (biggest drop from a peak in cumulative P&L); "
                     f"currently {now_dd}")
        n = eq["streak_len"]
        lines.append(f"  Current streak: {n} {eq['streak_kind']}{'' if n == 1 else 's'} in a row")
    return lines + [""]


def _sample_lines(s: dict, trades: list[dict]) -> list[str]:
    be, hit, rate = s["breakeven"], s["hit_rate"], trades_per_day(trades)

    def pace(n: int) -> str:
        return f", about {max(1, round(n / rate))} days at {rate:.0f} trades/day" if rate else ""

    def count(n):
        return "more than 50,000" if n is None else f"about {n:,}"

    lines = [f"How much data is enough (the 95% interval has to clear the {be:.1%} break-even):"]
    for pts in (1, 2, 3):
        if be + pts / 100.0 > 1.0:
            break
        n = trades_to_separate(be + pts / 100.0, be)
        lines.append(f"  If the true edge is +{pts} pt{'s' if pts > 1 else ''}: {count(n)} trades"
                     f"{pace(n) if n else ''}")
    n_obs = trades_to_separate(hit, be) if s["n"] else None
    if n_obs is not None:
        side = "above" if hit > be else "below"
        more = max(0, n_obs - s["n"])
        if more:
            lines.append(f"  If the observed {hit:.1%} holds, it would sit clearly {side} break-even at about "
                         f"{n_obs:,} trades")
            lines.append(f"  ({more:,} more{pace(more)})")
        else:
            lines.append(f"  The observed {hit:.1%} already sits clearly {side} break-even at this sample size")
    return lines + [""]


def _fill_lines(trades: list[dict], done: list[dict]) -> list[str]:
    q = fill_quality(trades)
    if not q:
        return []
    lines = ["Fill quality (all paper fills):"]
    if q["with_quote"]:
        parts = []
        if q["above"]:
            parts.append(f"{q['above']} above (avg {q['avg_above_c']:.2f}c worse)")
        parts.append(f"{q['equal']} at the quote")
        if q["below"]:
            parts.append(f"{q['below']} below (avg {abs(q['avg_below_c']):.2f}c better, best {abs(q['worst_below_c']):.0f}c)")
        lines.append("  Entry price vs the quoted ask that flagged each trade:")
        lines.append("    " + ", ".join(parts))
        settled_q = fill_quality(done)
        if settled_q and settled_q["with_quote"] and abs(settled_q["vs_quote_usd"]) >= 0.005:
            cheaper = settled_q["vs_quote_usd"] < 0
            at_quote = sum(float(t["pnl_usd"]) for t in done) + settled_q["vs_quote_usd"]
            lines.append(f"  Settled fills were {_money(abs(settled_q['vs_quote_usd']))} {'cheaper' if cheaper else 'dearer'} "
                         f"than the quotes in total; at the quoted prices P&L would be about {_money(at_quote)}")
        lines.append("  (The quote and the order book are read moments apart, so gaps reflect fast-moving prices as well as")
        lines.append("  book depth. Fills well below the quote flatter the paper results; a real order may not get them.)")
    m = mirror_savings(trades)
    if m:
        saving = (f"; the cheaper side saved {m['avg_saving_c']:.2f}c on average ({_money(m['saved_usd'])} in total)"
                  if m["cheaper"] else "")
        lines.append(f"  Cheaper-side rule: {m['cheaper']} of {m['compared']} trades with a priced alternative went to the "
                     f"cheaper side{saving}")
        if m["below_floor"]:
            lines.append(f"  {m['below_floor']} trade(s) entered below the entry floor because the other side of the game reached it")
    lines.append(f"  Partial fills: {q['partial']} of {q['n']} filled fewer than {q['biggest']:.0f} contracts")
    if q["fee_pct_of_stake"] is not None:
        lines.append(f"  Fees: {_money(q['fees'])} total ({q['fee_pct_of_stake']:.2%} of money staked); "
                     f"they lift the break-even hit rate by {q['fee_lift_pts']:.1f} pts")
    if q["avg_spread_c"] is not None:
        lines.append(f"  Average bid/ask spread at entry: {q['avg_spread_c']:.1f}c")
    return lines + [""]


def _funnel_lines(games: list[dict], snapshots: list[dict], trades: list[dict]) -> list[str]:
    f = games_funnel(games, snapshots, trades)
    if not f:
        return []
    lines = ["Scan funnel by game day (games, not contracts: the two sides of a game are one bet):",
             f"  {'Date':<12}{'Observed':>10}{'Hit floor':>11}{'Fillable':>10}{'Traded':>8}"]
    for d in f["days"][-10:]:
        seen = "-" if d["observed"] is None else str(d["observed"])
        lines.append(f"  {d['day']:<12}{seen:>10}{d['hit']:>11}{d['fillable']:>10}{d['traded']:>8}")
    if f["first_logged_day"]:
        lines.append(f"  Observed = games on that day's schedule that the scanner saw. It counts games that never reached")
        lines.append(f"  the entry floor from {f['first_logged_day']} on; earlier days only know about games that did ('-').")
    else:
        lines.append("  Games that never reach the entry floor start being counted with the next scan.")
    if f["never_hit"]:
        lines.append(f"  The {f['never_hit']} observed games that never reached the floor peaked at an average of "
                     f"{f['avg_peak']:.1f}c (highest {f['max_peak']:.0f}c)")
    return lines + [""]


def render(data_dir: Path | None = None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    trades = load_trades(data_dir)
    snapshots = load_snapshots(data_dir)
    games = load_games(data_dir)
    done = settled(trades)
    lines = banner("1. Status")[1:]          # no leading blank line at the top of the report
    lines.append(f"Paper trades: {len(trades)} total, {len(done)} settled, "
                 f"{sum(1 for t in trades if t['status'] == 'open')} open")
    lines += _freshness_lines(trades, snapshots, now)
    lines.append("")
    if not trades:
        lines.append("No paper fills yet. Let the scanner run through a few game windows.")
        lines += banner("4. Execution quality") + _funnel_lines(games, snapshots, trades)
        return "\n".join(lines).rstrip()
    lines += _open_lines(trades)
    if not done:
        lines += banner("3. Breakdowns")
        lines.append("Fills by sport and entry price (open and settled; 'avg size' is contracts per fill):")
        lines += ["  " + row for row in fills_table(trades)]
        lines.append("")
        lines.append("Nothing settled yet. Hit rates appear once games finish and Kalshi settles them.")
        lines += banner("4. Execution quality") + _fill_lines(trades, done) + _funnel_lines(games, snapshots, trades)
        return "\n".join(lines).rstrip()

    overall = summarize(done)
    lines += banner("2. Overall result")
    lines += _table([("ALL SETTLED", overall)])
    lines += ["  n = trades, W-L = wins-losses, hit = win rate (each trade counts once; the 95% CI is on this), wtd hit =",
              "  win rate weighted by contracts (what dollars follow), implied = average entry price, b/e = break-even rate",
              "  after fees, edge = wtd hit minus b/e in percentage points. Parentheses mean negative.",
              "  " + edge_verdict(overall["hit_rate"], overall["ci_low"], overall["ci_high"], overall["breakeven"]),
              ""]
    lines += _win_loss_lines(done) + _daily_lines(done) + _sample_lines(overall, trades)

    lines += banner("3. Breakdowns")
    lines.append("Fills by sport and entry price (open and settled; 'avg size' is contracts per fill):")
    lines += ["  " + row for row in fills_table(trades)]
    lines += ["", "By entry price:"] + _table(by_bucket(done)) + ["", "By sport:"] + _table(by_sport(done))
    lines += ["", "By sport and entry price:", "  " + table_header(LABEL_W + 2)]
    for sport, rows in by_sport_and_bucket(done):
        lines.append(f"  {sport}")
        lines += ["    " + table_row(label, s, LABEL_W - 2) for label, s in rows]

    for title, key_fn, order in (("By side bought:", side_label, SIDE_ORDER),
                                 ("By bid/ask spread at entry:", spread_label, SPREAD_ORDER),
                                 ("By entry time relative to the game's listed end:", timing_label, TIMING_ORDER)):
        rows = group_rows(done, key_fn, order)
        if rows:
            lines += ["", title] + _table(rows)
    lines.append("")
    lines += banner("4. Execution quality")
    lines += _fill_lines(trades, done) + _funnel_lines(games, snapshots, trades)

    lines += ["Edge exists only if hit rate beats the implied probability by more than fees AND the",
              "confidence interval stays above the break-even rate. Small samples will mislead you.",
              "Fills are sized to what the book offered, so small fills pay proportionally more in",
              "rounded-up fees; judge hit rate vs implied first and treat roi at small sizes as pessimistic."]
    return "\n".join(lines)
