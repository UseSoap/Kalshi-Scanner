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
    assert evaluate(market(yes_ask=89, yes_bid=88), NOW, Config()) == []  # below 90c
    assert evaluate(market(yes_ask=100, yes_bid=99), NOW, Config()) == []  # above 99c
    assert evaluate(market(expected_expiration_time="2026-10-06T12:00:00Z"), NOW, Config()) == []  # already ended


def test_falls_back_to_close_time():
    m = market(close_time=TONIGHT)
    del m["expected_expiration_time"]
    assert len(evaluate(m, NOW, Config())) == 1


def test_ninety_cent_floor_and_five_cent_spread_are_accepted():
    assert [c.ask for c in evaluate(market(yes_ask=90, yes_bid=88), NOW, Config())] == [90.0]
    assert len(evaluate(market(yes_ask=97, yes_bid=92), NOW, Config())) == 1      # exactly 5c
    assert evaluate(market(yes_ask=97, yes_bid=91), NOW, Config()) == []          # 6c


def test_any_day_flag():
    assert len(evaluate(market(expected_expiration_time=TOMORROW), NOW, Config(same_day_only=False))) == 1


# NO bids at 3c and 2c mean YES can be bought at 97c (deep) and 98c.
DEEP_BOOK = {"orderbook": {"yes": [[96, 500]], "no": [[3, 400], [2, 400]]}}


class FakeClient:
    def __init__(self, by_series, book=DEEP_BOOK):
        self.by_series = by_series
        self.book = book

    def list_markets(self, series_ticker, status):
        return iter(self.by_series.get(series_ticker, []))

    def get_orderbook(self, ticker):
        return self.book


def test_scan_walks_each_series_and_dedupes():
    m = market()
    client = FakeClient({"KXMLBGAME": [m, m], "KXNHLGAME": []})
    found = scan(client, Config(series=["KXMLBGAME", "KXNHLGAME"]), NOW)
    assert len(found) == 1 and found[0].series == "KXMLBGAME"


def test_diagnostics_explain_rejections():
    from kalshi_scanner.scanner import new_diag
    diag = new_diag()
    markets = [market(), market(ticker="B", expected_expiration_time=TOMORROW),
               market(ticker="C", yes_bid=60, yes_ask=62, no_bid=38, no_ask=40)]
    client = FakeClient({"KXMLBGAME": markets})
    scan(client, Config(series=["KXMLBGAME"]), NOW, diag)
    assert diag["per_series"] == {"KXMLBGAME": 3}
    assert diag["same_day"] == 2
    assert diag["reasons"]["ends on another day"] == 1
    assert diag["reasons"]["ask outside price range"] >= 2
    assert max(diag["top"])[0] == 97.0


# ---- order-book depth ----

from kalshi_scanner.orderbook import fill_estimate, parse_book
from kalshi_scanner.scanner import apply_depth


def test_parse_book_cents_and_dollar_formats_and_unknown():
    cents = parse_book({"orderbook": {"yes": [[96, 10]], "no": [[3, 5]]}})
    dollars = parse_book({"orderbook_fp": {"yes_dollars": [["0.9600", "10.00"]], "no_dollars": [["0.0300", "5.00"]]}})
    assert cents == dollars == {"yes": [(96.0, 10.0)], "no": [(3.0, 5.0)]}
    assert parse_book({"orderbook": {"yes": None, "no": None}}) == {"yes": [], "no": []}   # empty book is readable
    assert parse_book({"something": "else"}) is None and parse_book(None) is None


def test_fill_estimate_walks_levels_and_averages():
    book = {"yes": [], "no": [(3.0, 60.0), (2.0, 100.0)]}      # YES asks: 60 @ 97c, 100 @ 98c
    est = fill_estimate(book, "yes", 100, 99.0)
    assert est["depth_at_ask"] == 60.0 and est["filled"] == 100
    assert abs(est["vwap"] - (60 * 97 + 40 * 98) / 100) < 1e-9


