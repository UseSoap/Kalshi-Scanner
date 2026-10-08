"""Plain-CSV storage so GitHub diffs stay readable and no database is needed."""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .scanner import Candidate, sport_of

DATA_DIR = Path(os.environ.get("KALSHI_DATA_DIR", "data"))

# Candidate fields that are working state for the trade logic, not part of the snapshot files. Keeping them
# out means the snapshot columns never change, so day files that already exist stay valid to append to.
_INTERNAL_FIELDS = {"mirror_key", "via_mirror"}
SNAPSHOT_FIELDS = [f.name for f in fields(Candidate) if f.name not in _INTERNAL_FIELDS]
TRADE_FIELDS = [
    "trade_id", "first_seen", "ticker", "event_ticker", "series", "sport", "title", "side",
    "entry_price", "best_ask", "depth_at_ask", "bid", "spread", "volume", "open_interest", "expiry",
    "contracts", "fee_usd", "status", "result", "won", "pnl_usd", "settled_at",
    # via_mirror: "1" if the trade is on a contract that was below the entry floor itself and was taken
    # because the other side of the same bet reached it. partner_fill: that other side's fill price.
    "via_mirror", "partner_fill",
]
GAME_FIELDS = ["event_ticker", "series", "sport", "day", "expiry", "first_seen", "peak_ask"]


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
    _append_rows(path, SNAPSHOT_FIELDS, [{k: v for k, v in asdict(c).items() if k in SNAPSHOT_FIELDS} for c in candidates])


def load_snapshots(data_dir: Path | None = None) -> list[dict]:
    """Every logged candidate row from data/snapshots/*.csv, oldest file first."""
    folder = (data_dir or DATA_DIR) / "snapshots"
    rows: list[dict] = []
    if folder.exists():
        for path in sorted(folder.glob("*.csv")):
            with path.open(newline="") as fh:
                rows.extend(csv.DictReader(fh))
    return rows


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
    so counting each as its own trade would overstate the sample size. A game that already has a
    trade is skipped, and skipped candidates still appear in the snapshots. Which contract is taken:

    - In a two-team game the two sides ("A wins" YES, "B wins" NO) are the same bet, so the
      cheaper fill is taken (then the larger fill). That can be a contract just under the entry
      floor, offered because its mirror reached it.
    - Otherwise (a soccer win/draw/loss set) the contracts are different bets, so the
      highest-priced one is taken, as before.
    """
    trades = load_trades(data_dir)
    known = {t["trade_id"] for t in trades}
    events = {t["event_ticker"] for t in trades if t.get("event_ticker")}
    added = 0

    def price(c: Candidate) -> float:
        return c.fill_price if c.fill_price is not None else c.ask

    ranked = sorted(candidates, key=lambda c: (price(c) if c.mirror_key else -price(c), -c.contracts))
    fill_of = {f"{c.ticker}|{c.side}": price(c) for c in candidates if c.depth_status in ("ok", "unchecked")}
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
            "via_mirror": "1" if c.via_mirror else "0",
            "partner_fill": fill_of.get(c.mirror_key, "") if c.mirror_key else "",
        })
    if added:
        save_trades(trades, data_dir)
    return added


# --- games seen (one row per game, so the report can count games that never reached the floor) ---

def load_games(data_dir: Path | None = None) -> list[dict]:
    path = (data_dir or DATA_DIR) / "games.csv"
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def record_games(observed: dict, now: datetime, data_dir: Path | None = None,
                 tz_name: str = "America/Chicago") -> int:
    """Remember every game seen today and the highest ask on any of its contracts.

    `observed` is the scan's `diag["games"]`: event_ticker -> {series, expiry, peak_ask}. A game is added the
    first time it is seen and its peak is raised when a higher ask shows up. The file is only rewritten when
    something changed, so quiet scan cycles add nothing to the commit. Returns rows added or changed.
    """
    if not observed:
        return 0
    path = (data_dir or DATA_DIR) / "games.csv"
    rows = {r["event_ticker"]: r for r in load_games(data_dir)}
    tz, changed = ZoneInfo(tz_name), 0
    stamp = now.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")
    for event, info in observed.items():
        peak = float(info.get("peak_ask") or 0.0)
        row = rows.get(event)
        if row is None:
            expiry = datetime.fromisoformat(info["expiry"])
            rows[event] = {"event_ticker": event, "series": info["series"], "sport": sport_of(info["series"]),
                           "day": expiry.astimezone(tz).date().isoformat(), "expiry": info["expiry"],
                           "first_seen": stamp, "peak_ask": f"{peak:g}"}
            changed += 1
        elif peak > float(row.get("peak_ask") or 0.0):
            row["peak_ask"] = f"{peak:g}"
            changed += 1
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=GAME_FIELDS)
            writer.writeheader()
            for row in sorted(rows.values(), key=lambda r: (r["day"], r["event_ticker"])):
                writer.writerow({k: row.get(k, "") for k in GAME_FIELDS})
        tmp.replace(path)
    return changed
