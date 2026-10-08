"""Command line: python -m kalshi_scanner [loop|run|scan|settle|report|discover]"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from . import combos, report, settle
from .client import KalshiClient
from .runner import run_loop, scan_cycle, verbose_lines
from .scanner import Config, DEFAULT_SERIES
from .schedule import ScheduleConfig


def _config(args) -> Config:
    series = args.series or os.environ.get("KALSHI_SERIES")
    series_list = [s.strip() for s in series.split(",") if s.strip()] if series else list(DEFAULT_SERIES)
    return Config(series=series_list, min_price=args.min_price, max_price=args.max_price,
                  max_spread=args.max_spread, contracts=args.contracts,
                  min_contracts=args.min_contracts, max_slippage=args.max_slippage,
                  timezone=args.timezone, same_day_only=not args.any_day,
                  check_depth=not args.skip_depth, one_trade_per_event=not args.multi_per_game,
                  mirror_tolerance=args.mirror_tolerance)


def cmd_scan(args, client) -> None:
    found, diag, added = scan_cycle(client, _config(args), datetime.now(timezone.utc))
    for line in verbose_lines(found, diag, added):
        print(line)


def cmd_loop(args, client) -> None:
    sc = ScheduleConfig(window_before_min=args.window_before, window_after_min=args.window_after,
                        lookahead_min=args.lookahead, hot_ask=args.hot_ask,
                        hot_interval_s=args.hot_interval, live_interval_s=args.live_interval)
    result = run_loop(client, _config(args), sc, max_minutes=args.max_minutes)
    print(f"loop finished: {result}  (billed Actions time is this, rounded up, plus about 20s of setup)")
    again = wants_another_run(result)
    print(f"chain: {'start another run' if again else 'nothing more to watch, stopping'}")
    # In GitHub Actions this becomes `steps.<id>.outputs.again`, which the workflow uses to re-dispatch itself.
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a") as fh:
            fh.write(f"again={'true' if again else 'false'}\n")


def wants_another_run(result: dict) -> bool:
    """True when the loop stopped only because its time budget ran out, not because nothing is on.

    run_loop ends in mode "idle" when there are no games in or near a watch window. Any other
    last mode (live, hot, waiting) means games are still being watched, so a fresh run should
    pick up where this one stopped.
    """
    return result.get("last_mode") != "idle"


def cmd_settle(args, client) -> None:
    print(f"settle: {settle.settle_open_trades(client)} trades resolved")


def cmd_discover(args, client) -> None:
    keywords = (   # only used without --all-series
        # the leagues the scanner started with
        "MLB", "NHL", "NBA", "NFL", "NCAA", "WNBA", "MLS", "EPL", "PREMIER", "LIGA", "SERIE",
        "BUNDES", "LIGUE", "CHAMPIONS", "UCL", "ATP", "WTA", "UFC", "TENNIS",
        # golf
        "GOLF", "PGA", "MASTERS", "DP WORLD", "RYDER",
        # more soccer
        "EUROPA", "CONFERENCE", "EREDIVISIE", "LIGAMX", "LIGA MX", "CHAMPIONSHIP", "SAUDI",
        "LIBERTADORES", "NATIONS LEAGUE", "WORLD CUP", "QUALIF", "SOCCER",
        # overseas baseball (plays overnight US time), cricket
        "KBO", "NPB", "CRICKET", "IPL",
        # tennis tiers, college and other combat/racing/esports
        "CHALLENGER", "ITF", "BOXING", "PFL", "F1", "FORMULA", "NASCAR", "INDYCAR",
        "ESPORT", "LEAGUE OF LEGENDS", "COUNTER-STRIKE", "DOTA", "VALORANT",
    )
    series = client.list_series("Sports")
    rows = series if args.all_series else [
        s for s in series if any(k in (s.get("ticker", "") + s.get("title", "")).upper() for k in keywords)]
    for s in sorted(rows, key=lambda s: s.get("ticker", "")):
        print(f"{s.get('ticker', ''):<28} {s.get('title', '')}")
    kind = "series" if args.all_series else "matching series"
    print(f"\n{len(rows)} {kind} (of {len(series)} in Sports). Set KALSHI_SERIES to a comma-separated list to scan them.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kalshi_scanner")
    parser.add_argument("command", choices=["loop", "run", "scan", "settle", "report", "discover"], nargs="?", default="run")
    parser.add_argument("--series", help="comma-separated series tickers (or KALSHI_SERIES env var)")
    parser.add_argument("--min-price", type=float, default=90.0, help="lowest ask (cents) to log")
    parser.add_argument("--max-price", type=float, default=99.0)
    parser.add_argument("--max-spread", type=float, default=5.0)
    parser.add_argument("--contracts", type=int, default=100,
                        help="largest paper order; each trade fills as many as the book allows, up to this")
    parser.add_argument("--min-contracts", type=int, default=5,
                        help="smallest fill worth recording; thinner books are skipped")
    parser.add_argument("--max-slippage", type=float, default=2.0,
                        help="never pay more than the quoted ask plus this many cents")
    parser.add_argument("--mirror-tolerance", type=float, default=2.0,
                        help="in two-team games, once one side reaches --min-price, also consider the other side "
                             "of the same bet down to this many cents below it, and buy whichever is cheaper (0 = off)")
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--skip-depth", action="store_true",
                        help="do not check order-book depth (paper fills at the top ask, which is optimistic)")
    loop = parser.add_argument_group("loop mode (adaptive scheduling)")
    loop.add_argument("--max-minutes", type=float, default=25, help="stop after this long; 0 = until idle")
    loop.add_argument("--window-before", type=int, default=180, help="minutes before a game's expected end to start watching")
    loop.add_argument("--window-after", type=int, default=90, help="minutes after the expected end to keep watching")
    loop.add_argument("--lookahead", type=int, default=30, help="wait for a window opening within this many minutes")
    loop.add_argument("--hot-ask", type=float, default=85.0, help="ask (cents) that triggers the fastest scan rate")
    loop.add_argument("--hot-interval", type=int, default=45, help="seconds between scans when a game is lopsided")
    loop.add_argument("--live-interval", type=int, default=90, help="seconds between scans during a game window")
    parser.add_argument("--multi-per-game", action="store_true",
                        help="allow several paper trades in one game (they are correlated, so this inflates the sample size)")
    parser.add_argument("--any-day", action="store_true", help="do not restrict to contracts ending today")
    parser.add_argument("--all-series", action="store_true",
                        help="discover: list every Sports series instead of only keyword matches")
    args = parser.parse_args()

    if args.command == "report":
        print(report.render())
        print()
        print(combos.render())
        return

    client = KalshiClient()
    if args.command == "discover":
        cmd_discover(args, client)
    elif args.command == "loop":
        cmd_loop(args, client)
    elif args.command == "scan":
        cmd_scan(args, client)
    elif args.command == "settle":
        cmd_settle(args, client)
    else:
        cmd_settle(args, client)
        cmd_scan(args, client)


if __name__ == "__main__":
    main()
