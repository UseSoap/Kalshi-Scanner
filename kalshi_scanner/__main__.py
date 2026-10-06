"""Command line: python -m kalshi_scanner [run|scan|settle|report|discover]"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

from . import combos, report, settle
from .client import KalshiClient
from .scanner import Config, DEFAULT_SERIES, new_diag, scan
from .storage import log_snapshots, record_new_trades


def _config(args) -> Config:
    series = args.series or os.environ.get("KALSHI_SERIES")
    series_list = [s.strip() for s in series.split(",") if s.strip()] if series else list(DEFAULT_SERIES)
    return Config(series=series_list, min_price=args.min_price, max_price=args.max_price,
                  max_spread=args.max_spread, contracts=args.contracts,
                  timezone=args.timezone, same_day_only=not args.any_day,
                  check_depth=not args.skip_depth)


def cmd_scan(args, client) -> None:
    cfg, now = _config(args), datetime.now(timezone.utc)
    diag = new_diag()
    found = scan(client, cfg, now, diag)
    log_snapshots(found, now)
    added = record_new_trades(found)
    stats = diag["per_series"]
    print("markets seen per series: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    print(f"ending today (local time): {diag['same_day']} markets")
    if diag["reasons"]:
        print("filtered out because: " + ", ".join(f"{k}={v}" for k, v in diag["reasons"].most_common()))
    if diag["top"]:
        print("highest asks among today's contracts (ask/bid, ticker, side):")
        for ask, bid, ticker, side in sorted(diag["top"], key=lambda x: -x[0])[:8]:
            print(f"  {ask}c/{bid}c  {ticker} {side.upper()}")
    if diag["reasons"].get("order book unreadable"):
        print("WARNING: could not read the order book for some candidates, so no paper trade was opened "
              "for them. Paste this log to Claude; the order-book parser may need adjusting.")
    if stats and not any(stats.values()):
        print("WARNING: no open markets returned for any series. The series tickers are probably "
              "wrong; run `python -m kalshi_scanner discover` and set KALSHI_SERIES.")
    print(f"scan: {len(found)} candidates, {added} new paper trades")
    for c in sorted(found, key=lambda c: -c.ask)[:15]:
        fill = f"{c.fill_price:.2f}c" if c.fill_price is not None else "n/a"
        print(f"  {c.ticker} {c.side.upper()} ask={c.ask}c fill={fill} depth@ask={c.depth_at_ask} "
              f"spread={c.spread}c [{c.depth_status}] breakeven={c.breakeven_prob:.2%}")


def cmd_settle(args, client) -> None:
    print(f"settle: {settle.settle_open_trades(client)} trades resolved")


def cmd_discover(args, client) -> None:
    keywords = ("MLB", "NHL", "NBA", "NFL", "NCAA")
    rows = [s for s in client.list_series("Sports")
            if any(k in (s.get("ticker", "") + s.get("title", "")).upper() for k in keywords)]
    for s in sorted(rows, key=lambda s: s.get("ticker", "")):
        print(f"{s.get('ticker', ''):<28} {s.get('title', '')}")
    print(f"\n{len(rows)} matching series. Set KALSHI_SERIES to a comma-separated list to scan them.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kalshi_scanner")
    parser.add_argument("command", choices=["run", "scan", "settle", "report", "discover"], nargs="?", default="run")
    parser.add_argument("--series", help="comma-separated series tickers (or KALSHI_SERIES env var)")
    parser.add_argument("--min-price", type=float, default=95.0)
    parser.add_argument("--max-price", type=float, default=99.0)
    parser.add_argument("--max-spread", type=float, default=3.0)
    parser.add_argument("--contracts", type=int, default=100)
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--skip-depth", action="store_true",
                        help="do not check order-book depth (paper fills at the top ask, which is optimistic)")
    parser.add_argument("--any-day", action="store_true", help="do not restrict to contracts ending today")
    args = parser.parse_args()

    if args.command == "report":
        print(report.render())
        print()
        print(combos.render())
        return

    client = KalshiClient()
    if args.command == "discover":
        cmd_discover(args, client)
    elif args.command == "scan":
        cmd_scan(args, client)
    elif args.command == "settle":
        cmd_settle(args, client)
    else:
        cmd_settle(args, client)
        cmd_scan(args, client)


if __name__ == "__main__":
    main()
