from datetime import datetime, timezone

from kalshi_scanner import tiers
from kalshi_scanner.scanner import Config, evaluate
from kalshi_scanner.scanner import apply_depth
from kalshi_scanner.settle import settle_open_trades
from kalshi_scanner.storage import (TIER_FIELDS, TIER_FILE, TRADE_FIELDS, load_trades, record_new_trades,
                                    record_tier_trades, save_trades, tier_start)

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
TONIGHT = "2026-10-07T02:30:00Z"
AFTER = datetime(2026, 10, 7, 6, 0, tzinfo=timezone.utc)


class Book:
    def __init__(self, ask):
        self.ask = ask

    def get_orderbook(self, ticker):
        return {"orderbook": {"yes": [], "no": [[100 - self.ask, 500]]}}


def cand(ticker="KXMLBGAME-26OCT06AAABBB-AAA", ask=92, event="KXMLBGAME-26OCT06AAABBB", book_ask=None):
    m = {"ticker": ticker, "event_ticker": event, "title": "t", "status": "open", "expected_expiration_time": TONIGHT,
         "yes_bid": ask - 1, "yes_ask": ask, "no_bid": 100 - ask, "no_ask": 101 - ask, "volume": 10, "open_interest": 5}
    c = [x for x in evaluate(m, NOW, Config()) if x.side == "yes"][0]
    return apply_depth(Book(book_ask or ask), c, Config())


def test_tier_trade_opens_only_for_a_traded_contract_that_reaches_95(tmp_path):
    assert record_new_trades([cand(ask=92)], tmp_path) == 1
    main_before = (tmp_path / "trades.csv").read_text()

    assert record_tier_trades([cand(ask=93)], tmp_path, 95.0, NOW) == 0          # not high enough yet
    assert record_tier_trades([cand(ask=96)], tmp_path, 95.0, NOW) == 1
    assert record_tier_trades([cand(ask=97)], tmp_path, 95.0, NOW) == 0          # one per main trade
    other = cand(ticker="KXMLBGAME-26OCT06AAABBB-BBB", ask=97)                    # same game, different contract
    assert record_tier_trades([other], tmp_path, 95.0, NOW) == 0

    row = load_trades(tmp_path, TIER_FILE)[0]
    assert row["base_trade_id"] == "KXMLBGAME-26OCT06AAABBB-AAA|yes" and row["base_entry_price"] == "92.0"
    assert row["entry_price"] == "96.0" and row["tier_ask"] == "95" and row["status"] == "open"
    assert (tmp_path / "trades.csv").read_text() == main_before                  # main file untouched
    assert len(load_trades(tmp_path)) == 1


def test_tier_ignores_games_with_no_main_trade_and_thin_books(tmp_path):
    assert record_tier_trades([cand(ask=96)], tmp_path, 95.0, NOW) == 0          # no main trade for this game
    record_new_trades([cand(ask=92)], tmp_path)
    thin = cand(ask=96)
    thin.depth_status = "thin"
    assert record_tier_trades([thin], tmp_path, 95.0, NOW) == 0
    assert not (tmp_path / TIER_FILE).exists()


def test_start_marker_is_written_once(tmp_path):
    assert tier_start(tmp_path) is None
    record_tier_trades([], tmp_path, 95.0, NOW)
    assert tier_start(tmp_path) == NOW
    record_tier_trades([], tmp_path, 95.0, AFTER)
    assert tier_start(tmp_path) == NOW


class Settler:
    def get_market(self, ticker):
        return {"status": "settled", "result": "yes"}


def test_both_files_settle_independently(tmp_path):
    record_new_trades([cand(ask=92)], tmp_path)
    record_tier_trades([cand(ask=96)], tmp_path, 95.0, NOW)
    assert settle_open_trades(Settler(), AFTER, tmp_path) == 2
    main, tier = load_trades(tmp_path)[0], load_trades(tmp_path, TIER_FILE)[0]
    assert main["won"] == tier["won"] == "1" and main["status"] == tier["status"] == "settled"
    assert float(tier["pnl_usd"]) < float(main["pnl_usd"])        # same win, but the 96c entry paid less


