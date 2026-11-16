"""Mock-based tests for the ev_betting package (no network)."""

from datetime import datetime, timedelta, timezone

import pytest

from ev_betting import devig, paper
from ev_betting.board import build_board, match_game, name_score
from ev_betting.config import Settings
from ev_betting.run import run

NOW = datetime(2026, 11, 16, 18, 0, tzinfo=timezone.utc)


def odds_event(home, away, start, home_dec, away_dec, updated=NOW - timedelta(minutes=2)):
    ts = updated.isoformat().replace("+00:00", "Z")
    return {"home_team": home, "away_team": away, "commence_time": start.isoformat().replace("+00:00", "Z"),
            "bookmakers": [{"key": "pinnacle", "last_update": ts, "markets": [{"key": "h2h", "last_update": ts,
                            "outcomes": [{"name": home, "price": home_dec}, {"name": away, "price": away_dec}]}]}]}


def mkt(ticker, event, team, ask, end):
    return {"ticker": ticker, "event_ticker": event, "yes_sub_title": team, "title": f"{team} wins",
            "status": "active", "yes_ask_dollars": f"{ask:.4f}", "expected_expiration_time": end.isoformat()}


START = NOW + timedelta(hours=3)
END = START + timedelta(hours=2, minutes=30)
ODDS = [
    odds_event("Los Angeles Lakers", "Boston Celtics", START, 2.15, 1 + 100 / 130),
    odds_event("Los Angeles Clippers", "New York Knicks", START, 1.95, 1.95),
]
MARKETS = [
    mkt("NBA-BOSLAL-BOS", "NBA-BOSLAL", "Boston", 0.56, END),
    mkt("NBA-BOSLAL-LAL", "NBA-BOSLAL", "Los Angeles L", 0.42, END),
    mkt("NBA-NYKLAC-NYK", "NBA-NYKLAC", "New York", 0.50, END),
    mkt("NBA-NYKLAC-LAC", "NBA-NYKLAC", "Los Angeles C", 0.52, END),
]


class FakeClient:
    def __init__(self, markets, results=None, depth=500):
        self.markets, self.results, self.depth = markets, results or {}, depth

    def list_markets(self, **params):
        return [m for m in self.markets if params.get("series_ticker") == "KXNBAGAME"]

    def get_orderbook(self, ticker):
        m = next(m for m in self.markets if m["ticker"] == ticker)
        ask = float(m["yes_ask_dollars"])
        return {"orderbook_fp": {"yes_dollars": [], "no_dollars": [[f"{1 - ask:.4f}", str(self.depth)]]}}

    def get_market(self, ticker):
        return {"ticker": ticker, "status": "finalized" if ticker in self.results else "active",
                "result": self.results.get(ticker, "")}


def cfg(tmp_path):
    return Settings(sports=["nba"], bankroll=350, odds_api_key="x", data_dir=tmp_path)


def test_devig_matches_hand_calc():
    fair, methods = devig.conservative_fair([1 + 100 / 130, 2.15])
    assert methods["multiplicative"][0] == pytest.approx(0.5486, abs=1e-4)
    assert fair[1] == pytest.approx(0.4492, abs=1e-4)   # power method is lowest for the underdog
    for res in methods.values():
        assert sum(res) == pytest.approx(1, abs=1e-9)


def test_name_matching_keeps_la_teams_apart():
    assert name_score("Los Angeles L", "Los Angeles Lakers") == 1.0
    assert name_score("Los Angeles L", "Los Angeles Clippers") < 1.0
    ev, mapping = match_game(MARKETS[:2], ODDS)
    assert ev["home_team"] == "Los Angeles Lakers"
    assert mapping["NBA-BOSLAL-LAL"] == "Los Angeles Lakers"


def test_same_matchup_other_day_not_matched():
    later = [mkt(m["ticker"], m["event_ticker"], m["yes_sub_title"], 0.42, END + timedelta(days=2)) for m in MARKETS[:2]]
    assert match_game(later, ODDS) == (None, None)


def test_board_flags_only_real_edges(tmp_path):
    rows, stats, unmatched, _ = build_board(FakeClient(MARKETS), lambda k: (ODDS, 480), cfg(tmp_path), NOW)
    assert stats["nba"]["matched"] == 2 and not unmatched
    plus = [r for r in rows if r["status"] == "+EV"]
    assert [r["team"] for r in plus] == ["Los Angeles Lakers"]
    assert plus[0]["ev_pct"] == pytest.approx(2.78, abs=0.02)


def test_stale_lines_are_skipped(tmp_path):
    stale = [odds_event("Los Angeles Lakers", "Boston Celtics", START, 2.15, 1.77, updated=NOW - timedelta(hours=1))]
    rows, stats, _, _ = build_board(FakeClient(MARKETS[:2]), lambda k: (stale, 480), cfg(tmp_path), NOW)
    assert stats["nba"]["stale"] == 1
    assert all(r["status"] == "stale line" for r in rows)


def test_full_cycle_open_update_settle(tmp_path):
    c = cfg(tmp_path)
    out = run(c, client=FakeClient(MARKETS), fetch=lambda k: (ODDS, 480), now=NOW)
    assert len(out["new"]) == 1
    t = paper.load(tmp_path)[0]
    assert t["result"] == "Open" and t["venue"] == "Kalshi" and int(t["contracts"]) >= 1
    assert (tmp_path / "STATUS.md").exists() and (tmp_path / "board_latest.csv").exists()

    # second run, line moved toward us before the start: no duplicate trade, closing line updated
    moved = [odds_event("Los Angeles Lakers", "Boston Celtics", START, 2.05, 1.83, updated=NOW + timedelta(hours=1))]
    out2 = run(c, client=FakeClient(MARKETS[:2]), fetch=lambda k: (moved, 470), now=NOW + timedelta(hours=1, minutes=1))
    assert out2["new"] == []
    t = paper.load(tmp_path)[0]
    assert float(t["closing_fair_prob"]) > float(t["fair_prob_at_bet"])

    # after the game: Kalshi reports YES won
    out3 = run(c, client=FakeClient(MARKETS[:2], results={"NBA-BOSLAL-LAL": "yes"}), fetch=lambda k: ([], 469),
               now=START + timedelta(hours=4))
    assert len(out3["settled"]) == 1
    t = paper.load(tmp_path)[0]
    contracts, entry, fee = int(t["contracts"]), float(t["entry_price_c"]), float(t["fee_usd"])
    assert t["result"] == "Win"
    assert float(t["pnl_usd"]) == pytest.approx(contracts * (100 - entry) / 100 - fee, abs=0.01)
    assert float(t["clv_pct"]) > 0


def test_no_key_still_settles_and_reports(tmp_path):
    c = Settings(sports=["nba"], data_dir=tmp_path)
    out = run(c, client=FakeClient(MARKETS), now=NOW)
    assert "ODDS_API_KEY" in out["error"]


def test_thin_book_limits_size(tmp_path):
    c = Settings(sports=["nba"], bankroll=100000, odds_api_key="x", data_dir=tmp_path)
    out = run(c, client=FakeClient(MARKETS, depth=3), fetch=lambda k: (ODDS, 480), now=NOW)
    assert int(out["new"][0]["contracts"]) == 3
