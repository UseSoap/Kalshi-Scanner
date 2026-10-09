from kalshi_scanner import report


def trade(won, contracts, price="90"):
    return {"status": "settled", "won": "1" if won else "0", "entry_price": price, "contracts": str(contracts),
            "fee_usd": "0.5", "pnl_usd": "1.0", "series": "KXMLBGAME", "event_ticker": "e", "expiry": "2026-10-07T02:30:00Z"}


def test_weighted_hit_follows_contracts_and_drives_edge():
    s = report.summarize([trade(True, 40), trade(True, 40), trade(False, 100)])
    assert abs(s["hit_rate"] - 2 / 3) < 1e-9                    # each trade counts once
    assert abs(s["whit"] - 80 / 180) < 1e-9                     # winning contracts / contracts bought
    assert abs(s["edge"] - (s["whit"] - s["breakeven"])) < 1e-9


def test_even_sizes_make_the_two_hit_rates_equal():
    s = report.summarize([trade(True, 100), trade(True, 100), trade(False, 100)])
    assert abs(s["whit"] - s["hit_rate"]) < 1e-9


def test_table_shows_the_weighted_column():
    assert "wtd hit" in report.table_header()
    row = report.table_row("x", report.summarize([trade(True, 40), trade(False, 100)]))
    assert "28.6%" in row                                       # 40 / 140 contracts
