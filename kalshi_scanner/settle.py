"""Resolve open paper trades once Kalshi has settled the underlying market."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .client import KalshiError
from .scanner import parse_ts
from .storage import TIER_FILE, load_trades, save_trades

SETTLED_STATUSES = {"settled", "finalized"}


def settle_trade(trade: dict, market: dict, now: datetime) -> bool:
    """Update `trade` in place if `market` is settled. Returns True if it changed."""
    status = str(market.get("status", "")).lower()
    result = str(market.get("result", "")).lower()
    if status not in SETTLED_STATUSES and result not in ("yes", "no"):
        return False

    entry = float(trade["entry_price"])
    contracts = int(float(trade["contracts"]))
    fee = float(trade["fee_usd"])
    trade["settled_at"] = now.astimezone(timezone.utc).isoformat(timespec="seconds")

    if result not in ("yes", "no"):
        # Voided / unresolved-style settlement: treat as no P&L.
        trade.update(status="void", result=result, won="", pnl_usd="0.0")
        return True

    won = result == trade["side"]
    if won:
        pnl = contracts * (100.0 - entry) / 100.0 - fee
    else:
        pnl = -(contracts * entry / 100.0) - fee
    trade.update(status="settled", result=result, won="1" if won else "0", pnl_usd=f"{pnl:.4f}")
    return True


def settle_open_trades(client, now: datetime | None = None, data_dir: Path | None = None) -> int:
    """Check every open trade whose expected end time has passed. Returns count settled.

    Covers the main trades and the separate second-tier trades (trades_95.csv); each file is
    saved on its own, so one never alters the other.
    """
    now = now or datetime.now(timezone.utc)
    changed = 0
    for name in ("trades.csv", TIER_FILE):
        trades = load_trades(data_dir, name)
        resolved = 0
        for trade in trades:
            if trade["status"] != "open":
                continue
            expiry = parse_ts(trade["expiry"])
            if expiry and expiry > now:
                continue
            try:
                market = client.get_market(trade["ticker"])
            except KalshiError:
                continue
            if settle_trade(trade, market, now):
                resolved += 1
        if resolved:
            save_trades(trades, data_dir, name)
        changed += resolved
    return changed
