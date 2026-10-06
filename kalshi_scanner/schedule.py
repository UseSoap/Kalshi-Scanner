"""Decide how often to scan, based on where today's games are in their schedule.

Kalshi sports markets expose when a game is *expected to end*, not when it starts. Near-certain
(95%+) prices show up in the back half of a game, so each game gets a "watch window":

    [expected_end - window_before, expected_end + window_after]

Inside a window we scan every `live_interval_s`; if any contract is already priced at
`hot_ask` or higher (a game is getting lopsided) we scan every `hot_interval_s`. Between
windows we wait if the next one is close, and otherwise report "idle" so the loop can exit
and stop using compute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ScheduleConfig:
    window_before_min: int = 180    # start watching this long before the expected end (MLB runs ~3h)
    window_after_min: int = 90      # keep watching after it: overtime / extra innings run long
    lookahead_min: int = 30         # stay alive for a window that opens within this long
    hot_ask: float = 85.0           # cents; any contract this high means a game is getting lopsided
    hot_interval_s: int = 30
    live_interval_s: int = 60
    wait_interval_s: int = 300


@dataclass
class Plan:
    mode: str                 # hot | live | waiting | idle
    sleep_s: int | None       # None means "nothing to do, exit"
    reason: str


def make_plan(now: datetime, expiries, max_ask: float | None, sc: ScheduleConfig) -> Plan:
    before = timedelta(minutes=sc.window_before_min)
    after = timedelta(minutes=sc.window_after_min)
    expiries = sorted(set(expiries))

    in_window = [e for e in expiries if e - before <= now <= e + after]
    if in_window:
        if max_ask is not None and max_ask >= sc.hot_ask:
            return Plan("hot", sc.hot_interval_s, f"{len(in_window)} game windows open, top ask {max_ask:g}c")
        return Plan("live", sc.live_interval_s, f"{len(in_window)} game windows open")

    upcoming = [e - before for e in expiries if e - before > now]
    if upcoming:
        wait = (min(upcoming) - now).total_seconds()
        if wait <= sc.lookahead_min * 60:
            return Plan("waiting", int(max(30, min(wait, sc.wait_interval_s))),
                        f"next window opens in {int(wait // 60)} min")
        return Plan("idle", None, f"next window opens in {int(wait // 60)} min")
    return Plan("idle", None, "no games ahead")