def test_fill_estimate_fills_the_largest_size_available():
    book = {"yes": [], "no": [(3.0, 60.0), (2.0, 100.0)]}
    est = fill_estimate(book, "yes", 500, 99.0)                  # asks for more than exists
    assert est["filled"] == 160
    assert abs(est["vwap"] - (60 * 97 + 100 * 98) / 160) < 1e-9
    est = fill_estimate(book, "yes", 100, 97.0)                  # price cap excludes the 98c level
    assert est["filled"] == 60 and est["vwap"] == 97.0
    est = fill_estimate(book, "yes", 100, 96.0)                  # nothing within the cap
    assert est["filled"] == 0 and est["vwap"] is None
    assert fill_estimate({"yes": [], "no": []}, "yes", 100, 99.0) == {"depth_at_ask": 0.0, "filled": 0, "vwap": None}
    assert fill_estimate({"yes": [], "no": [(3.0, 7.9)]}, "yes", 100, 99.0)["filled"] == 7   # whole contracts only


def test_no_side_uses_yes_bids():
    book = {"yes": [(96.0, 100.0)], "no": []}                  # NO can be bought at 4c, not 97c
    assert fill_estimate(book, "no", 10, 99.0)["vwap"] == 4.0


def candidate(client_book):
    cand = evaluate(market(), NOW, Config())[0]
    return apply_depth(FakeClient({}, client_book), cand, Config())


def test_apply_depth_reprices_to_average_fill():
    cand = candidate({"orderbook": {"yes": [], "no": [[3, 60], [2, 100]]}})
    assert cand.depth_status == "ok" and cand.depth_at_ask == 60.0
    assert cand.contracts == 100 and cand.fillable == 100
    assert abs(cand.fill_price - 97.4) < 1e-9
    assert cand.fee_usd == order_fee(97.4, 100)


def test_apply_depth_sizes_the_order_to_what_can_fill():
    cand = candidate({"orderbook": {"yes": [], "no": [[3, 37]]}})        # only 37 contracts at 97c
    assert cand.depth_status == "ok" and cand.contracts == 37 and cand.fillable == 37
    assert cand.fill_price == 97.0
    assert cand.fee_usd == order_fee(97.0, 37)
    capped = candidate({"orderbook": {"yes": [], "no": [[3, 5000]]}})
    assert capped.contracts == 100                                         # never above Config.contracts


def test_slippage_limit_stops_the_walk():
    # Quoted ask 97c, so the limit is 99c at the default 2c slippage and both levels are reachable.
    # With 1c slippage the limit is 98c, which excludes the 99c level.
    book = {"orderbook": {"yes": [], "no": [[3, 10], [1, 50]]}}            # 10 @ 97c, 50 @ 99c
    assert candidate(book).contracts == 60
    cand = evaluate(market(), NOW, Config(max_slippage=1.0))[0]
    apply_depth(FakeClient({}, book), cand, Config(max_slippage=1.0))
    assert cand.contracts == 10 and cand.fill_price == 97.0


def test_thin_and_unreadable_books_are_flagged():
    assert candidate({"orderbook": {"yes": [], "no": [[3, 4]]}}).depth_status == "thin"   # under min_contracts (5)
    assert candidate({"orderbook": {"yes": [], "no": [[3, 5]]}}).depth_status == "ok"     # exactly min_contracts
    thin = candidate({"orderbook": {"yes": [], "no": [[3, 4]]}})
    assert thin.fillable == 4 and thin.fill_price is None
    assert candidate({"unexpected": 1}).depth_status == "unknown"


def test_only_fillable_candidates_become_paper_trades(tmp_path):
    from kalshi_scanner.storage import load_trades, record_new_trades
    good = candidate({"orderbook": {"yes": [], "no": [[3, 500]]}})
    thin = candidate({"orderbook": {"yes": [], "no": [[3, 4]]}})
    thin.ticker = "THIN"
    unreadable = candidate({"x": 1})
    unreadable.ticker = "UNREAD"
    assert record_new_trades([good, thin, unreadable], tmp_path) == 1
    trade = load_trades(tmp_path)[0]
    assert trade["entry_price"] == "97.0" and trade["depth_at_ask"] == "500.0"
    assert trade["contracts"] == "100" and trade["sport"] == "MLB"


def test_trade_is_sized_to_the_fill_and_fee_matches(tmp_path):
    from kalshi_scanner.storage import load_trades, record_new_trades
    record_new_trades([candidate({"orderbook": {"yes": [], "no": [[3, 37]]}})], tmp_path)
    trade = load_trades(tmp_path)[0]
    assert trade["contracts"] == "37" and float(trade["fee_usd"]) == order_fee(97.0, 37)


def test_scan_reports_thin_books_in_diagnostics():
    from kalshi_scanner.scanner import new_diag
    diag = new_diag()
    client = FakeClient({"KXMLBGAME": [market()]}, {"orderbook": {"yes": [], "no": [[3, 4]]}})
    found = scan(client, Config(series=["KXMLBGAME"]), NOW, diag)
    assert found[0].depth_status == "thin"
    assert diag["reasons"]["thin book (under 5 contracts fillable)"] == 1


