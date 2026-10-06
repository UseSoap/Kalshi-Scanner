"""Plain-CSV storage so GitHub diffs stay readable and no database is needed."""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from .scanner import Candidate, sport_of

DATA_DIR = Path(os.environ.get("KALSHI_DATA_DIR", "data"))

SNAPSHOT_FIELDS = [f.name for f in fields(Candidate)]
TRADE_FIELDS = [
    "trade_id", "first_seen", "ticker", "event_ticker", "series", "sport", "title", "side",
    "entry_price", "best_ask", "depth_at_ask", "bid", "spread", "volume", "open_interest", "expiry",
    "contracts", "fee_usd", "status", "result", "won", "pnl_usd", "settled_at",
]


def _append_rows(path: Path, field_names: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=field_names)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def log_snapshots(candidates: list[Candidate], now: datetime, data_dir: Path | None = None) -> None:
    root = data_dir or DATA_DIR
    path = root / "snapshots" / f"{now.date().isoformat()}.csv"
    _append_rows(path, SNAPSHOT_FIELDS, [asdict(c) for c in candidates])


def load_trades(data_dir: Path | None = None) -> list[dict]:
    path = (data_dir or DATA_DIR) / "trades.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def save_trades(trades: list[dict], data_dir: Path | None = None) -> None:
    path = (data_dir or DATA_DIR) / "trades.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        writer.writeheader()
        for row in trades:
            writer.writerow({k: row.get(k, "") for k in TRADE_FIELDS})
    tmp.replace(path)


def record_new_trades(candidates: list[Candidate], data_dir: Path | None = None,
                      one_per_event: bool = True) -> int:
    """Open a paper trade the first time a (ticker, side) shows up. Returns count added.

    Only candidates with enough size to fill (depth_status "ok", or "unchecked" when the
    depth check is switched off) become trades. The trade is sized to what could fill, and the
    entry is the average fill price, not the top ask.

    With `one_per_event` (the default) a game gets at most one paper trade. Several contracts
    in one game (a soccer win/draw/loss set, or both sides of a game that flips) move together,
    so counting each as its own trade would overstate the sample size. When several qualify at
    once, the highest-priced one (then the larger fill) is taken, and a game that already has a
    trade is skipped. Skipped candidates still appear in the snapshots.
    """
    trades = load_trades(data_dir)
    known = {t["trade_id"] for t in trades}
    events = {t["event_ticker"] for t in trades if t.get("event_ticker")}
    added = 0
    ranked = sorted(candidates, key=lambda c: (-(c.fill_price if c.fill_price is not None else c.ask), -c.contracts))
    for c in ranked:
        if c.depth_status not in ("ok", "unchecked"):
            continue
        trade_id = f"{c.ticker}|{c.side}"
        if trade_id in known:
            continue
        if one_per_event and c.event_ticker and c.event_ticker in events:
            continue
        known.add(trade_id)
        if c.event_ticker:
            events.add(c.event_ticker)
        added += 1
        trades.append({
            "trade_id": trade_id, "first_seen": c.ts, "ticker": c.ticker,
            "event_ticker": c.event_ticker, "series": c.series, "sport": sport_of(c.series), "title": c.title,
            "side": c.side, "entry_price": c.fill_price if c.fill_price is not None else c.ask,
            "best_ask": c.ask, "depth_at_ask": c.depth_at_ask if c.depth_at_ask is not None else "", "bid": c.bid if c.bid is not None else "",
            "spread": c.spread if c.spread is not None else "", "volume": c.volume,
            "open_interest": c.open_interest, "expiry": c.expiry, "contracts": c.contracts,
            "fee_usd": c.fee_usd, "status": "open", "result": "", "won": "",
            "pnl_usd": "", "settled_at": "",
        })
    if added:
        save_trades(trades, data_dir)
    return added
