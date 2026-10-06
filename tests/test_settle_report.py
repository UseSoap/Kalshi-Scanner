from datetime import datetime, timezone

from kalshi_scanner import combos, report
from kalshi_scanner.scanner import Config, evaluate
from kalshi_scanner.settle import settle_open_trades, settle_trade
from kalshi_scanner.storage import load_trades, log_snapshots, record_new_trades

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)


def mk_market(ticker="KXNBAGAME-26OCT06LALDEN-LAL", event="KXNBAGAME-26OCT06LALDEN", ask=97):
    return {"ticker": ticker, "event_ticker": event, "title": "t", "status": "open",
            "expected_expiration_time": "2026-10-07T02:30:00Z", "yes_bid": ask - 1, "yes_ask": ask,
            "no_bid": 100 - ask, "no_ask": 101 - ask, "volume": 10, "open_interest": 5}


class SettleClient:
    def __init__(self, results):
        self.results = results

    def get_market(self, ticker):
        return {"ticker": ticker, "status": "settled", "result": self.results[ticker]}


def test_trade_lifecycle(tmp_path):
    cands = evaluate(mk_market(), NOW, Config())
    log_snapshots(cands, NOW, tmp_path)
    assert (tmp_path / "snapshots" / "2026-10-06.csv").exists()
    assert record_new_trades(cands, tmp_path) == 1
    assert record_new_trades(cands, tmp_path) == 0   # no duplicate paper trade

    # Game not over yet: nothing to settle.
    assert settle_open_trades(SettleClient({}), NOW, tmp_path) == 0

    assert settle_open_trades(SettleClient({"KXNBAGAME-26OCT06LALDEN-LAL": "yes"}), AFTER, tmp_path) == 1
    t = load_trades(tmp_path)[0]
    assert t["status"] == "settled" and t["won"] == "1"
    assert abs(float(t["pnl_usd"]) - (100 * 0.03 - 0.21)) < 1e-6


def test_loss_pnl_includes_stake_and_fee():
    t = {"entry_price": "97", "contracts": "100", "fee_usd": "0.21", "side": "yes"}
    assert settle_trade(t, {"status": "settled", "result": "no"}, AFTER)
    assert t["won"] == "0" and abs(float(t["pnl_usd"]) - (-97.0 - 0.21)) < 1e-6


def test_unsettled_market_is_left_alone():
    t = {"entry_price": "97", "contracts": "100", "fee_usd": "0.21", "side": "yes"}
    assert not settle_trade(t, {"status": "closed", "result": ""}, AFTER)


def trade(event, price, won, expiry="2026-10-07T02:30:00Z"):
    return {"status": "settled", "won": "1" if won else "0", "entry_price": str(price), "contracts": "100",
            "fee_usd": "0.2", "pnl_usd": "1.0", "series": "KXMLBGAME", "event_ticker": event, "expiry": expiry}


def test_report_buckets_and_summary(tmp_path):
    trades = [trade("e1", 97, True), trade("e2", 97.5, False), trade("e3", 99, True)]
    s = report.summarize(trades)
    assert s["n"] == 3 and s["wins"] == 2
    buckets = dict(report.by_bucket(trades))
    assert buckets["97-98c"]["n"] == 2 and buckets["99-100c"]["n"] == 1
    lo, hi = report.wilson_interval(2, 3)
    assert 0 < lo < 2 / 3 < hi < 1


def test_combo_uses_one_leg_per_event_and_hits_target():
    legs = [trade(f"e{i}", 97, True) for i in range(30)] + [trade("e0", 98, True)]
    combo = combos.build_combo(legs, 0.5)
    assert combo is not None
    assert len({leg["event_ticker"] for leg in combo}) == len(combo)
    prob = 1.0
    for leg in combo:
        prob *= float(leg["entry_price"]) / 100
    assert 0.4 <= prob <= 0.5


def test_combo_skipped_when_not_enough_legs():
    assert combos.build_combo([trade("e1", 97, True), trade("e2", 97, True)], 0.5) is None


def test_combo_simulation_loses_if_any_leg_loses():
    legs = [trade(f"e{i}", 97, i != 3) for i in range(30)]
    results = combos.simulate(legs, 0.5)
    assert len(results) == 1 and results[0]["won"] is False and results[0]["pnl_per_dollar"] == -1.0