def test_sport_labels_and_default_leagues():
    from kalshi_scanner.scanner import DEFAULT_SERIES, sport_of
    assert sport_of("KXMLBGAME") == "MLB" and sport_of("kxnflgame") == "NFL"
    assert sport_of("KXCRICKETMATCH") == "CRICKET" and sport_of("KXFOOBAR") == "FOOBAR"
    assert {"KXMLBGAME", "KXNHLGAME", "KXNBAGAME", "KXNFLGAME", "KXNCAAFGAME", "KXEPLGAME",
            "KXATPMATCH", "KXUFCFIGHT"} <= set(DEFAULT_SERIES)
    assert len(DEFAULT_SERIES) == len(set(DEFAULT_SERIES))


# ---- one paper trade per game ----

def soccer_candidates(*asks):
    """Candidates for different contracts of one game (e.g. team A / draw / team B)."""
    out = []
    for i, ask in enumerate(asks):
        m = market(ticker=f"KXMLSGAME-26OCT06CHIVAN-X{i}", event_ticker="KXMLSGAME-26OCT06CHIVAN",
                   yes_ask=ask, yes_bid=ask - 1, no_bid=100 - ask, no_ask=101 - ask)
        cand = evaluate(m, NOW, Config())[0]
        apply_depth(FakeClient({}, {"orderbook": {"yes": [], "no": [[100 - ask, 500]]}}), cand, Config())
        out.append(cand)
    return out


def test_one_trade_per_game_takes_the_highest_priced_contract(tmp_path):
    from kalshi_scanner.storage import load_trades, record_new_trades
    cands = soccer_candidates(92, 97, 94)             # three contracts, same game
    assert record_new_trades(cands, tmp_path) == 1
    trades = load_trades(tmp_path)
    assert [t["ticker"] for t in trades] == ["KXMLSGAME-26OCT06CHIVAN-X1"]     # the 97c one


def test_a_game_with_a_trade_ignores_later_contracts(tmp_path):
    from kalshi_scanner.storage import load_trades, record_new_trades
    first, later = soccer_candidates(92, 98)
    assert record_new_trades([first], tmp_path) == 1
    assert record_new_trades([later], tmp_path) == 0                           # same game, even at a better price
    assert len(load_trades(tmp_path)) == 1 and load_trades(tmp_path)[0]["ticker"].endswith("X0")


def test_different_games_still_each_get_a_trade(tmp_path):
    from kalshi_scanner.storage import record_new_trades
    a, = soccer_candidates(97)
    b, = soccer_candidates(97)
    b.ticker, b.event_ticker = "KXMLSGAME-26OCT06OTHER-X0", "KXMLSGAME-26OCT06OTHER"
    assert record_new_trades([a, b], tmp_path) == 2


def test_one_per_game_can_be_switched_off(tmp_path):
    from kalshi_scanner.storage import record_new_trades
    assert record_new_trades(soccer_candidates(92, 97, 94), tmp_path, one_per_event=False) == 3


def test_scan_cycle_applies_the_config_flag(tmp_path, monkeypatch):
    from kalshi_scanner import storage
    from kalshi_scanner.runner import scan_cycle
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    markets = [market(ticker=f"T{i}", event_ticker="SAME-GAME", yes_ask=a, yes_bid=a - 1, no_bid=100 - a, no_ask=101 - a)
               for i, a in enumerate((93, 96))]
    client = FakeClient({"KXMLBGAME": markets}, {"orderbook": {"yes": [], "no": [[3, 500], [7, 500]]}})
    _, _, added = scan_cycle(client, Config(series=["KXMLBGAME"]), NOW)
    assert added == 1
    _, _, added = scan_cycle(client, Config(series=["KXMLBGAME"], one_trade_per_event=False), NOW)
    assert added == 1                                                           # the other contract, now allowed


# ---- the two sides of a two-team game, and the cheaper-side rule ----

EVENT = "KXMLBGAME-26OCT06NYYBOS"


