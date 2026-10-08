from pathlib import Path

from kalshi_scanner.scanner import DEFAULT_SERIES, DRAW_POSSIBLE, SPORT_LABELS, _binary_pairs, sport_of

SERIES_FILE = Path(__file__).resolve().parent.parent / "docs" / "kalshi_series.txt"


def known_series() -> dict[str, str]:
    rows = {}
    for line in SERIES_FILE.read_text().splitlines():
        parts = line.split(None, 1)
        if parts:
            rows[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return rows


def test_every_configured_series_exists_in_kalshis_list():
    known = known_series()
    assert len(known) > 1000                      # the file was read, not an empty stub
    missing = [t for t in DEFAULT_SERIES if t not in known]
    assert not missing, f"not in docs/kalshi_series.txt (typo, or Kalshi retired it): {missing}"


def test_draw_possible_series_are_all_configured():
    assert DRAW_POSSIBLE <= set(SPORT_LABELS)


def test_every_series_has_a_distinct_label():
    labels = list(SPORT_LABELS.values())
    assert len(labels) == len(set(labels))
    assert sport_of("KXKBOGAME") == "KBO" and sport_of("KXITFWMATCH") == "Tennis (ITF women)"


def test_leagues_that_can_end_level_are_not_paired_as_one_bet():
    two_markets = [{"ticker": "T-A", "event_ticker": "E"}, {"ticker": "T-B", "event_ticker": "E"}]
    for series in ("KXKBOGAME", "KXNPBGAME", "KXUELGAME", "KXFACUPGAME"):
        assert _binary_pairs(two_markets, series) == {}
    for series in ("KXMLBGAME", "KXATPCHALLENGERMATCH", "KXITFMATCH", "KXWTACHALLENGERMATCH"):
        assert list(_binary_pairs(two_markets, series)) == ["E"]
