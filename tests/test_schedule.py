from datetime import datetime, timedelta, timezone

from kalshi_scanner.runner import run_loop
from kalshi_scanner.scanner import Config, evaluate
from kalshi_scanner.schedule import ScheduleConfig, make_plan
from kalshi_scanner.storage import load_trades

SC = ScheduleConfig()
T0 = datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc)      # 5:30 PM Central
END = "2026-10-07T01:00:00Z"                                  # 8:00 PM Central, same local day


def at(h, m=0):
    return datetime(2026, 10, 7, h, m, tzinfo=timezone.utc)


def test_plan_idle_when_no_games_or_far_away():
    assert make_plan(T0, [], None, SC).mode == "idle"
    far = make_plan(T0, [at(4)], None, SC)            # window opens ~1 AM UTC, hours away
    assert far.mode == "idle" and far.sleep_s is None


def test_plan_waits_when_a_window_is_about_to_open():
    now = datetime(2026, 10, 6, 21, 45, tzinfo=timezone.utc)
    p = make_plan(now, [at(1)], None, SC)            # window opens 22:00Z, 15 minutes away
    assert p.mode == "waiting" and 30 <= p.sleep_s <= 300


def test_plan_live_then_hot_inside_window():
    assert make_plan(T0, [at(1)], 62.0, SC).sleep_s == SC.live_interval_s
    hot = make_plan(T0, [at(1)], 90.0, SC)
    assert hot.mode == "hot" and hot.sleep_s == SC.hot_interval_s


def test_plan_keeps_watching_after_expected_end_for_overtime():
    assert make_plan(at(1, 45), [at(1)], 70.0, SC).mode == "live"       # 45 min past expected end
    assert make_plan(at(3, 0), [at(1)], 70.0, SC).mode == "idle"        # 2 h past: done


def mk(ask=97, end=END, status="open", ticker="KXNHLGAME-26OCT06AAABBB-AAA"):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "title": "t", "status": status,
            "expected_expiration_time": end, "yes_bid": ask - 1, "yes_ask": ask, "no_bid": 100 - ask,
            "no_ask": 101 - ask, "volume": 100, "open_interest": 50}


def test_open_market_past_expected_end_is_still_scanned():
    now = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
    assert len(evaluate(mk(end="2026-10-06T17:00:00Z"), now, Config())) == 1     # 1 h over: overtime
    assert evaluate(mk(end="2026-10-06T14:00:00Z"), now, Config()) == []         # 4 h over: stale


class LoopClient:
    def __init__(self, markets):
        self.markets = markets
        self.scans = 0

    def list_markets(self, series_ticker, status):
        if series_ticker == "KXNHLGAME":
            self.scans += 1
            return iter([dict(m) for m in self.markets])
        return iter([])

    def get_orderbook(self, ticker):
        return {"orderbook": {"yes": [], "no": [[3, 500]]}}

    def get_market(self, ticker):
        return {"status": "open", "result": ""}


class Clock:
    def __init__(self, start):
        self.now, self.sleeps = start, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def drive(client, clock, minutes, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return run_loop(client, Config(series=["KXNHLGAME"]), SC, max_minutes=minutes,
                    now_fn=clock, sleep_fn=clock.sleep, log=lambda *_: None)


def test_loop_exits_immediately_when_idle(tmp_path, monkeypatch):
    clock = Clock(T0)
    client = LoopClient([mk(end="2026-10-07T06:00:00Z")])     # game ends hours from now
    result = drive(client, clock, 25, tmp_path, monkeypatch)
    assert result["cycles"] == 1 and clock.sleeps == []


def test_loop_scans_fast_when_a_game_is_lopsided_and_trades_once(tmp_path, monkeypatch):
    clock = Clock(T0)
    client = LoopClient([mk(ask=97)])
    result = drive(client, clock, 5, tmp_path, monkeypatch)
    assert set(clock.sleeps) == {SC.hot_interval_s} and result["cycles"] == 10
    assert result["new_trades"] == 1 and len(load_trades(tmp_path / "data")) == 1
    snaps = (tmp_path / "data" / "snapshots" / "2026-10-06.csv").read_text().splitlines()
    assert len(snaps) == 2                                    # header + one throttled row


def test_loop_scans_at_live_pace_when_nothing_is_hot(tmp_path, monkeypatch):
    clock = Clock(T0)
    result = drive(LoopClient([mk(ask=62)]), clock, 5, tmp_path, monkeypatch)
    assert set(clock.sleeps) == {SC.live_interval_s} and result["cycles"] == 5


def test_loop_waits_for_an_upcoming_window(tmp_path, monkeypatch):
    clock = Clock(datetime(2026, 10, 6, 21, 45, tzinfo=timezone.utc))   # window opens at 22:00Z
    result = drive(LoopClient([mk(ask=62)]), clock, 25, tmp_path, monkeypatch)
    assert clock.sleeps[0] == 300 and result["cycles"] > 1
