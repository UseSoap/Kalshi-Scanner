"""One scheduled pass: settle, scan, update closing lines, open paper trades, write files, notify."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from kalshi_scanner.client import KalshiClient
from kalshi_scanner.scanner import parse_ts

from . import notify, paper
from .board import build_board
from .config import SPORTS, Settings
from .odds_api import OddsApiError, fetch_moneylines

CT = ZoneInfo("America/Chicago")
BOARD_COLS = ["status", "ev_pct", "sport", "team", "kalshi_ask_c", "fair_prob", "fair_american", "game",
              "start_utc", "line_age_min", "sharp_books", "sharp_decimal", "fair_multiplicative", "fair_additive",
              "fair_power", "fair_shin", "ticker", "event_ticker"]
RUN_COLS = ["run_at", "sports", "credits_remaining", "pinnacle_games", "kalshi_games", "matched", "stale",
            "plus_ev", "opened", "settled", "error"]


def _ct(iso: str) -> str:
    dt = parse_ts(iso)
    return dt.astimezone(CT).strftime("%a %-I:%M %p") if dt else ""


def _money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"${x:,.2f}"


def write_board(rows, cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (r["ev_pct"] is None, -(r["ev_pct"] or 0)))
    with (cfg.data_dir / "board_latest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BOARD_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def append_run(cfg, rec):
    path = cfg.data_dir / "runs.csv"
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RUN_COLS)
        if new:
            w.writeheader()
        w.writerow({k: rec.get(k, "") for k in RUN_COLS})


def write_status(cfg, now, rows, stats, unmatched, trades, error, credits):
    s = paper.summary(trades, cfg.bankroll)
    L = [f"# EV scanner status", "",
         f"Last run: **{now.astimezone(CT).strftime('%a %b %-d, %-I:%M %p')} CT**"
         + (f" · Odds API credits left: {credits}" if credits is not None else ""), ""]
    if error:
        L += [f"> **Problem on this run:** {error}", ""]
    clv = "" if s["avg_clv"] is None else f"{s['avg_clv']:.2f}%"
    beat = "" if s["beat_close"] is None else f"{s['beat_close']:.0%}"
    L += ["## Paper results", "",
          "| Settled | Wins | Staked | P&L | Expected P&L | ROI | Avg CLV | Beat close | Bankroll | Open |",
          "|---|---|---|---|---|---|---|---|---|---|",
          f"| {s['settled']} | {s['wins']} | {_money(s['staked'])} | {_money(s['pnl'])} | {_money(s['expected'])} | "
          f"{s['roi']:.1%} | {clv} | {beat} | {_money(s['bankroll'])} | {s['open']} |", ""]
    opens = [t for t in trades if t["result"] == "Open"]
    if opens:
        L += ["## Open paper trades", "", "| Pick | Game | Start (CT) | Price | Contracts | Stake | Fair at bet | Latest fair |",
              "|---|---|---|---|---|---|---|---|"]
        for t in opens:
            L.append(f"| {t['selection']} | {t['event']} | {_ct(t['start_utc'])} | {float(t['entry_price_c']):.0f}c | "
                     f"{t['contracts']} | ${float(t['stake']):.2f} | {float(t['fair_prob_at_bet']):.1%} | "
                     f"{float(t['closing_fair_prob']):.1%} |")
        L.append("")
    L += ["## Health check", "", "| Sport | Pinnacle games | Not started | Kalshi games | Matched | Stale lines | +EV |",
          "|---|---|---|---|---|---|---|"]
    for a, st in stats.items():
        if "error" in st:
            L.append(f"| {a} | {st['error']} | | | | | |")
        else:
            L.append(f"| {SPORTS[a][2]} | {st['pinnacle_games']} | {st['pinnacle_pregame']} | {st['kalshi_games']} | "
                     f"{st['matched']} | {st['stale']} | {st['plus_ev']} |")
    L += ["", f"## Board ({len(rows)} contracts, best edge first)", "",
          "| Status | EV % | Team | Kalshi ask | Fair | Fair odds | Game | Start (CT) | Line age |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:60]:
        ev = "" if r["ev_pct"] is None else f"{r['ev_pct']:.1f}"
        ask = "" if r["kalshi_ask_c"] is None else f"{r['kalshi_ask_c']:.0f}c"
        fair = "" if r["fair_prob"] is None else f"{r['fair_prob']:.1%}"
        age = "" if r["line_age_min"] is None else r["line_age_min"]
        L.append(f"| {r['status']} | {ev} | {r['team']} | {ask} | {fair} | {r['fair_american']} | {r['game']} | "
                 f"{_ct(r['start_utc'])} | {age} |")
    if len(rows) > 60:
        L.append(f"\n_{len(rows) - 60} more rows in board_latest.csv_")
    if unmatched:
        L += ["", f"## Kalshi games without a Pinnacle line yet ({len(unmatched)})", "",
              "Usually games further out than Pinnacle has posted, or games already under way.", ""]
        L += [f"- {u}" for u in unmatched[:40]]
    (cfg.data_dir / "STATUS.md").write_text("\n".join(L) + "\n")


def run(cfg: Settings | None = None, client=None, fetch=None, now: datetime | None = None) -> dict:
    cfg = cfg or Settings.from_env()
    client = client or KalshiClient()
    now = now or datetime.now(timezone.utc)
    trades = paper.load(cfg.data_dir)
    error, credits = "", None

    settled = paper.settle(client, trades, now)

    rows, stats, unmatched = [], {}, []
    if not cfg.odds_api_key and fetch is None:
        error = "ODDS_API_KEY secret is not set, so no Pinnacle lines were pulled (settling still ran)."
        for alias in cfg.sports:   # still confirm Kalshi is reachable and has games
            if alias in SPORTS:
                events = {m.get("event_ticker") for m in client.list_markets(series_ticker=SPORTS[alias][1], status="open")}
                stats[alias] = {"pinnacle_games": 0, "pinnacle_pregame": 0, "kalshi_games": len(events - {None}),
                                "matched": 0, "stale": 0, "plus_ev": 0}
    else:
        fetch = fetch or (lambda key: fetch_moneylines(key, cfg.odds_api_key, cfg.sharp_books))
        guarded = {"credits": None}

        def fetch_guarded(sport_key):
            if guarded["credits"] is not None and guarded["credits"] < cfg.min_credits:
                raise OddsApiError(f"only {guarded['credits']} Odds API credits left; pausing scans to protect the quota")
            events, left = fetch(sport_key)
            guarded["credits"] = left
            return events, left

        try:
            rows, stats, unmatched, credits = build_board(client, fetch_guarded, cfg, now)
        except OddsApiError as exc:
            error = str(exc)
            credits = guarded["credits"]

    paper.update_closing_lines(rows, trades, now)
    new, notes = paper.open_trades(client, rows, trades, cfg, now)
    paper.save(trades, cfg.data_dir)
    rows = write_board(rows, cfg)
    write_status(cfg, now, rows, stats, unmatched, trades, error, credits)
    append_run(cfg, {
        "run_at": now.isoformat(timespec="seconds"), "sports": ",".join(cfg.sports), "credits_remaining": credits,
        "pinnacle_games": sum(s.get("pinnacle_games", 0) for s in stats.values()),
        "kalshi_games": sum(s.get("kalshi_games", 0) for s in stats.values()),
        "matched": sum(s.get("matched", 0) for s in stats.values()),
        "stale": sum(s.get("stale", 0) for s in stats.values()),
        "plus_ev": sum(s.get("plus_ev", 0) for s in stats.values()),
        "opened": len(new), "settled": len(settled), "error": error,
    })

    # ---- notifications
    for t in new:
        notify.send(cfg.ntfy_topic,
                    f"+EV {t['ev_pct_at_bet']}%: {t['selection']} at {float(t['entry_price_c']):.0f}c",
                    f"{t['event']}, {_ct(t['start_utc'])} CT\n"
                    f"Fair {float(t['fair_prob_at_bet']):.1%} vs. price {float(t['entry_price_c']):.0f}c + fee\n"
                    f"Paper trade: {t['contracts']} contracts, ${float(t['stake']):.2f}\n"
                    f"Check Kalshi before acting; prices move fast.",
                    priority="high", tags="moneybag")
    if settled:
        s = paper.summary(trades, cfg.bankroll)
        lines = [f"{t['selection']}: {t['result']} {_money(float(t['pnl_usd']))}" for t in settled]
        clv = "" if s["avg_clv"] is None else f", avg CLV {s['avg_clv']:.2f}%"
        notify.send(cfg.ntfy_topic, f"Settled {len(settled)} paper trade(s)",
                    "\n".join(lines) + f"\n\nTotal: {_money(s['pnl'])} over {s['settled']} bets "
                    f"(expected {_money(s['expected'])}){clv}", tags="checkered_flag")
    if error and ("401" in error or "credits" in error or "quota" in error or "not set" in error):
        notify.send(cfg.ntfy_topic, "EV scanner needs attention", error, priority="high", tags="warning")

    # ---- log
    print(f"Run at {now.astimezone(CT):%Y-%m-%d %I:%M %p} CT | credits left: {credits}")
    for a, st in stats.items():
        print(f"  {a}: {st}")
    print(f"  opened {len(new)}, settled {len(settled)}, open now {sum(t['result'] == 'Open' for t in trades)}")
    for n in notes:
        print("  note:", n)
    if error:
        print("  ERROR:", error)
    top = [r for r in rows if r["ev_pct"] is not None][:8]
    if top:
        print("  best edges right now:")
        for r in top:
            print(f"    {r['ev_pct']:+6.2f}%  {r['team']:<26} ask {r['kalshi_ask_c']:.0f}c  fair {r['fair_prob']:.1%}  {r['game']}")
    return {"new": new, "settled": settled, "error": error, "stats": stats, "rows": rows}
