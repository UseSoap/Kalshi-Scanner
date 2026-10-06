"""One scan cycle, and the adaptive loop that repeats it while games are on."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import settle
from .scanner import Config, new_diag, scan
from .schedule import ScheduleConfig, make_plan
from .storage import log_snapshots, record_new_trades

SNAPSHOT_THROTTLE_S = 300   # in loop mode, re-log the same contract at most this often


def max_ask(diag: dict):
    return max((ask for ask, _bid, _t, _s in diag["top"]), default=None)


def scan_cycle(client, cfg: Config, now: datetime, last_logged: dict | None = None):
    """Scan once, log snapshots, open paper trades. Returns (candidates, diag, new_trade_count)."""
    diag = new_diag()
    found = scan(client, cfg, now, diag)
    to_log = found
    if last_logged is not None:
        to_log = []
        for c in found:
            key = (c.ticker, c.side)
            if key not in last_logged or (now - last_logged[key]).total_seconds() >= SNAPSHOT_THROTTLE_S:
                last_logged[key] = now
                to_log.append(c)
    log_snapshots(to_log, now)
    return found, diag, record_new_trades(found, one_per_event=cfg.one_trade_per_event)


def verbose_lines(found, diag, added) -> list[str]:
    stats = diag["per_series"]
    lines = ["markets seen per series: " + ", ".join(f"{k}={v}" for k, v in stats.items()),
             f"ending today (local time): {diag['same_day']} markets"]
    empty = [k for k, v in stats.items() if not v]
    if empty and len(empty) < len(stats):
        lines.append("series with no open markets (off-season, or the ticker is wrong): " + ", ".join(empty))
    if diag["reasons"]:
        lines.append("filtered out because: " + ", ".join(f"{k}={v}" for k, v in diag["reasons"].most_common()))
    if diag["top"]:
        lines.append("highest asks among today's contracts (ask/bid, ticker, side):")
        for ask, bid, ticker, side in sorted(diag["top"], key=lambda x: -x[0])[:8]:
            lines.append(f"  {ask}c/{bid}c  {ticker} {side.upper()}")
    if diag["reasons"].get("order book unreadable"):
        lines.append("WARNING: could not read the order book for some candidates, so no paper trade was opened "
                     "for them. Paste this log to Claude; the order-book parser may need adjusting.")
    if stats and not any(stats.values()):
        lines.append("WARNING: no open markets returned for any series. The series tickers are probably "
                     "wrong; run `python -m kalshi_scanner discover` and set KALSHI_SERIES.")
    lines.append(f"scan: {len(found)} candidates, {added} new paper trades")
    for c in sorted(found, key=lambda c: -c.ask)[:15]:
        fill = f"{c.fill_price:.2f}c" if c.fill_price is not None else "n/a"
        size = f"x{c.contracts}" if c.depth_status == "ok" else f"fillable={c.fillable}"
        lines.append(f"  {c.ticker} {c.side.upper()} ask={c.ask}c fill={fill} {size} depth@ask={c.depth_at_ask} "
                     f"spread={c.spread}c [{c.depth_status}] breakeven={c.breakeven_prob:.2%}")
    return lines


def run_loop(client, cfg: Config, sc: ScheduleConfig, max_minutes: float = 25.0,
             now_fn=lambda: datetime.now(timezone.utc), sleep_fn=time.sleep, log=print,
             settle_every_s: int = 300) -> dict:
    """Scan repeatedly at a pace set by the game schedule; stop when idle or out of time.

    `max_minutes` of 0 means run until idle (or forever, if games never stop).
    """
    start = now_fn()
    deadline = start + timedelta(minutes=max_minutes) if max_minutes else None
    last_settle, last_logged = None, {}
    cycles = trades = 0
    while True:
        now = now_fn()
        if last_settle is None or (now - last_settle).total_seconds() >= settle_every_s:
            resolved = settle.settle_open_trades(client, now)
            last_settle = now
            if resolved:
                log(f"settle: {resolved} trades resolved")
        found, diag, added = scan_cycle(client, cfg, now, last_logged)
        cycles += 1
        trades += added
        plan = make_plan(now, diag["expiries"], max_ask(diag), sc)

        if cycles == 1:
            for line in verbose_lines(found, diag, added):
                log(line)
        elif added or found:
            log(f"candidates={len(found)} new_trades={added}")
        top = max_ask(diag)
        log(f"[{now:%H:%M:%SZ}] {plan.mode}: {plan.reason}; top ask "
            f"{'n/a' if top is None else f'{top:g}c'}; "
            f"{'exiting' if plan.sleep_s is None else f'next scan in {plan.sleep_s}s'}")

        if plan.sleep_s is None:
            break
        if deadline and now + timedelta(seconds=plan.sleep_s) >= deadline:
            log("time budget reached; the next scheduled run continues from here")
            break
        sleep_fn(plan.sleep_s)
    elapsed = round((now_fn() - start).total_seconds() / 60.0, 1)
    return {"cycles": cycles, "new_trades": trades, "last_mode": plan.mode, "elapsed_min": elapsed}
