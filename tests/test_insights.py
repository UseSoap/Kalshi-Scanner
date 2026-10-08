from datetime import datetime, timezone

import pytest

from kalshi_scanner import insights, report
from kalshi_scanner.storage import TRADE_FIELDS, load_snapshots, save_trades


def row(**kw):
    """A full trades.csv row (all strings, like the real file) with sensible defaults."""
    base = {k: "" for k in TRADE_FIELDS}
    base.update(trade_id="t|yes", ticker="t", event_ticker="e", series="KXMLBGAME", sport="MLB", title="Team wins",
                side="yes", entry_price="90.0", best_ask="90.0", spread="1.0", contracts="100", fee_usd="0.63",
                status="settled", won="1", pnl_usd="9.37", first_seen="2026-10-07T00:00:00+00:00",
                expiry="2026-10-07T03:00:00+00:00", settled_at="2026-10-07T04:00:00+00:00")
    for k, v in kw.items():
        base[k] = str(v)
    return base


def test_breakeven_includes_fees_and_edge_is_hit_minus_breakeven():
    s = report.summarize([row()])
    assert s["breakeven"] == pytest.approx((90.0 + 0.63) / 100)       # (stake + fee) / contracts
    assert s["edge"] == pytest.approx(1.0 - 0.9063)
    lost = report.summarize([row(won=0, pnl_usd=-90.63)])
    assert lost["edge"] == pytest.approx(-0.9063)
    assert report.summarize([])["breakeven"] == 0.0 and report.summarize([])["edge"] == 0.0


def test_breakeven_is_weighted_by_contracts():
    big, small = row(entry_price=90, contracts=100, fee_usd=0.63), row(entry_price=95, contracts=10, fee_usd=0.04)
    s = report.summarize([big, small])
    assert s["breakeven"] == pytest.approx((90 + 0.63 + 9.5 + 0.04) / 110)


def test_verdict_has_three_outcomes():
    assert insights.edge_verdict(0.95, 0.93, 0.97, 0.92).startswith("EDGE SO FAR")
    assert insights.edge_verdict(0.80, 0.70, 0.88, 0.92).startswith("NO EDGE")
    assert "below" in insights.edge_verdict(0.89, 0.67, 0.97, 0.916)
    assert insights.edge_verdict(0.93, 0.70, 0.98, 0.916).startswith("INCONCLUSIVE")


def test_win_loss_stats():
    trades = [row(pnl_usd=9), row(pnl_usd=11), row(pnl_usd=10), row(won=0, pnl_usd=-90)]
    w = insights.win_loss_stats(trades)
    assert (w["wins"], w["losses"]) == (3, 1)
    assert w["avg_win"] == pytest.approx(10) and w["avg_loss"] == pytest.approx(90) and w["largest_loss"] == 90
    assert w["wins_per_loss"] == pytest.approx(9) and w["profit_factor"] == pytest.approx(30 / 90)
    only_wins = insights.win_loss_stats([row()])
    assert only_wins["avg_loss"] is None and only_wins["profit_factor"] is None
    assert insights.win_loss_stats([]) is None


def test_daily_results_use_us_central_game_day_and_accumulate():
    trades = [
        row(expiry="2026-10-07T03:00:00+00:00", pnl_usd=10),               # 10 pm on Oct 6 in Chicago
        row(expiry="2026-10-07T06:00:00+00:00", pnl_usd=-4, won=0),        # 1 am on Oct 7
        row(expiry="2026-10-07T23:00:00+00:00", pnl_usd=3),                # 6 pm on Oct 7
    ]
    days = insights.daily_results(trades)
    assert [d["day"] for d in days] == ["2026-10-06", "2026-10-07"]
    assert (days[1]["n"], days[1]["wins"], days[1]["pnl"]) == (2, 1, -1)
    assert [d["cumulative"] for d in days] == [10, 9]


def test_equity_stats_drawdown_and_streak():
    trades = [
        row(pnl_usd=10, settled_at="2026-10-07T01:00:00+00:00"),
        row(pnl_usd=-50, won=0, settled_at="2026-10-07T02:00:00+00:00"),
        row(pnl_usd=20, settled_at="2026-10-07T03:00:00+00:00"),
    ]
    eq = insights.equity_stats(list(reversed(trades)))                   # input order must not matter
    assert eq["cumulative"] == -20 and eq["peak"] == 10
    assert eq["max_drawdown"] == 50 and eq["drawdown"] == 30
    assert (eq["streak_kind"], eq["streak_len"]) == ("win", 1)
    assert insights.equity_stats([]) is None


def test_trades_to_separate_behaviour():
    near, far = insights.trades_to_separate(0.93, 0.916), insights.trades_to_separate(0.96, 0.916)
    assert near > far > 0                                                # a bigger edge is proven sooner
    assert insights.trades_to_separate(0.916, 0.916) is None            # no edge: no sample ever separates them
    below = insights.trades_to_separate(0.88, 0.916)
    lo, hi = insights.wilson_interval(0.88 * below, below)
    assert hi < 0.916                                                    # below break-even works too
    assert insights.trades_to_separate(0.9161, 0.916, max_n=50) is None  # gives up at max_n


