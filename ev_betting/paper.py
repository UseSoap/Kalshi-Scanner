"""Paper trades: open on +EV rows, keep the closing line current, settle from Kalshi results.

The first ten columns of paper_trades.csv match the workbook's Bet Tracker (columns A-J), so they
can be pasted straight in. Everything is a simulation; no orders are ever placed.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from kalshi_scanner.client import KalshiError
from kalshi_scanner.fees import order_fee
from kalshi_scanner.orderbook import fill_estimate, parse_book
from kalshi_scanner.scanner import parse_ts, price_cents

from .config import Settings

CT = ZoneInfo("America/Chicago")
FILE = "paper_trades.csv"
COLUMNS = [
    # --- Bet Tracker columns A-J ---
    "date", "sport", "event", "selection", "venue", "odds_price", "stake", "fair_prob_at_bet",
    "closing_fair_prob", "result",
    # --- extra detail ---
    "trade_id", "ticker", "event_ticker", "start_utc", "contracts", "entry_price_c", "fee_usd",
    "ev_pct_at_bet", "ask_at_scan_c", "line_age_min", "sharp_decimal", "opened_at",
    "close_updated_at", "close_lag_min", "pnl_usd", "clv_pct", "settled_at",
]
SETTLE_AFTER = timedelta(hours=2)   # don't ask Kalshi for a result until well after the start


def load(data_dir: Path) -> list[dict]:
    path = data_dir / FILE
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [{c: row.get(c, "") for c in COLUMNS} for row in csv.DictReader(f)]


def save(trades: list[dict], data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / FILE).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for t in trades:
            w.writerow({c: t.get(c, "") for c in COLUMNS})


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def current_bankroll(trades: list[dict], start: float) -> float:
    return start + sum(_num(t["pnl_usd"]) for t in trades if t["result"] in ("Win", "Loss", "Push"))


def size_order(fair: float, ask_c: float, bankroll: float, cfg: Settings) -> int:
    p = ask_c / 100.0
    cost = p + cfg.fee_rate * p * (1 - p)
    if cost >= 1:
        return 0
    kelly = max(0.0, (fair - cost) / (1 - cost))
    target = min(kelly * cfg.kelly, cfg.max_stake) * bankroll
    return int(target // cost)


def open_trades(client, rows: list[dict], trades: list[dict], cfg: Settings, now: datetime) -> tuple[list[dict], list[str]]:
    """Open one paper trade per game for each +EV row. Returns (new trades, notes)."""
    traded_events = {t["event_ticker"] for t in trades}
    bankroll = current_bankroll(trades, cfg.bankroll)
    new, notes = [], []
    for r in sorted((r for r in rows if r["status"] == "+EV"), key=lambda r: -r["ev_pct"]):
        if r["event_ticker"] in traded_events:
            continue
        want = size_order(r["fair_prob"], r["kalshi_ask_c"], bankroll, cfg)
        if want < 1:
            notes.append(f"{r['team']}: +EV but stake rounds to 0 contracts")
            continue
        entry, contracts = r["kalshi_ask_c"], want
        try:
            book = parse_book(client.get_orderbook(r["ticker"]))
        except KalshiError:
            book = None
        if book is not None:
            fill = fill_estimate(book, "yes", want, max_price=r["kalshi_ask_c"])
            if not fill["filled"]:
                notes.append(f"{r['team']}: +EV but nothing to buy at {r['kalshi_ask_c']:.0f}c")
                continue
            contracts, entry = fill["filled"], fill["vwap"]
        fee = order_fee(entry, contracts, cfg.fee_rate)
        stake = contracts * entry / 100.0 + fee
        ev = r["fair_prob"] * contracts / stake - 1
        if ev < cfg.min_ev:
            notes.append(f"{r['team']}: edge fell to {ev:.1%} after the real order fee")
            continue
        t = {
            "date": now.astimezone(CT).strftime("%Y-%m-%d"), "sport": r["sport"], "event": r["game"],
            "selection": f"{r['team']} ML", "venue": "Kalshi", "odds_price": f"{entry / 100:.4f}",
            "stake": f"{stake:.2f}", "fair_prob_at_bet": f"{r['fair_prob']:.4f}",
            "closing_fair_prob": f"{r['fair_prob']:.4f}", "result": "Open",
            "trade_id": f"{r['ticker']}|yes", "ticker": r["ticker"], "event_ticker": r["event_ticker"],
            "start_utc": r["start_utc"], "contracts": str(contracts), "entry_price_c": f"{entry:.2f}",
            "fee_usd": f"{fee:.2f}", "ev_pct_at_bet": f"{ev * 100:.2f}", "ask_at_scan_c": f"{r['kalshi_ask_c']:.2f}",
            "line_age_min": r["line_age_min"], "sharp_decimal": r["sharp_decimal"],
            "opened_at": now.isoformat(timespec="seconds"), "close_updated_at": now.isoformat(timespec="seconds"),
        }
        trades.append(t)
        new.append(t)
        traded_events.add(r["event_ticker"])
    return new, notes


def update_closing_lines(rows: list[dict], trades: list[dict], now: datetime) -> int:
    """Before each game starts, keep replacing closing_fair_prob with the newest Pinnacle fair price."""
    fair = {r["ticker"]: r["fair_prob"] for r in rows if r.get("fair_prob") is not None}
    n = 0
    for t in trades:
        if t["result"] != "Open" or t["ticker"] not in fair:
            continue
        start = parse_ts(t["start_utc"])
        if start and start > now:
            t["closing_fair_prob"] = f"{fair[t['ticker']]:.4f}"
            t["close_updated_at"] = now.isoformat(timespec="seconds")
            n += 1
    return n


def _finish(t: dict, result: str, pnl: float, now: datetime) -> None:
    stake = _num(t["stake"])
    contracts = _num(t["contracts"])
    close = _num(t["closing_fair_prob"], None)
    t["result"] = result
    t["pnl_usd"] = f"{pnl:.2f}"
    t["settled_at"] = now.isoformat(timespec="seconds")
    if close is not None and stake > 0:
        t["clv_pct"] = f"{(close * contracts / stake - 1) * 100:.2f}"
    start, upd = parse_ts(t["start_utc"]), parse_ts(t["close_updated_at"])
    if start and upd:
        t["close_lag_min"] = f"{(start - upd).total_seconds() / 60:.0f}"


def settle(client, trades: list[dict], now: datetime) -> list[dict]:
    done = []
    for t in trades:
        if t["result"] != "Open":
            continue
        start = parse_ts(t["start_utc"])
        if start and now < start + SETTLE_AFTER:
            continue
        try:
            m = client.get_market(t["ticker"])
        except KalshiError:
            continue
        status, result = str(m.get("status", "")).lower(), str(m.get("result", "")).lower()
        contracts, entry, fee = _num(t["contracts"]), _num(t["entry_price_c"]), _num(t["fee_usd"])
        if result == "yes":
            _finish(t, "Win", contracts * (100 - entry) / 100 - fee, now)
        elif result == "no":
            _finish(t, "Loss", -(contracts * entry / 100) - fee, now)
        elif status in ("settled", "finalized"):
            # Neither side won (cancelled/void). Use the settlement value if given, else Kalshi's 50c rule.
            value = price_cents(m, "settlement_value")
            value = 50.0 if value is None else value
            _finish(t, "Push", contracts * (value - entry) / 100 - fee, now)
        else:
            continue
        done.append(t)
    return done


def summary(trades: list[dict], start_bankroll: float) -> dict:
    settled = [t for t in trades if t["result"] in ("Win", "Loss", "Push")]
    clvs = [_num(t["clv_pct"]) for t in settled if t["clv_pct"] != ""]
    staked = sum(_num(t["stake"]) for t in settled)
    pnl = sum(_num(t["pnl_usd"]) for t in settled)
    expected = sum(_num(t["stake"]) * _num(t["ev_pct_at_bet"]) / 100 for t in settled)
    return {
        "open": sum(t["result"] == "Open" for t in trades),
        "settled": len(settled),
        "wins": sum(t["result"] == "Win" for t in settled),
        "staked": staked, "pnl": pnl, "expected": expected,
        "roi": pnl / staked if staked else 0.0,
        "avg_clv": sum(clvs) / len(clvs) if clvs else None,
        "beat_close": (sum(c > 0 for c in clvs) / len(clvs)) if clvs else None,
        "bankroll": start_bankroll + pnl,
    }
