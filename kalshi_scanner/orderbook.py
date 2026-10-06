"""Order-book depth: can the paper order really fill, and at what average price?

Kalshi's order book lists *bids* for YES and for NO. Buying YES at price `a` matches a
NO bid at `100 - a`, and vice versa, so the asks for one side are the opposite side's bids
flipped around 100.
"""

from __future__ import annotations


def _levels(raw, dollars: bool) -> list[tuple[float, float]]:
    out = []
    for row in raw or []:
        try:
            price, qty = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        out.append((round(price * 100.0, 4) if dollars else price, qty))
    return out


def parse_book(payload: dict | None):
    """Return {"yes": [(price_c, qty)...], "no": [...]} of bids, or None if the format is unrecognized.

    Handles the classic `orderbook` (cents) and the newer `orderbook_fp` (dollar strings).
    An empty book parses fine (empty lists); only an unrecognizable response returns None.
    """
    if not isinstance(payload, dict):
        return None
    book = payload.get("orderbook")
    if isinstance(book, dict) and ("yes" in book or "no" in book):
        return {"yes": _levels(book.get("yes"), False), "no": _levels(book.get("no"), False)}
    fp = payload.get("orderbook_fp")
    if isinstance(fp, dict) and ("yes_dollars" in fp or "no_dollars" in fp):
        return {"yes": _levels(fp.get("yes_dollars"), True), "no": _levels(fp.get("no_dollars"), True)}
    return None


def fill_estimate(book: dict, side: str, contracts: int, max_price: float) -> dict:
    """Walk the asks for `side`, cheapest first, never paying more than `max_price` cents.

    Fills as many whole contracts as the book allows, up to `contracts`. Returns the size
    at the best ask, how many contracts could fill, and the average fill price for exactly
    those contracts (None if nothing could fill within `max_price`).
    """
    opposing = book["no" if side == "yes" else "yes"]
    asks = sorted(((100.0 - price, qty) for price, qty in opposing), key=lambda level: level[0])
    depth_at_best = asks[0][1] if asks else 0.0

    available = sum(qty for price, qty in asks if price <= max_price)
    filled = int(min(float(contracts), available) + 1e-9)
    if filled <= 0:
        return {"depth_at_ask": depth_at_best, "filled": 0, "vwap": None}

    remaining, cost = float(filled), 0.0
    for price, qty in asks:
        if price > max_price or remaining <= 1e-9:
            break
        take = min(qty, remaining)
        cost += take * price
        remaining -= take
    return {"depth_at_ask": depth_at_best, "filled": filled, "vwap": cost / filled}