def pair(a_yes_ask, b_no_ask, series="KXMLBGAME", event=EVENT):
    """Both markets of one game. 'A wins' YES and 'B wins' NO are the same bet, in separate books."""
    a = market(ticker=f"{event}-NYY", event_ticker=event, yes_bid=a_yes_ask - 1, yes_ask=a_yes_ask,
               no_bid=100 - a_yes_ask, no_ask=101 - a_yes_ask)
    b = market(ticker=f"{event}-BOS", event_ticker=event, no_bid=b_no_ask - 1, no_ask=b_no_ask,
               yes_bid=100 - b_no_ask, yes_ask=101 - b_no_ask)
    return [a, b]


class BookClient(FakeClient):
    """Serves a real-looking book for each side: YES asks come from NO bids and vice versa."""

    def __init__(self, markets, series="KXMLBGAME", thin=()):
        super().__init__({series: markets})
        self.thin = set(thin)

    def get_orderbook(self, ticker):
        qty = 3 if ticker in self.thin else 500
        m = next(m for ms in self.by_series.values() for m in ms if m["ticker"] == ticker)
        # Resting bids: NO bids at 100 - yes_ask (they fill YES buyers), YES bids at 100 - no_ask.
        return {"orderbook": {"no": [[100 - m["yes_ask"], qty]], "yes": [[100 - m["no_ask"], qty]]}}


def scan_pair(a, b, **cfg):
    return scan(BookClient(pair(a, b)), Config(series=["KXMLBGAME"], **cfg), NOW)


def test_the_other_side_of_a_game_is_offered_when_it_lags_the_floor():
    found = {(c.ticker.rsplit("-", 1)[1], c.side): c for c in scan_pair(90, 89)}
    assert set(found) == {("NYY", "yes"), ("BOS", "no")}
    primary, mirror = found[("NYY", "yes")], found[("BOS", "no")]
    assert not primary.via_mirror and mirror.via_mirror and mirror.ask == 89
    assert primary.mirror_key == f"{EVENT}-BOS|no" and mirror.mirror_key == f"{EVENT}-NYY|yes"
    assert mirror.depth_status == "ok" and mirror.fill_price == 89.0


def test_mirror_tolerance_sets_how_far_below_the_floor_it_looks():
    assert len(scan_pair(90, 88)) == 2                      # exactly 2c under the 90c floor is allowed
    only = scan_pair(90, 87)
    assert [c.side for c in only] == ["yes"] and only[0].mirror_key           # linked, but nothing extra offered
    assert len(scan_pair(90, 87, mirror_tolerance=3.0)) == 2
    assert len(scan_pair(90, 89, mirror_tolerance=0)) == 1                  # switched off


def test_both_sides_reaching_the_floor_are_each_one_candidate():
    found = scan_pair(91, 90)
    assert len(found) == 2 and not any(c.via_mirror for c in found)


def test_no_mirror_without_the_one_trade_per_game_rule():
    assert len(scan_pair(90, 89, one_trade_per_event=False)) == 1


def test_games_with_a_draw_or_with_more_than_two_markets_are_not_mirrored():
    soccer = pair(90, 89, event="KXEPLGAME-26OCT06ARSCHE")
    found = scan(BookClient(soccer, "KXEPLGAME"), Config(series=["KXEPLGAME"]), NOW)
    assert len(found) == 1 and found[0].mirror_key == ""               # a draw makes NO a different bet
    three_way = pair(90, 89) + [market(ticker=f"{EVENT}-TIE", event_ticker=EVENT, yes_bid=5, yes_ask=6, no_bid=94, no_ask=95)]
    found = scan(BookClient(three_way), Config(series=["KXMLBGAME"]), NOW)
    assert all(c.mirror_key == "" and not c.via_mirror for c in found)


def trades_after(tmp_path, found):
    from kalshi_scanner.storage import load_trades, record_new_trades
    return record_new_trades(found, tmp_path), load_trades(tmp_path)


def test_the_cheaper_side_gets_the_trade_even_when_it_is_under_the_floor(tmp_path):
    added, trades = trades_after(tmp_path, scan_pair(90, 89))
    assert added == 1
    t = trades[0]
    assert t["ticker"].endswith("-BOS") and t["side"] == "no" and t["entry_price"] == "89.0"
    assert t["via_mirror"] == "1" and t["partner_fill"] == "90.0"


def test_when_both_sides_qualify_the_cheaper_is_taken_and_ties_make_one_trade(tmp_path):
    added, trades = trades_after(tmp_path, scan_pair(91, 90))
    assert added == 1 and trades[0]["entry_price"] == "90.0" and trades[0]["via_mirror"] == "0"
    assert trades[0]["partner_fill"] == "91.0"
    added, trades = trades_after(tmp_path / "tie", scan_pair(91, 91))
    assert added == 1 and trades[0]["partner_fill"] == "91.0"