def test_trades_per_day_needs_two_trades_and_floors_span_at_one_day():
    assert insights.trades_per_day([row()]) is None
    same_day = [row(first_seen="2026-10-07T00:00:00+00:00"), row(first_seen="2026-10-07T05:00:00+00:00")]
    assert insights.trades_per_day(same_day) == 2
    two_days = [row(first_seen="2026-10-07T00:00:00+00:00"), row(first_seen="2026-10-09T00:00:00+00:00")] * 2
    assert insights.trades_per_day(two_days) == 2


def test_group_labels():
    assert insights.side_label(row(side="yes")) == "Bought YES" and insights.side_label(row(side="no")) == "Bought NO"
    assert insights.side_label(row(side="")) is None
    assert [insights.spread_label(row(spread=s)) for s in ("0.5", "1.0", "1.5", "2.0", "3.0")] == [
        "1c or less", "1c or less", "over 1 to 2c", "over 1 to 2c", "over 2c"]
    assert insights.spread_label(row(spread="")) is None
    seen = "2026-10-07T00:00:00+00:00"

    def label(expiry):
        return insights.timing_label(row(first_seen=seen, expiry=expiry))

    assert label("2026-10-06T23:50:00+00:00") == "after listed end"
    assert label("2026-10-07T00:10:00+00:00") == "0-30 min before"
    assert label("2026-10-07T01:00:00+00:00") == "30-90 min before"
    assert label("2026-10-07T03:00:00+00:00") == "90+ min before"
    assert insights.timing_label(row(expiry="")) is None


def test_fill_quality_compares_entry_with_the_quote_in_both_directions():
    trades = [row(entry_price=91, best_ask=90), row(entry_price=90, best_ask=90), row(entry_price=90, best_ask=96),
              row(entry_price=91, best_ask=91, contracts=50, fee_usd=0.3, spread=3)]
    q = insights.fill_quality(trades)
    assert (q["above"], q["equal"], q["below"]) == (1, 2, 1)
    assert q["avg_above_c"] == 1 and q["avg_below_c"] == -6 and q["worst_below_c"] == -6
    assert q["vs_quote_usd"] == pytest.approx((1 + 0 - 6 + 0) * 100 / 100)     # entry minus quote, in dollars
    assert q["partial"] == 1 and q["biggest"] == 100
    assert q["fees"] == pytest.approx(0.63 * 3 + 0.3)
    assert q["fee_lift_pts"] == pytest.approx(q["fees"] / 350 * 100)
    assert q["avg_spread_c"] == pytest.approx(1.5)
    assert insights.fill_quality([]) is None


def test_fill_quality_survives_blank_fields():
    q = insights.fill_quality([row(best_ask="", spread="", fee_usd="")])
    assert q["with_quote"] == 0 and q["avg_spread_c"] is None


def test_open_exposure_sums_risk_and_sorts_by_listed_end():
    trades = [
        row(status="open", title="Late", expiry="2026-10-07T05:00:00+00:00"),
        row(status="open", title="Soon", expiry="2026-10-07T01:00:00+00:00", entry_price=95, contracts=10, fee_usd=0.04),
        row(status="settled"),
    ]
    ex = insights.open_exposure(trades)
    assert ex["count"] == 2
    assert ex["at_risk"] == pytest.approx(90.63 + 9.54)
    assert ex["max_profit"] == pytest.approx((10 - 0.63) + (0.5 - 0.04))
    assert [u["title"] for u in ex["upcoming"]] == ["Soon", "Late"]
    assert insights.open_exposure([row()])["count"] == 0


def snap(ticker, side, status, event="g1", ts="2026-10-07T00:00:00+00:00"):
    return {"ts": ts, "ticker": ticker, "side": side, "event_ticker": event, "depth_status": status}


def test_scan_funnel_counts_each_contract_by_its_best_depth_result():
    snaps = [snap("A", "yes", "thin"), snap("A", "yes", "ok"),           # thin first, then fillable: counts as fillable
             snap("B", "no", "ok", event="g1"),                          # fillable but the game was already traded
             snap("C", "yes", "thin", event="g2"), snap("D", "yes", "unknown", event="g3"),
             snap("E", "yes", "unchecked", event="g4", ts="2026-10-08T00:00:00+00:00")]
    f = insights.scan_funnel(snaps, [{"trade_id": "A|yes"}])
    assert (f["rows"], f["contracts"], f["games"]) == (6, 5, 4)
    assert (f["fillable"], f["thin_only"], f["unreadable_only"]) == (3, 1, 1)
    assert (f["traded"], f["skipped"]) == (1, 2)
    assert f["first"] < f["last"]
    assert insights.scan_funnel([], []) is None


