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


def trade(event, price, won, expiry="2026-10-07T02:30:00Z", series="KXMLBGAME", contracts="100", status="settled"):
    return {"status": status, "won": "1" if won else "0", "entry_price": str(price), "contracts": contracts,
            "fee_usd": "0.2", "pnl_usd": "1.0", "series": series, "event_ticker": event, "expiry": expiry}


def test_report_buckets_and_summary(tmp_path):
    trades = [trade("e1", 97, True), trade("e2", 97.5, False), trade("e3", 99, True)]
    s = report.summarize(trades)
    assert s["n"] == 3 and s["wins"] == 2
    buckets = dict(report.by_bucket(trades))
    assert buckets["97-98c"]["n"] == 2 and buckets["99-100c"]["n"] == 1
    assert "<90c" not in buckets                                   # empty under-90 bucket is hidden
    low = dict(report.by_bucket([trade("e1", 91, True), trade("e2", 94, True), trade("e3", 89.5, False)]))
    assert low["90-93c"]["n"] == 1 and low["93-95c"]["n"] == 1 and low["<90c"]["n"] == 1
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


def sample_trades():
    return [
        trade("a1", 91.2, True, series="KXMLBGAME", contracts="40"),
        trade("a2", 96.5, True, series="KXMLBGAME"),
        trade("a3", 96.9, False, series="KXMLBGAME", contracts="60"),
        trade("b1", 92.0, True, series="KXNHLGAME", contracts="20"),
        trade("c1", 98.4, True, series="KXEPLGAME", status="open"),
    ]


def test_report_groups_by_sport_and_bucket():
    done = report.settled(sample_trades())
    sports = dict(report.by_sport(done))
    assert sports["MLB"]["n"] == 3 and sports["NHL"]["n"] == 1 and "Premier League" not in sports
    nested = dict(report.by_sport_and_bucket(done))
    assert dict(nested["MLB"])["96-97c"]["n"] == 2 and dict(nested["MLB"])["90-93c"]["n"] == 1
    assert [label for label, _ in nested["NHL"]] == ["90-93c"]           # only non-empty buckets listed


def test_sport_uses_the_stored_column_when_present():
    t = trade("a", 97, True, series="KXMLBGAME")
    assert report.sport_label(t) == "MLB"
    t["sport"] = "Custom"
    assert report.sport_label(t) == "Custom"


def test_fills_table_counts_open_and_settled_with_average_size():
    rows = report.fills_table(sample_trades())
    assert rows[0].split()[:3] == ["Sport", "90-93c", "93-95c"]
    mlb = next(r for r in rows if r.startswith("MLB"))
    assert mlb.split()[-2:] == ["3", "67"]                                  # 3 fills, (40+100+60)/3 contracts
    epl = next(r for r in rows if r.startswith("Premier League"))
    assert "98-99c" not in epl and epl.split()[-2:] == ["1", "100"]
    total = rows[-1].split()
    assert total[0] == "Total" and total[-2:] == ["5", "64"]                # (40+100+60+20+100)/5


def test_render_shows_fills_before_anything_settles(tmp_path):
    from kalshi_scanner.storage import save_trades, TRADE_FIELDS
    open_trade = {k: "" for k in TRADE_FIELDS}
    open_trade.update(trade_id="x|yes", ticker="x", event_ticker="e", series="KXNBAGAME", sport="NBA",
                      entry_price="96.0", contracts="25", fee_usd="0.1", status="open", expiry="2026-10-07T02:30:00Z")
    save_trades([open_trade], tmp_path)
    text = report.render(tmp_path)
    assert "Fills by sport and entry price" in text and "NBA" in text and "Nothing settled yet" in text


def test_render_full_report_has_sport_and_price_sections(tmp_path):
    from kalshi_scanner.storage import save_trades, TRADE_FIELDS
    rows = []
    for i, t in enumerate(sample_trades()):
        row = {k: "" for k in TRADE_FIELDS}
        row.update(t, trade_id=f"t{i}", ticker=f"t{i}", pnl_usd=t["pnl_usd"], result="yes")
        rows.append(row)
    save_trades(rows, tmp_path)
    text = report.render(tmp_path)
    for expected in ("By entry price:", "By sport:", "By sport and entry price:", "MLB", "NHL", "96-97c"):
        assert expected in text
