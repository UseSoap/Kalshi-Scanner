from datetime import datetime, timedelta, timezone

from kalshi_scanner import hypothesis, report
from kalshi_scanner.runner import track_quotes
from kalshi_scanner.scanner import Candidate
from kalshi_scanner.storage import TRADE_FIELDS, _trade_row, save_trades

END = datetime(2026, 10, 12, 2, 0, tzinfo=timezone.utc)
AFTER_START = hypothesis.START + timedelta(hours=1)


def row(minutes_before_end, won=True, seen_after_start=True, series="KXMLBGAME", price="92", **over):
    end = END
    seen = end - timedelta(minutes=minutes_before_end)
    if not seen_after_start:
        seen = hypothesis.START - timedelta(days=1)
        end = seen + timedelta(minutes=minutes_before_end)
    t = {k: "" for k in TRADE_FIELDS}
    t.update(trade_id=f"t{minutes_before_end}{won}{seen.timestamp()}", first_seen=seen.isoformat(), expiry=end.isoformat(),
            ticker="T", event_ticker=f"E{seen.timestamp()}{minutes_before_end}", series=series, sport="",
            entry_price=price, best_ask=price, depth_at_ask="500", spread="1", contracts="100", fee_usd="0.5",
            status="settled", result="yes", won="1" if won else "0", pnl_usd="7.5" if won else "-92.5")
    t.update(over)
    return t


def test_late_means_final_30_minutes_or_after():
    assert hypothesis.is_late(row(29)) and hypothesis.is_late(row(0)) and hypothesis.is_late(row(-20))
    assert not hypothesis.is_late(row(30)) and not hypothesis.is_late(row(120))


def test_only_trades_opened_after_start_count():
    assert hypothesis.counts(row(10))
    assert not hypothesis.counts(row(10, seen_after_start=False))


def test_exploratory_late_trades_are_shown_but_not_tested(tmp_path):
    save_trades([row(10, seen_after_start=False), row(10), row(120)], tmp_path)
    text = hypothesis.render(tmp_path)
    assert "Progress: 1/150 late entries settled" in text
    assert "late, exploratory" in text and "Control (all other entries, same period): 1 settled" in text
    assert "IN PROGRESS" in text


def test_verdict_is_withheld_until_the_stop_rule(tmp_path, monkeypatch):
    save_trades([row(10) for _ in range(20)], tmp_path)             # 20/20 looks great but is far below 150
    text = hypothesis.render(tmp_path)
    assert "IN PROGRESS" in text and "SUPPORTED" not in text.replace("NOT SUPPORTED", "")


def test_supported_and_not_supported_at_the_stop_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(hypothesis, "STOP_N", 40)
    save_trades([row(10, price="70") for _ in range(40)], tmp_path)   # 40/40 clears a ~70% break-even easily
    assert "Verdict: SUPPORTED" in hypothesis.render(tmp_path)
    save_trades([row(10) for _ in range(40)], tmp_path)               # 40/40 at 92c still can't clear its ~92.5% b/e
    assert "NOT SUPPORTED" in hypothesis.render(tmp_path)
    save_trades([row(10, won=i >= 5) for i in range(40)], tmp_path)  # 35/40 = 87.5% against a ~92% break-even
    assert "NOT SUPPORTED" in hypothesis.render(tmp_path)


def test_tennis_split_and_execution_rows(tmp_path):
    save_trades([row(10, series="KXITFMATCH", quote_age_s="0"), row(5, quote_age_s="240", contracts="40", spread="3"),
                 row(120, quote_age_s="0")], tmp_path)
    text = hypothesis.render(tmp_path)
    assert "late: tennis" in text and "late: everything else" in text
    assert "partial fills 50%" in text                              # 1 of 2 late fills under 100 contracts
    assert "quote unchanged >=90s 50% (of 2 timed)" in text


def test_execution_row_says_when_quote_age_is_missing(tmp_path):
    save_trades([row(10)], tmp_path)
    assert "quote age not recorded yet" in hypothesis.render(tmp_path)


def test_report_sections_are_numbered_and_ordered(tmp_path):
    save_trades([row(10), row(120)], tmp_path)
    text = report.render(tmp_path)
    heads = [h for h in ("1. STATUS", "2. OVERALL RESULT", "3. BREAKDOWNS", "4. EXECUTION QUALITY") if h in text]
    assert heads == ["1. STATUS", "2. OVERALL RESULT", "3. BREAKDOWNS", "4. EXECUTION QUALITY"]
    assert text.index("1. STATUS") < text.index("2. OVERALL RESULT") < text.index("3. BREAKDOWNS") < text.index("4. EXECUTION QUALITY")


def cand(ticker="T", side="yes", ask=92.0, bid=91.0):
    return Candidate(ts="", ticker=ticker, event_ticker="E", series="S", title="", side=side, ask=ask, bid=bid,
                     spread=ask - bid, volume=0, open_interest=0, expiry="", contracts=100, fee_usd=0.5,
                     win_usd=0, loss_usd=0, breakeven_prob=0.9)


def test_quote_age_counts_up_while_the_quote_sits_and_resets_when_it_moves():
    state, t0 = {}, datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    ages = []
    for i, ask in enumerate((92.0, 92.0, 92.0, 93.0, 93.0)):
        c = cand(ask=ask)
        track_quotes([c], t0 + timedelta(seconds=90 * i), state)
        ages.append(c.quote_age_s)
    assert ages == [0.0, 90.0, 180.0, 0.0, 90.0]


def test_quote_age_forgets_contracts_that_drop_out():
    state, t0 = {}, datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    track_quotes([cand()], t0, state)
    track_quotes([], t0 + timedelta(seconds=90), state)             # no longer a candidate
    back = cand()
    track_quotes([back], t0 + timedelta(seconds=900), state)
    assert back.quote_age_s == 0.0 and len(state) == 1


def test_quote_age_is_stored_on_the_trade_and_blank_when_untracked():
    c = cand()
    c.quote_age_s = 180.0
    assert _trade_row(c, "T|yes")["quote_age_s"] == "180"
    assert _trade_row(cand(), "T|yes")["quote_age_s"] == ""