def test_a_thin_cheaper_side_is_passed_over(tmp_path):
    client = BookClient(pair(90, 89), thin={f"{EVENT}-BOS"})
    found = scan(client, Config(series=["KXMLBGAME"]), NOW)
    added, trades = trades_after(tmp_path, found)
    assert added == 1 and trades[0]["ticker"].endswith("-NYY") and trades[0]["partner_fill"] == ""


def test_soccer_style_games_still_take_the_highest_priced_contract(tmp_path):
    added, trades = trades_after(tmp_path, soccer_candidates(92, 97, 94))
    assert added == 1 and trades[0]["ticker"].endswith("X1") and trades[0]["via_mirror"] == "0"


def test_snapshot_files_keep_their_columns(tmp_path):
    from kalshi_scanner.storage import SNAPSHOT_FIELDS, log_snapshots
    assert "mirror_key" not in SNAPSHOT_FIELDS and "via_mirror" not in SNAPSHOT_FIELDS
    log_snapshots(scan_pair(90, 89), NOW, tmp_path)                         # includes a mirror candidate
    header, *rows = (tmp_path / "snapshots" / "2026-10-06.csv").read_text().splitlines()
    assert header.split(",") == SNAPSHOT_FIELDS and len(rows) == 2


# ---- games seen ----

def test_diagnostics_record_every_game_and_its_peak_ask():
    from kalshi_scanner.scanner import new_diag
    diag = new_diag()
    quiet = pair(86, 85, event="KXMLBGAME-26OCT06QUIET")
    scan(BookClient(pair(90, 89) + quiet), Config(series=["KXMLBGAME"]), NOW, diag)
    games = diag["games"]
    assert set(games) == {EVENT, "KXMLBGAME-26OCT06QUIET"}
    assert games[EVENT]["peak_ask"] == 90 and games["KXMLBGAME-26OCT06QUIET"]["peak_ask"] == 86
    assert games[EVENT]["series"] == "KXMLBGAME" and games[EVENT]["expiry"] == "2026-10-07T02:30:00+00:00"


def test_record_games_adds_raises_the_peak_and_skips_unchanged_scans(tmp_path):
    from kalshi_scanner.storage import load_games, record_games
    seen = {"E1": {"series": "KXNHLGAME", "expiry": "2026-10-07T02:30:00+00:00", "peak_ask": 84.0}}
    assert record_games(seen, NOW, tmp_path) == 1
    row = load_games(tmp_path)[0]
    assert row["day"] == "2026-10-06" and row["sport"] == "NHL" and row["peak_ask"] == "84"   # 9:30 pm Central
    stamp = (tmp_path / "games.csv").stat().st_mtime_ns
    assert record_games(seen, NOW, tmp_path) == 0 and (tmp_path / "games.csv").stat().st_mtime_ns == stamp
    later = datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)
    seen["E1"]["peak_ask"] = 91.5
    seen["E2"] = {"series": "KXNBAGAME", "expiry": "2026-10-07T04:00:00+00:00", "peak_ask": 70.0}
    assert record_games(seen, later, tmp_path) == 2
    rows = {r["event_ticker"]: r for r in load_games(tmp_path)}
    assert rows["E1"]["peak_ask"] == "91.5" and rows["E1"]["first_seen"] == "2026-10-06T18:00:00+00:00"
    seen["E1"]["peak_ask"] = 80.0
    assert record_games(seen, later, tmp_path) == 0                          # a lower ask never lowers the peak
    assert record_games({}, later, tmp_path) == 0


def test_scan_cycle_logs_games_and_trades_the_cheaper_side(tmp_path, monkeypatch):
    from kalshi_scanner import storage
    from kalshi_scanner.runner import scan_cycle
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    client = BookClient(pair(90, 89) + pair(80, 79, event="KXMLBGAME-26OCT06NOPE"))
    found, diag, added = scan_cycle(client, Config(series=["KXMLBGAME"]), NOW)
    assert added == 1 and len(found) == 2
    games = {r["event_ticker"]: r for r in storage.load_games(tmp_path)}
    assert set(games) == {EVENT, "KXMLBGAME-26OCT06NOPE"} and games["KXMLBGAME-26OCT06NOPE"]["peak_ask"] == "80"
    assert storage.load_trades(tmp_path)[0]["via_mirror"] == "1"
