from datetime import datetime, timedelta, timezone

from kalshi_scanner import hypothesis, report, startstate
from kalshi_scanner.scanner import Config, count_field, evaluate
from kalshi_scanner.settle import settle_trade
from kalshi_scanner.startstate import CERTAIN, LIKELY, LIVE, UNKNOWN, classify, state_of
from kalshi_scanner.storage import (TRADE_FIELDS, load_games, load_trades, record_games, record_new_trades,
                                    save_trades)

END = datetime(2026, 10, 7, 2, 30, tzinfo=timezone.utc)            # 9:30 PM Central, Oct 6
NOW = datetime(2026, 10, 6, 23, 0, tzinfo=timezone.utc)            # 3.5 h before the end
LATE_NOW = datetime(2026, 10, 7, 1, 30, tzinfo=timezone.utc)       # 1 h before the end


def at(minutes_before_end):
    return END - timedelta(minutes=minutes_before_end)


# ---- classify ----

def test_four_hours_or_more_before_the_end_is_certainly_prematch():
    assert classify(at(240), END) == CERTAIN
    assert classify(at(900), END, first_ask=60, peak_ask=99) == CERTAIN      # lead beats any price move
    assert classify(at(239), END) == UNKNOWN                                  # no game record, not certain


def test_a_big_climb_since_first_sighting_means_live():
    assert classify(at(60), END, first_ask=70, peak_ask=78) == LIVE
    assert classify(at(60), END, first_ask=70, peak_ask=77.9) == UNKNOWN      # 7.9c is not enough
    assert classify(at(-30), END, first_ask=60, peak_ask=95) == LIVE          # after the listed end, still moving


def test_flat_price_with_lead_is_probably_prematch():
    assert classify(at(120), END, first_ask=93, peak_ask=93) == LIKELY
    assert classify(at(120), END, first_ask=93, peak_ask=95.9) == LIKELY      # under 3c of movement
    assert classify(at(120), END, first_ask=93, peak_ask=96) == UNKNOWN       # 3-8c: neither
    assert classify(at(60), END, first_ask=93, peak_ask=93) == UNKNOWN        # too close to the end to say


def test_missing_data_is_unknown_not_a_guess():
    assert classify(None, END) == UNKNOWN and classify(at(60), None) == UNKNOWN
    assert classify(at(60), END, first_ask=None, peak_ask=None) == UNKNOWN


def test_state_of_prefers_the_stored_label_and_falls_back_to_lead_time():
    stamp = lambda dt: dt.isoformat(timespec="seconds")
    assert state_of({"start_state": LIVE, "first_seen": stamp(at(600)), "expiry": stamp(END)}) == LIVE
    assert state_of({"first_seen": stamp(at(600)), "expiry": stamp(END)}) == CERTAIN
    assert state_of({"start_state": "", "first_seen": stamp(at(30)), "expiry": stamp(END)}) == UNKNOWN
    assert state_of({"start_state": "garbage", "first_seen": stamp(at(30)), "expiry": stamp(END)}) == UNKNOWN


# ---- games.csv tracker ----

def observed(peak, event="KXNHLGAME-26OCT06AAABBB"):
    return {event: {"series": "KXNHLGAME", "expiry": END.isoformat(timespec="seconds"), "peak_ask": peak}}


def test_first_ask_is_written_once_and_peak_keeps_rising(tmp_path):
    record_games(observed(70.0), at(300), tmp_path)
    record_games(observed(95.0), at(60), tmp_path)
    record_games(observed(80.0), at(30), tmp_path)           # a lower later reading changes nothing
    row = load_games(tmp_path)[0]
    assert row["first_ask"] == "70" and row["peak_ask"] == "95"


def test_old_game_rows_without_first_ask_still_load(tmp_path):
    (tmp_path / "games.csv").write_text(
        "event_ticker,series,sport,day,expiry,first_seen,peak_ask\n"
        "E1,KXNHLGAME,NHL,2026-10-06,2026-10-07T02:30:00+00:00,2026-10-06T20:00:00+00:00,90\n")
    record_games({"E1": {"series": "KXNHLGAME", "expiry": END.isoformat(), "peak_ask": 96.0}}, NOW, tmp_path)
    row = load_games(tmp_path)[0]
    assert row["peak_ask"] == "96" and row["first_ask"] == ""


