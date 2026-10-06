from datetime import datetime, timezone

from kalshi_scanner.fees import breakeven_probability, order_fee, win_loss
from kalshi_scanner.scanner import Config, evaluate, price_cents, scan

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)  # 1:00 PM Central
TONIGHT = "2026-10-07T02:30:00Z"                          # 9:30 PM Central, same local day
TOMORROW = "2026-10-08T02:30:00Z"                         # next local day


def market(**over):
    base = {"ticker": "KXMLBGAME-26OCT06NYYBOS-NYY", "event_ticker": "KXMLBGAME-26OCT06NYYBOS",
            "title": "Yankees win?", "status": "open", "expected_expiration_time": TONIGHT,
            "yes_bid": 96, "yes_ask": 97, "no_bid": 3, "no_ask": 4, "volume": 5000, "open_interest": 900}
    base.update(over)
    return base


def test_fee_is_rounded_up_per_order():
    assert order_fee(97, 1) == 0.01          # 0.0020 -> 1 cent minimum
    assert order_fee(97, 100) == 0.21        # 0.2037 -> 21 cents total
    assert order_fee(50, 100) == 1.75        # 0.07 * 100 * 0.25 = 1.75 exactly, no extra cent


def test_one_contract_at_99c_has_no_profit_after_fee():
    win, loss, fee = win_loss(99, 1)
    assert fee == 0.01 and round(win, 4) == 0.0
    assert breakeven_probability(99, 1) > 0.99


def test_larger_orders_cut_fee_drag():
    assert breakeven_probability(97, 100) < breakeven_probability(97, 1)


def test_picks_the_favored_side_only():
    cands = evaluate(market(), NOW, Config())
    assert [(c.side, c.ask) for c in cands] == [("yes", 97.0)]
    assert cands[0].spread == 1.0


def test_no_side_favorite_is_found():
    m = market(yes_bid=3, yes_ask=4, no_bid=96, no_ask=97)
    assert [(c.side, c.ask) for c in evaluate(m, NOW, Config())] == [("no", 97.0)]


def test_derives_no_ask_from_yes_bid_when_missing():
    m = market(yes_bid=3, yes_ask=4)
    del m["no_ask"], m["no_bid"]
    assert [(c.side, c.ask) for c in evaluate(m, NOW, Config())] == [("no", 97.0)]


def test_prefers_subpenny_dollar_fields():
    assert price_cents({"yes_ask": 97, "yes_ask_dollars": "0.9725"}, "yes_ask") == 97.25


def test_rejects_other_day_closed_wide_spread_and_out_of_range():
    assert evaluate(market(expected_expiration_time=TOMORROW), NOW, Config()) == []
    assert evaluate(market(status="closed"), NOW, Config()) == []
    assert evaluate(market(yes_bid=90), NOW, Config()) == []             # 7c spread
    assert evaluate(market(yes_ask=94, yes_bid=93), NOW, Config()) == []  # below 95c
    assert evaluate(market(yes_ask=100, yes_bid=99), NOW, Config()) == []  # above 99c
    assert evaluate(market(expected_expiration_time="2026-10-06T12:00:00Z"), NOW, Config()) == []  # already ended


def test_falls_back_to_close_time():
    m = market(close_time=TONIGHT)
    del m["expected_expiration_time"]
    assert len(evaluate(m, NOW, Config())) == 1


def test_any_day_flag():
    assert len(evaluate(market(expected_expiration_time=TOMORROW), NOW, Config(same_day_only=False))) == 1


class FakeClient:
    def __init__(self, by_series):
        self.by_series = by_series

    def list_markets(self, series_ticker, status):
        return iter(self.by_series.get(series_ticker, []))


def test_scan_walks_each_series_and_dedupes():
    m = market()
    client = FakeClient({"KXMLBGAME": [m, m], "KXNHLGAME": []})
    found = scan(client, Config(series=["KXMLBGAME", "KXNHLGAME"]), NOW)
    assert len(found) == 1 and found[0].series == "KXMLBGAME"
