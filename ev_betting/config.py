"""Settings, read from environment variables (GitHub repo variables/secrets) with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# alias -> (The Odds API sport key, Kalshi game-winner series, label)
SPORTS = {
    "nba": ("basketball_nba", "KXNBAGAME", "NBA"),
    "nhl": ("icehockey_nhl", "KXNHLGAME", "NHL"),
    "nfl": ("americanfootball_nfl", "KXNFLGAME", "NFL"),
    "ncaaf": ("americanfootball_ncaaf", "KXNCAAFGAME", "College football"),
    "ncaab": ("basketball_ncaab", "KXNCAAMBGAME", "College basketball (M)"),
    "mlb": ("baseball_mlb", "KXMLBGAME", "MLB"),
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ev"


def _f(name, default):
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else default
    except ValueError:
        return default


@dataclass
class Settings:
    sports: list[str] = field(default_factory=lambda: ["nba", "nhl"])
    sharp_books: list[str] = field(default_factory=lambda: ["pinnacle"])
    bankroll: float = 350.0        # starting paper bankroll; settled P&L is added to it
    kelly: float = 0.25            # Kelly multiplier
    max_stake: float = 0.02        # cap per bet as a share of current bankroll
    min_ev: float = 0.02           # minimum EV after fees to open a paper trade
    max_line_age: float = 15.0     # minutes; older Pinnacle lines are ignored
    fee_rate: float = 0.07         # Kalshi taker fee rate (verify against Kalshi's schedule)
    min_credits: int = 20          # stop calling The Odds API below this many remaining credits
    odds_api_key: str = ""
    ntfy_topic: str = ""
    data_dir: Path = DATA_DIR

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        sports = os.environ.get("EV_SPORTS", "").strip()
        if sports:
            s.sports = [x.strip().lower() for x in sports.split(",") if x.strip()]
        sharp = os.environ.get("EV_SHARP_BOOKS", "").strip()
        if sharp:
            s.sharp_books = [x.strip() for x in sharp.split(",") if x.strip()]
        s.bankroll = _f("EV_BANKROLL", s.bankroll)
        s.kelly = _f("EV_KELLY", s.kelly)
        s.max_stake = _f("EV_MAX_STAKE", s.max_stake)
        s.min_ev = _f("EV_MIN_EV", s.min_ev)
        s.max_line_age = _f("EV_MAX_LINE_AGE", s.max_line_age)
        s.fee_rate = _f("EV_FEE_RATE", s.fee_rate)
        s.odds_api_key = os.environ.get("ODDS_API_KEY", "").strip()
        s.ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()
        return s