def mk_market(ask=95, event="KXNHLGAME-26OCT06AAABBB", ticker="KXNHLGAME-26OCT06AAABBB-AAA"):
    return {"ticker": ticker, "event_ticker": event, "title": "t", "status": "open",
            "expected_expiration_time": END.isoformat().replace("+00:00", "Z"), "yes_bid": ask - 1,
            "yes_ask": ask, "no_bid": 100 - ask, "no_ask": 101 - ask, "volume": 10, "open_interest": 5}


def open_trade(now, ask, data_dir, first, peak):
    """Record the game as the scanner would, then open a trade on it. Returns the stored start_state."""
    record_games(observed(first), at(400), data_dir)
    record_games(observed(peak), now, data_dir)
    cands = evaluate(mk_market(ask), now, Config())
    assert record_new_trades(cands, data_dir) == 1
    return load_trades(data_dir)[0]["start_state"]


def test_trade_is_labelled_live_when_the_price_climbed(tmp_path):
    assert open_trade(NOW, 95, tmp_path, first=72.0, peak=95.0) == LIVE


def test_trade_is_labelled_prematch_likely_when_flat(tmp_path):
    assert open_trade(NOW, 95, tmp_path, first=94.0, peak=95.0) == LIKELY


def test_trade_is_unknown_close_to_the_end_with_a_flat_price(tmp_path):
    assert open_trade(LATE_NOW, 95, tmp_path, first=94.0, peak=95.0) == UNKNOWN


def test_trade_with_no_game_record_is_labelled_from_lead_time_only(tmp_path):
    cands = evaluate(mk_market(95), NOW, Config())
    record_new_trades(cands, tmp_path)
    assert load_trades(tmp_path)[0]["start_state"] == UNKNOWN              # 3.5 h of lead is not 4 h


def test_label_does_not_drift_with_later_price_moves(tmp_path):
    assert open_trade(NOW, 95, tmp_path, first=94.0, peak=95.0) == LIKELY
    record_games(observed(99.0), END, tmp_path)                            # price runs up afterwards
    assert load_trades(tmp_path)[0]["start_state"] == LIKELY               # stored at entry, untouched


# ---- volume parsing ----

def test_count_field_reads_current_and_legacy_names():
    assert count_field({"volume": 5000}, "volume") == 5000
    assert count_field({"volume_fp": "301538.66"}, "volume") == 301538
    assert count_field({"open_interest_fp": "123063.18"}, "open_interest") == 123063
    assert count_field({"volume": 7, "volume_fp": "9"}, "volume") == 7
    assert count_field({}, "volume") == 0 and count_field({"volume_fp": ""}, "volume") == 0


def test_scanner_now_records_volume_from_the_fp_field():
    m = mk_market(95)
    del m["volume"], m["open_interest"]
    m.update(volume_fp="301538.66", open_interest_fp="123063.18")
    cand = evaluate(m, NOW, Config())[0]
    assert cand.volume == 301538 and cand.open_interest == 123063


# ---- settlement when neither side wins ----

NO97 = {"entry_price": "97", "contracts": "100", "fee_usd": "0.21", "side": "no"}
YES90 = {"entry_price": "90", "contracts": "100", "fee_usd": "0.58", "side": "yes"}


def test_a_match_that_never_starts_is_booked_at_fifty_cents():
    t = dict(NO97)
    assert settle_trade(t, {"status": "settled", "result": ""}, END)
    assert t["status"] == "void" and t["won"] == "" and t["result"] == "half_assumed"
    assert abs(float(t["pnl_usd"]) - (100 * (50 - 97) / 100 - 0.21)) < 1e-6          # -47.21, not 0
    y = dict(YES90)
    settle_trade(y, {"status": "finalized", "result": ""}, END)
    assert abs(float(y["pnl_usd"]) - (100 * (50 - 90) / 100 - 0.58)) < 1e-6


def test_the_markets_own_settlement_value_beats_the_assumption():
    t = dict(NO97)
    settle_trade(t, {"status": "settled", "result": "scalar", "settlement_value_dollars": "0.2500"}, END)
    assert t["result"] == "scalar" and abs(float(t["pnl_usd"]) - (100 * (75 - 97) / 100 - 0.21)) < 1e-6
    y = dict(YES90)
    settle_trade(y, {"status": "settled", "result": "", "settlement_value": 50}, END)
    assert y["result"] == "half" and abs(float(y["pnl_usd"]) - (-40 - 0.58)) < 1e-6


