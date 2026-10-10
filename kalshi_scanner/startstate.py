"""Did the game actually start before a paper trade was opened?

Kalshi sports markets only expose when a game is *expected to end*, not when play began, so a 97c
favorite can be a late-game lead or a pre-match price hours before the first ball. Those are different
bets (and a match that never starts can resolve to 50c instead of paying 100c or 0c), so each trade is
labelled with the best evidence available when it was opened. This is inference, not a live feed:

    prematch_certain  entered at least PREMATCH_CERTAIN_MIN minutes before the listed end. No game in
                      the scan lasts that long, so play cannot have started. Needs nothing but two
                      timestamps, so it can be computed for old trades too.
    live_inferred     the game's top ask had risen by LIVE_MOVE_C or more since the scanner first saw
                      the game. A favorite does not usually climb 8c+ without play (or news).
    prematch_likely   at least PREMATCH_LIKELY_MIN minutes of lead and the top ask has barely moved
                      (under FLAT_MOVE_C) since first sighting. Probably a pre-match price.
    unknown           everything else, including trades with no first-sighting record.

The label is stored on the trade at entry (trades.csv `start_state`) so later price moves cannot leak
into it. Trades opened before this existed are labelled from lead time alone: they can be
prematch_certain or unknown, never live_inferred.
"""

from __future__ import annotations

from datetime import datetime

from .scanner import parse_ts

PREMATCH_CERTAIN_MIN = 240    # minutes of lead before the listed end that rules out a game in progress
PREMATCH_LIKELY_MIN = 90      # minutes of lead that, with a flat price, suggests it has not started
LIVE_MOVE_C = 8.0             # cents the top ask must have climbed since first sighting to infer play
FLAT_MOVE_C = 3.0             # cents of movement or less counts as "flat"

LIVE, UNKNOWN, LIKELY, CERTAIN = "live_inferred", "unknown", "prematch_likely", "prematch_certain"
STATE_ORDER = [LIVE, UNKNOWN, LIKELY, CERTAIN]
STATE_LABELS = {
    LIVE: "live (price climbed since first seen)",
    UNKNOWN: "unknown",
    LIKELY: "probably pre-match (flat price, 90+ min out)",
    CERTAIN: "pre-match for sure (4h+ before end)",
}


def _num(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def lead_minutes(entered_at: datetime | None, expiry: datetime | None):
    """Minutes from entry to the game's listed end (negative once the listed end has passed)."""
    if entered_at is None or expiry is None:
        return None
    return (expiry - entered_at).total_seconds() / 60.0


def classify(entered_at: datetime | None, expiry: datetime | None,
             first_ask: float | None = None, peak_ask: float | None = None) -> str:
    """Label one entry. `first_ask` is the game's top ask when the scanner first saw it, `peak_ask` the
    highest top ask seen up to the moment of entry. Pass None for both when no game record exists."""
    lead = lead_minutes(entered_at, expiry)
    if lead is None:
        return UNKNOWN
    if lead >= PREMATCH_CERTAIN_MIN:
        return CERTAIN
    if first_ask is None or peak_ask is None:
        return UNKNOWN
    move = peak_ask - first_ask
    if move >= LIVE_MOVE_C:
        return LIVE
    if lead >= PREMATCH_LIKELY_MIN and move < FLAT_MOVE_C:
        return LIKELY
    return UNKNOWN


def classify_candidate(cand, game: dict | None) -> str:
    """Label a candidate that is about to become a trade, from its game's row in games.csv (or None)."""
    first = _num(game.get("first_ask")) if game else None
    peak = _num(game.get("peak_ask")) if game else None
    return classify(parse_ts(cand.ts), parse_ts(cand.expiry), first, peak)


def state_of(trade: dict) -> str:
    """The stored label, or a lead-time-only label for trades opened before labels were recorded."""
    stored = (trade.get("start_state") or "").strip()
    if stored in STATE_ORDER:
        return stored
    return classify(parse_ts(trade.get("first_seen")), parse_ts(trade.get("expiry")))