def test_load_snapshots_reads_every_day_and_tolerates_a_missing_folder(tmp_path):
    assert load_snapshots(tmp_path) == []
    folder = tmp_path / "snapshots"
    folder.mkdir()
    (folder / "2026-10-07.csv").write_text("ts,ticker,side\nt1,A,yes\n")
    (folder / "2026-10-08.csv").write_text("ts,ticker,side\nt2,B,no\nt3,C,no\n")
    assert [r["ticker"] for r in load_snapshots(tmp_path)] == ["A", "B", "C"]


def test_describe_when_formats_local_time_and_age():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    ts = lambda s: datetime.fromisoformat(s)
    assert insights.describe_when(ts("2026-10-08T05:13:00+00:00"), now) == "Oct 08 12:13 AM CDT (6h ago)"
    assert insights.describe_when(ts("2026-10-08T11:30:00+00:00"), now).endswith("(30m ago)")
    assert insights.describe_when(ts("2026-10-01T12:00:00+00:00"), now).endswith("(7d ago)")
    assert insights.latest([{"x": "2026-10-07T01:00:00+00:00"}, {"x": ""}, {"x": "2026-10-08T01:00:00+00:00"}], "x").day == 8
    assert insights.latest([], "x") is None


# --- how it prints ---------------------------------------------------------------------------

def test_negative_numbers_print_in_parentheses_and_columns_line_up():
    win, lose = report.summarize([row()]), report.summarize([row(won=0, pnl_usd=-90.63)])
    w, l = report.table_row("win", win), report.table_row("loss", lose)
    assert "($90.63)" in l and "(100.00%)" in l and "(90.6)" in l          # P&L, ROI and edge in parentheses
    assert "$9.37" in w and "-$" not in w + l and "$-" not in w + l        # no minus-signed money anywhere
    assert len(w) == len(l) == len(report.table_header())                  # every row is the same width as the header


def test_empty_group_row_shows_only_a_zero_count():
    assert report.table_row("95-96c", report.summarize([])).split() == ["95-96c", "0"]


def make_data(tmp_path, trades, snapshots=None):
    save_trades(trades, tmp_path)
    if snapshots:
        folder = tmp_path / "snapshots"
        folder.mkdir()
        cols = ["ts", "ticker", "event_ticker", "side", "depth_status"]
        lines = [",".join(cols)] + [",".join(s[c] for c in cols) for s in snapshots]
        (folder / "2026-10-07.csv").write_text("\n".join(lines) + "\n")


def test_full_report_has_every_new_section(tmp_path):
    trades = [row(trade_id=f"t{i}|yes", event_ticker=f"e{i}", first_seen=f"2026-10-0{1 + i % 4}T00:00:00+00:00",
                  expiry=f"2026-10-0{1 + i % 4}T02:00:00+00:00", settled_at=f"2026-10-0{1 + i % 4}T03:00:00+00:00",
                  won=int(i != 3), pnl_usd=9.37 if i != 3 else -90.63, side="yes" if i % 2 else "no") for i in range(8)]
    trades.append(row(trade_id="open|yes", status="open", won="", pnl_usd="", title="Open Team", event_ticker="eo",
                      expiry="2026-10-09T02:00:00+00:00", first_seen="2026-10-08T22:00:00+00:00", settled_at=""))
    snaps = [snap(f"t{i}", "yes", "ok", event=f"e{i}") for i in range(8)] + [snap("x", "no", "thin", event="ex")]
    make_data(tmp_path, trades, snaps)
    text = report.render(tmp_path, now=datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc))
    for expected in ("Data freshness", "Open positions: 1 trades", "Open Team", "ALL SETTLED", "INCONCLUSIVE",
                     "Win/loss size:", "Record 7W-1L", "Results by game day", "Max drawdown", "Current streak",
                     "How much data is enough", "If the true edge is +1 pt", "By entry price:", "By sport:",
                     "By sport and entry price:", "By side bought:", "Bought YES", "Bought NO",
                     "By bid/ask spread at entry:", "By entry time relative", "Fill quality", "Fees:",
                     "Scan funnel", "Paper trades opened"):
        assert expected in text, expected
    assert "$-" not in text and "-$" not in text                           # no minus signs on money


def test_report_with_only_open_trades_still_shows_exposure_and_fills(tmp_path):
    make_data(tmp_path, [row(status="open", won="", pnl_usd="", settled_at="")])
    text = report.render(tmp_path)
    assert "Open positions: 1 trades" in text and "Nothing settled yet" in text and "Fill quality" in text
    assert "ALL SETTLED" not in text


def test_report_with_no_trades_but_snapshots_shows_the_funnel(tmp_path):
    make_data(tmp_path, [], [snap("A", "yes", "ok")])
    text = report.render(tmp_path)
    assert "No paper fills yet" in text and "Scan funnel" in text
