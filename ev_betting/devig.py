"""Remove the bookmaker's margin from a two-way line."""

from __future__ import annotations

import math


def _bisect(f, lo, hi, iters=100):
    flo = f(lo)
    for _ in range(iters):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def multiplicative(q):
    s = sum(q)
    return [x / s for x in q]


def additive(q):
    over = (sum(q) - 1) / len(q)
    p = [max(x - over, 1e-6) for x in q]
    s = sum(p)
    return [x / s for x in p]


def power(q):
    k = _bisect(lambda k: sum(x ** k for x in q) - 1, 1.0, 10.0)
    return [x ** k for x in q]


def shin(q):
    s = sum(q)

    def probs(z):
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z) / (2 * (1 - z)) for x in q]

    z = _bisect(lambda z: sum(probs(z)) - 1, 0.0, 0.4)
    return probs(z)


METHODS = {"multiplicative": multiplicative, "additive": additive, "power": power, "shin": shin}


def conservative_fair(decimal_odds: list[float]) -> tuple[list[float], dict[str, list[float]]]:
    """Per-outcome LOWEST fair probability across all methods, plus each method's result."""
    q = [1 / d for d in decimal_odds]
    results = {}
    for name, fn in METHODS.items():
        try:
            results[name] = fn(q)
        except (ValueError, ZeroDivisionError):
            continue
    fair = [min(r[i] for r in results.values()) for i in range(len(q))]
    return fair, results


def american(p: float) -> str:
    if p <= 0 or p >= 1:
        return ""
    return f"{-100 * p / (1 - p):.0f}" if p >= 0.5 else f"+{100 * (1 - p) / p:.0f}"