def test_a_settlement_value_that_names_a_winner_is_a_normal_win_or_loss():
    t = dict(NO97)
    settle_trade(t, {"status": "settled", "result": "", "settlement_value_dollars": "0.0000"}, END)
    assert t["status"] == "settled" and t["won"] == "1" and t["result"] == "no"


def test_yes_and_no_results_are_unchanged():
    t = dict(NO97)
    settle_trade(t, {"status": "settled", "result": "no"}, END)
    assert t["status"] == "settled" and t["won"] == "1" and abs(float(t["pnl_usd"]) - (3 - 0.21)) < 1e-6
    t = dict(NO97)
    assert not settle_trade(t, {"status": "active", "result": ""}, END)


# ---- report ----

def row(**over):
    r = {k: "" for k in TRADE_FIELDS}
    r.update(trade_id="x|yes", ticker="x", event_ticker="e", series="KXITFWMATCH", sport="Tennis (ITF women)",
             title="A wins", side="yes", entry_price="95.0", best_ask="95.0", contracts="100", fee_usd="0.35",
             first_seen=at(600).isoformat(timespec="seconds"), expiry=END.isoformat(timespec="seconds"),
             status="settled", result="yes", won="1", pnl_usd="4.65", settled_at=END.isoformat(timespec="seconds"))
    r.update(over)
    return r


def test_report_shows_start_state_table_void_line_and_overdue_trades(tmp_path):
    now = END + timedelta(hours=20)
    rows = [row(trade_id="a|yes", start_state=LIVE),
            row(trade_id="b|yes"),                                              # unlabelled, 10 h lead -> certain
            row(trade_id="c|yes", first_seen=at(45).isoformat(timespec="seconds"), won="0", result="no",
                pnl_usd="-95.35"),                                              # unlabelled, 45 min lead -> unknown
            row(trade_id="d|no", side="no", status="void", result="half_assumed", won="", pnl_usd="-47.21"),
            row(trade_id="e|no", side="no", status="open", result="", won="", pnl_usd="", settled_at="",
                title="Stuck Player wins")]
    save_trades(rows, tmp_path)
    text = report.render(tmp_path, now)
    assert "By start state at entry" in text
    assert any(l.split()[:1] == ["live_inferred"] for l in text.splitlines())
    assert any(l.split()[:1] == ["prematch_certain"] for l in text.splitlines())
    assert any(l.split()[:1] == ["unknown"] for l in text.splitlines())
    assert "Voided / half-refund settlements: 1 trades, P&L ($47.21) (1 booked at an assumed 50c" in text
    assert "1 open trade(s) are more than 6h past their listed end" in text and "Stuck Player wins" in text
    all_settled = next(l for l in text.splitlines() if l.strip().startswith("ALL SETTLED"))
    assert all_settled.split()[2] == "3"                 # 3 settled trades; the void and open ones are not in it


def test_void_trades_stay_out_of_hit_rates(tmp_path):
    save_trades([row(trade_id="a|yes"), row(trade_id="v|no", status="void", result="half", won="", pnl_usd="-47.0")],
                tmp_path)
    assert len(report.settled(load_trades(tmp_path))) == 1


def test_overdue_open_needs_more_than_six_hours_and_an_open_status():
    soon = row(status="open", expiry=(END).isoformat(timespec="seconds"))
    assert report.overdue_open([soon], END + timedelta(hours=5.9)) == []
    assert report.overdue_open([soon], END + timedelta(hours=6.1)) == [soon]
    assert report.overdue_open([row()], END + timedelta(days=3)) == []        # settled trades never count


def test_hypothesis_report_splits_the_late_group_by_start_state(tmp_path):
    # Opened after the frozen START, in the last 30 minutes before the end, so they count as late.
    base = hypothesis.START + timedelta(hours=1)
    end = base + timedelta(minutes=20)
    late = [row(trade_id="a|yes", first_seen=base.isoformat(timespec="seconds"), expiry=end.isoformat(timespec="seconds"),
                start_state=LIVE),
            row(trade_id="b|yes", first_seen=base.isoformat(timespec="seconds"), expiry=end.isoformat(timespec="seconds"),
                start_state=LIKELY, won="0", result="no", pnl_usd="-95.35")]
    save_trades(late, tmp_path)
    text = hypothesis.render(tmp_path)
    assert "Late group split by start state" in text and "late: live_inferred" in text and "late: prematch_likely" in text
    hypothesis.render(tmp_path / "empty")                                      # no trades at all must not crash