def trade_row(trade_id, entry, won, first_seen, tier=False, base_id="", base_entry=""):
    fee = 0.2
    pnl = 100 * (100 - entry) / 100 - fee if won else -(100 * entry / 100) - fee
    row = {k: "" for k in (TIER_FIELDS if tier else TRADE_FIELDS)}
    row.update(trade_id=trade_id, first_seen=first_seen, ticker=trade_id.split("|")[0], event_ticker=trade_id,
               series="KXMLBGAME", sport="MLB", side="yes", entry_price=str(entry), contracts="100", fee_usd=str(fee),
               status="settled", result="yes" if won else "no", won="1" if won else "0", pnl_usd=f"{pnl:.4f}",
               expiry=TONIGHT, settled_at="2026-10-07T04:00:00+00:00")
    if tier:
        row.update(tier_ask="95", base_trade_id=base_id, base_entry_price=str(base_entry))
    return row


def build_scenario(tmp_path):
    seen = "2026-10-06T18:00:00+00:00"
    main = [trade_row("A|yes", 92, True, seen), trade_row("B|yes", 93, False, seen), trade_row("C|yes", 91, True, seen),
            trade_row("D|yes", 96.5, True, seen),
            trade_row("OLD|yes", 91, True, "2026-10-05T18:00:00+00:00")]           # opened before the tier existed
    tier = [trade_row("A|yes|95", 96, True, seen, True, "A|yes", 92), trade_row("B|yes|95", 96, False, seen, True, "B|yes", 93),
            trade_row("D|yes|95", 96.5, True, seen, True, "D|yes", 96.5)]
    save_trades(main, tmp_path)
    save_trades(tier, tmp_path, TIER_FILE)
    (tmp_path / "trades_95_start.txt").write_text("2026-10-06T12:00:00+00:00\n")


def test_cohort_excludes_pre_tier_trades_and_separates_jumps(tmp_path):
    build_scenario(tmp_path)
    c = tiers.split_cohort(load_trades(tmp_path), load_trades(tmp_path, TIER_FILE), 95.0, tier_start(tmp_path))
    assert (len(c["waited"]), len(c["jumped"]), len(c["skipped"])) == (2, 1, 1)     # OLD is not counted as skipped
    assert [t["trade_id"] for t in c["waited_base"]] == ["A|yes", "B|yes"]
    assert c["base_n"] == 4


def test_render_has_separate_sections_and_does_not_touch_main_report(tmp_path):
    from kalshi_scanner import report
    build_scenario(tmp_path)
    main_report = report.render(tmp_path, NOW)
    text = tiers.render(tmp_path)
    for expected in ("What this tests", "1. Same games, two entry prices", "2. Games the 95c tier skipped",
                     "3. Games already at 95c+", "4. Each rule as a whole", "NOT added to the main report"):
        assert expected in text
    assert report.render(tmp_path, NOW) == main_report
    assert "What this tests" not in main_report and "trades_95" not in main_report


def test_render_before_start_and_before_settlement(tmp_path):
    assert "Not started yet" in tiers.render(tmp_path)
    (tmp_path / "trades_95_start.txt").write_text("2026-10-06T12:00:00+00:00\n")
    assert "Nothing settled yet" in tiers.render(tmp_path)


def test_scan_cycle_runs_the_tier_without_changing_main_counts(tmp_path, monkeypatch):
    from kalshi_scanner import storage
    from kalshi_scanner.runner import scan_cycle
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)

    class C:
        def __init__(self, ask):
            self.ask = ask

        def list_markets(self, series_ticker, status):
            if series_ticker != "KXMLBGAME":
                return iter([])
            a = self.ask
            return iter([{"ticker": "T-1", "event_ticker": "E-1", "title": "t", "status": "open",
                          "expected_expiration_time": TONIGHT, "yes_bid": a - 1, "yes_ask": a, "no_bid": 100 - a,
                          "no_ask": 101 - a, "volume": 10, "open_interest": 5}])

        def get_orderbook(self, ticker):
            return {"orderbook": {"yes": [], "no": [[100 - self.ask, 500]]}}

    cfg = Config(series=["KXMLBGAME"])
    _, diag, added = scan_cycle(C(92), cfg, NOW)
    assert added == 1 and diag["tier_added"] == 0
    _, diag, added = scan_cycle(C(96), cfg, NOW)
    assert added == 0 and diag["tier_added"] == 1                    # main count unchanged; tier opened
    assert len(load_trades(tmp_path)) == 1 and len(load_trades(tmp_path, TIER_FILE)) == 1

    off = Config(series=["KXMLBGAME"], second_tier_ask=None)
    _, diag, _ = scan_cycle(C(98), off, NOW)
    assert diag["tier_added"] == 0 and len(load_trades(tmp_path, TIER_FILE)) == 1
