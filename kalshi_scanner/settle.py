"""Resolve open paper trades once Kalshi has settled the underlying market."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .client import KalshiError
from .scanner import parse_ts, price_cents
from .storage import TIER_FILE, load_trades, save_trades

SETTLED_STATUSES = {"settled", "finalized"}
HALF_PAYOUT_C = 50.0   # what each side pays when a match never starts (Kalshi tennis rules)


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
        # Neither side won. Kalshi's sports rules say a match that never starts (walkover, injury, cancellation)
        # resolves every market to $0.50, so a NO bought at 97c loses 47c a contract, not nothing. Use the
        # market's own settlement value when it reports one; otherwise assume the documented 50c and say so
        # in `result` ("half_assumed") so those rows are easy to find and check.
        yes_value = price_cents(market, "settlement_value")
        assumed = yes_value is None
        if assumed:
            yes_value = HALF_PAYOUT_C
        elif yes_value in (0.0, 100.0):
            result = "yes" if yes_value == 100.0 else "no"   # a settlement value that names a winner
        if result in ("yes", "no"):
            return _finish(trade, result, entry, contracts, fee)
        payout = yes_value if trade["side"] == "yes" else 100.0 - yes_value
        pnl = contracts * (payout - entry) / 100.0 - fee
        # Not a win or a loss, so it stays out of hit rates (status "void"), but the money is booked.
        trade.update(status="void", result=result or ("half_assumed" if assumed else "half"), won="",
                     pnl_usd=f"{pnl:.4f}")
        return True

    return _finish(trade, result, entry, contracts, fee)


def _finish(trade: dict, result: str, entry: float, contracts: int, fee: float) -> bool:
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
