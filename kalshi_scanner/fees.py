"""Kalshi fee model.

Kalshi's taker fee is, as I understand the published schedule:

    fee = ceil_to_cent(rate * contracts * P * (1 - P))      with P in dollars

The fee is rounded up to the next cent *per order*, not per contract. That means
a 1-contract order at 98c pays a full 1c fee, while a 100-contract order pays
about 0.2c per contract. Fee drag therefore depends heavily on order size.

The rate (0.07 for most markets) can change, so it is a parameter. Verify it
against Kalshi's current fee schedule before trusting any P&L figure.
"""

from __future__ import annotations

import math

DEFAULT_TAKER_RATE = 0.07


def order_fee(price_cents: float, contracts: int = 1, rate: float = DEFAULT_TAKER_RATE) -> float:
    """Total fee in dollars for one order of `contracts` at `price_cents`."""
    p = price_cents / 100.0
    raw_cents = rate * contracts * p * (1.0 - p) * 100.0
    # Subtract a tiny epsilon so float noise never bumps an exact cent up a cent.
    return math.ceil(raw_cents - 1e-9) / 100.0


def win_loss(price_cents: float, contracts: int, rate: float = DEFAULT_TAKER_RATE):
    """Return (net_profit_if_win, net_loss_if_lose, fee) in dollars for a buy at `price_cents`."""
    fee = order_fee(price_cents, contracts, rate)
    gross_win = contracts * (100.0 - price_cents) / 100.0
    stake = contracts * price_cents / 100.0
    return gross_win - fee, stake + fee, fee


def breakeven_probability(price_cents: float, contracts: int, rate: float = DEFAULT_TAKER_RATE) -> float:
    """Win probability needed for zero expected profit after fees."""
    win, loss, _ = win_loss(price_cents, contracts, rate)
    return loss / (win + loss)
