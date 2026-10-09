"""Pre-registered hypothesis test: entries made late in a game cover.

FROZEN. Do not edit the constants below once the test is running. Changing the cutoff, the stop rule or
the success rule after seeing results would turn a test into a search for a flattering slice. If the
definition has to change, that is a new hypothesis with its own start time; this one keeps its result.

Hypothesis: paper trades opened within LATE_MINUTES of the game's listed end, or after it, win more often
than their break-even rate after fees.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .insights import TIMING_ORDER, edge_verdict, num, timing_label
from .report import _table, banner, settled, sport_label, summarize
from .scanner import parse_ts
from .storage import load_trades

# ---- frozen definition ---------------------------------------------------------------------------------
LATE_LABELS = tuple(TIMING_ORDER[:2])      # "after listed end" + "0-30 min before" (same buckets the report shows)
STOP_N = 150                               # evaluate once this many late entries have SETTLED
# Only trades opened at or after this instant count toward the test. Everything earlier, including the 22-0
# late-entry record that suggested the idea, is exploratory and shown separately.
START = datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)      # 1:30 PM Central, Oct 9 2026
SUCCESS_RULE = "the 95% CI lower bound of the late group's hit rate is above that group's break-even rate"
# ---------------------------------------------------------------------------------------------------------

STALE_QUOTE_S = 90                         # a quote unchanged this long had sat across at least two scans


def is_late(trade: dict) -> bool:
    return timing_label(trade) in LATE_LABELS


def counts(trade: dict) -> bool:
    seen = parse_ts(trade.get("first_seen"))
    return bool(seen) and seen >= START


def _median(xs: list[float]):
    xs = sorted(xs)
    if not xs:
        return None
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def _execution_row(label: str, trades: list[dict]) -> str:
    """Could a real order have gotten these? Uses fields stored on every trade (plus quote age on new ones)."""
    if not trades:
        return f"  {label:<10} n=0"
    sizes = [v for v in (num(t.get("depth_at_ask")) for t in trades) if v is not None]
    spreads = [v for v in (num(t.get("spread")) for t in trades) if v is not None]
    gaps = [num(t.get("entry_price")) - num(t.get("best_ask")) for t in trades
            if num(t.get("entry_price")) is not None and num(t.get("best_ask")) is not None]
    ages = [v for v in (num(t.get("quote_age_s")) for t in trades) if v is not None]
    size = _median(sizes)
    parts = [f"n={len(trades):<4}",
             f"median size at best ask {'n/a' if size is None else f'{size:,.0f}'}",
             f"partial fills {sum(1 for t in trades if (num(t.get('contracts')) or 0) < 100) / len(trades):.0%}",
             f"avg spread {sum(spreads) / len(spreads):.1f}c" if spreads else "avg spread n/a",
             f"filled >1c under quote {sum(1 for g in gaps if g < -1.0) / len(gaps):.0%}" if gaps else "vs quote n/a"]
    if ages:
        stale = sum(1 for a in ages if a >= STALE_QUOTE_S) / len(ages)
        parts.append(f"quote unchanged >={STALE_QUOTE_S}s {stale:.0%} (of {len(ages)} timed)")
    else:
        parts.append("quote age not recorded yet")
    return f"  {label:<10} " + "  ".join(parts)


def render(data_dir: Path | None = None) -> str:
    trades = load_trades(data_dir)
    test = [t for t in trades if counts(t)]
    late_all, control_all = [t for t in test if is_late(t)], [t for t in test if not is_late(t)]
    late, control = settled(late_all), settled(control_all)
    exploratory = [t for t in settled(trades) if not counts(t) and is_late(t)]

    out = banner("5. Pre-registered hypothesis: late entries cover")
    out += [f"Definition (frozen): entry in the final 30 minutes before the game's listed end, or after it.",
            f"Counts only trades opened on or after {START:%Y-%m-%d %H:%M}Z. Evaluated at {STOP_N} settled late entries.",
            f"Success rule: {SUCCESS_RULE}.",
            "If it fails, the hypothesis is closed. A different cutoff is a new hypothesis, not a retry.", ""]

    n = len(late)
    open_late = len(late_all) - n
    out.append(f"Progress: {n}/{STOP_N} late entries settled ({open_late} still open). "
               f"Control (all other entries, same period): {len(control)} settled, {len(control_all) - len(control)} open.")
    out += _table([("LATE (the test)", summarize(late)), ("control: not late", summarize(control))])

    if n:
        s = summarize(late)
        if n < STOP_N:
            verdict = (f"IN PROGRESS - {STOP_N - n} more settled late entries before this is judged. Today's lower bound is "
                       f"{s['ci_low']:.1%} against a {s['breakeven']:.1%} break-even. Do not act on it yet.")
        elif s["ci_low"] > s["breakeven"]:
            verdict = (f"SUPPORTED - the lower bound {s['ci_low']:.1%} clears the {s['breakeven']:.1%} break-even "
                       f"at {n} late entries.")
        else:
            verdict = (f"NOT SUPPORTED - at {n} late entries the lower bound {s['ci_low']:.1%} does not clear the "
                       f"{s['breakeven']:.1%} break-even. {edge_verdict(s['hit_rate'], s['ci_low'], s['ci_high'], s['breakeven'])}")
        out += ["", "  Verdict: " + verdict]

    out += ["", "Late group split (exploratory; does the result rest on one sport's end-time quirks?):"]
    tennis = [t for t in late if sport_label(t).startswith("Tennis")]
    other = [t for t in late if not sport_label(t).startswith("Tennis")]
    out += _table([("late: tennis", summarize(tennis)), ("late: everything else", summarize(other))])

    out += ["", "Could a real order have gotten these fills? (stale-quote check; all entries, open and settled)",
            _execution_row("late", late_all), _execution_row("control", control_all),
            "  A late group with thinner books, more partial fills, many fills well under the quote, or quotes that had",
            "  sat unchanged for a while is suspect: the paper result may not be tradeable. Quote age counts how long",
            "  the scanner saw the exact bid/ask unchanged; 0 means first sighting or just moved."]

    out += ["", "Excluded from the test (opened before the start time; this is what suggested the idea):"]
    out += _table([("late, exploratory", summarize(exploratory))])
    return "\n".join(out)
