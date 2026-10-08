"""Find near-certain, same-day sports contracts and describe them."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .client import KalshiError
from .fees import breakeven_probability, win_loss
from .orderbook import fill_estimate, parse_book

# Series tickers for game-winner markets, confirmed against `python -m kalshi_scanner discover`.
# Override with the KALSHI_SERIES environment variable or --series. A ticker that does not
# exist (or a league that is out of season) simply returns no markets; it breaks nothing.
SPORT_LABELS = {
    "KXMLBGAME": "MLB",
    "KXNHLGAME": "NHL",
    "KXNBAGAME": "NBA",
    "KXNFLGAME": "NFL",
    "KXNCAAFGAME": "College football",
    "KXNCAAMBGAME": "College basketball (M)",
    "KXWNBAGAME": "WNBA",
    "KXMLSGAME": "MLS",
    "KXEPLGAME": "Premier League",
    "KXLALIGAGAME": "La Liga",
    "KXSERIEAGAME": "Serie A",
    "KXBUNDESLIGAGAME": "Bundesliga",
    "KXLIGUE1GAME": "Ligue 1",
    "KXUCLGAME": "Champions League",
    "KXATPMATCH": "Tennis (ATP)",
    "KXWTAMATCH": "Tennis (WTA)",
    "KXUFCFIGHT": "UFC",
    # Added after running `discover` (Oct 2026): all of these tickers were seen in Kalshi's series list.
    # More soccer
    "KXUELGAME": "Europa League",
    "KXUECLGAME": "Conference League",
    "KXLIGAMXGAME": "Liga MX",
    "KXEREDIVISIEGAME": "Eredivisie",
    "KXEFLCHAMPIONSHIPGAME": "EFL Championship",
    "KXLIGAPORTUGALGAME": "Liga Portugal",
    "KXSAUDIPLGAME": "Saudi Pro League",
    "KXBRASILEIROGAME": "Brasileiro Serie A",
    "KXCONMEBOLLIBGAME": "Copa Libertadores",
    "KXSCOTTISHPREMGAME": "Scottish Premiership",
    "KXSERIEBGAME": "Serie B",
    "KXBUNDESLIGA2GAME": "Bundesliga 2",
    "KXLALIGA2GAME": "La Liga 2",
    "KXLIGUE2GAME": "Ligue 2",
    "KXFACUPGAME": "FA Cup",
    "KXUSLGAME": "USL Championship",
    # More tennis (the plain ATP/WTA tour series are above)
    "KXATPCHALLENGERMATCH": "Tennis (ATP Challenger)",
    "KXWTACHALLENGERMATCH": "Tennis (WTA Challenger)",
    "KXITFMATCH": "Tennis (ITF men)",
    "KXITFWMATCH": "Tennis (ITF women)",
    # Foreign baseball (plays overnight US time)
    "KXKBOGAME": "KBO",
    "KXNPBGAME": "NPB",
}
DEFAULT_SERIES = list(SPORT_LABELS)

# Leagues where a game can end in a draw or tie. There "Team A wins" YES and "Team B wins" NO are NOT the
# same bet (NO also wins on a draw), so the two sides of a game are never treated as interchangeable.
# Soccer is the obvious case; KBO and NPB regular-season games can also end tied after extra innings.
DRAW_POSSIBLE = {"KXMLSGAME", "KXEPLGAME", "KXLALIGAGAME", "KXSERIEAGAME", "KXBUNDESLIGAGAME",
                 "KXLIGUE1GAME", "KXUCLGAME",
                 "KXUELGAME", "KXUECLGAME", "KXLIGAMXGAME", "KXEREDIVISIEGAME", "KXEFLCHAMPIONSHIPGAME",
                 "KXLIGAPORTUGALGAME", "KXSAUDIPLGAME", "KXBRASILEIROGAME", "KXCONMEBOLLIBGAME",
                 "KXSCOTTISHPREMGAME", "KXSERIEBGAME", "KXBUNDESLIGA2GAME", "KXLALIGA2GAME",
                 "KXLIGUE2GAME", "KXFACUPGAME", "KXUSLGAME",
                 "KXKBOGAME", "KXNPBGAME"}


def sport_of(series: str) -> str:
    """Human-readable sport/league for a series ticker (falls back to the ticker itself)."""
    series = (series or "").upper()
    if series in SPORT_LABELS:
        return SPORT_LABELS[series]
    name = series[2:] if series.startswith("KX") else series
    for suffix in ("GAME", "MATCH", "FIGHT"):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name or "unknown"


OPEN_STATUSES = {"open", "active"}


@dataclass
class Config:
    series: list[str] = field(default_factory=lambda: list(DEFAULT_SERIES))
    min_price: float = 90.0       # cents; minimum ask on the favored side
    max_price: float = 99.0       # cents; above this there is no room for profit
    max_spread: float = 5.0       # cents; skip contracts nobody is quoting tightly
    min_volume: int = 0
    contracts: int = 100          # largest paper order; fills as many as the book allows, up to this
    min_contracts: int = 5        # smallest fill worth recording; below this the book is "thin"
    max_slippage: float = 2.0     # cents; never pay more than the quoted ask plus this
    fee_rate: float = 0.07
    timezone: str = "America/Chicago"
    same_day_only: bool = True
    expiry_grace_min: int = 180   # accept still-open markets this long past the expected end (overtime, delays)
    check_depth: bool = True      # fetch the order book and size the order to what can fill
    one_trade_per_event: bool = True   # one paper trade per game; correlated contracts are not extra samples
    mirror_tolerance: float = 2.0      # cents below min_price at which the other side of the same bet is still offered


@dataclass
class Candidate:
    ts: str
    ticker: str
    event_ticker: str
    series: str
    title: str
    side: str
    ask: float
    bid: float | None
    spread: float | None
    volume: int
    open_interest: int
    expiry: str
    contracts: int
    fee_usd: float
    win_usd: float
    loss_usd: float
    breakeven_prob: float
    # Filled in by apply_depth(). entry price for paper P&L is fill_price (average over the book),
    # and `contracts` becomes the size that could actually fill (up to Config.contracts).
    fill_price: float | None = None
    depth_at_ask: float | None = None
    depth_status: str = "unchecked"   # unchecked | ok | thin | unknown
    fillable: int | None = None       # contracts fillable within the slippage limit, capped at Config.contracts
    # Two-team games only (not logged in snapshots): the other side of the same bet, as "ticker|side".
    mirror_key: str = ""
    via_mirror: bool = False          # True if this contract was offered only because its mirror reached the floor


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def price_cents(market: dict, name: str):
    """Read a price in cents, preferring sub-penny `<name>_dollars` fields when present."""
    dollars = _num(market.get(f"{name}_dollars"))
    if dollars is not None:
        return round(dollars * 100.0, 4)
    return _num(market.get(name))


def parse_ts(value: str | None):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def market_expiry(market: dict):
    """When the outcome is expected to be known.

    `expected_expiration_time` reflects the game; `close_time` on sports markets can sit
    days or weeks later, so it is only a fallback.
    """
    return parse_ts(market.get("expected_expiration_time")) or parse_ts(market.get("close_time"))


def sides(market: dict):
    """Yield (side, ask_cents, bid_cents) for each side that has an ask."""
    yes_bid = price_cents(market, "yes_bid")
    yes_ask = price_cents(market, "yes_ask")
    no_bid = price_cents(market, "no_bid")
    no_ask = price_cents(market, "no_ask")
    if no_ask is None and yes_bid is not None:
        no_ask = round(100.0 - yes_bid, 4)
    if no_bid is None and yes_ask is not None:
        no_bid = round(100.0 - yes_ask, 4)
    if yes_ask is not None and yes_ask > 0:
        yield "yes", yes_ask, yes_bid
    if no_ask is not None and no_ask > 0:
        yield "no", no_ask, no_bid


def evaluate(market: dict, now: datetime, cfg: Config, diag: dict | None = None) -> list[Candidate]:
    """Return candidates for one market. If `diag` is given, record why markets were rejected."""

    def reject(reason: str):
        if diag is not None:
            diag["reasons"][reason] += 1
        return []

    if str(market.get("status", "")).lower() not in OPEN_STATUSES:
        return reject("not open")
    expiry = market_expiry(market)
    if expiry is None:
        return reject("no end time")
    if diag is not None and now - timedelta(hours=6) < expiry < now + timedelta(hours=36):
        diag["expiries"].add(expiry.replace(second=0, microsecond=0))
    if expiry <= now - timedelta(minutes=cfg.expiry_grace_min):
        return reject("end time long past")
    tz = ZoneInfo(cfg.timezone)
    if cfg.same_day_only and expiry.astimezone(tz).date() != now.astimezone(tz).date():
        return reject("ends on another day")
    volume = int(_num(market.get("volume")) or 0)
    if volume < cfg.min_volume:
        return reject("volume too low")

    all_sides = list(sides(market))
    if diag is not None:
        diag["same_day"] += 1
        for side, ask, bid in all_sides:
            diag["top"].append((ask, bid, market["ticker"], side))
        # One entry per game (event), with the highest ask seen on any of its contracts. This is how the
        # report knows how many games were on, including the ones that never reached the entry floor.
        game = diag["games"].setdefault(market.get("event_ticker") or market["ticker"], {
            "series": market.get("series_ticker") or market.get("event_ticker", "").split("-")[0],
            "expiry": expiry.astimezone(timezone.utc).isoformat(timespec="seconds"), "peak_ask": 0.0})
        game["peak_ask"] = max([game["peak_ask"]] + [ask for _side, ask, _bid in all_sides])

    out = []
    for side, ask, bid in all_sides:
        if not (cfg.min_price <= ask <= cfg.max_price):
            if diag is not None:
                diag["reasons"]["ask outside price range"] += 1
            continue
        spread = round(ask - bid, 4) if bid is not None else None
        if spread is None or spread > cfg.max_spread:
            if diag is not None:
                diag["reasons"]["spread too wide or no bid"] += 1
            continue
        win, loss, fee = win_loss(ask, cfg.contracts, cfg.fee_rate)
        out.append(Candidate(
            ts=now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            ticker=market["ticker"],
            event_ticker=market.get("event_ticker", ""),
            series=(market.get("series_ticker") or market.get("event_ticker", "").split("-")[0]),
            title=market.get("title", ""),
            side=side,
            ask=ask,
            bid=bid,
            spread=spread,
            volume=volume,
            open_interest=int(_num(market.get("open_interest")) or 0),
            expiry=expiry.astimezone(timezone.utc).isoformat(timespec="seconds"),
            contracts=cfg.contracts,
            fee_usd=fee,
            win_usd=round(win, 4),
            loss_usd=round(loss, 4),
            breakeven_prob=round(breakeven_probability(ask, cfg.contracts, cfg.fee_rate), 5),
        ))
    return out


def apply_depth(client, cand: Candidate, cfg: Config) -> Candidate:
    """Check the order book and size the paper order to what could actually fill.

    The order takes as many contracts as the book offers, up to `cfg.contracts`, without
    paying more than the quoted ask plus `cfg.max_slippage` (and never above `cfg.max_price`).
    It is repriced at the average fill price. Fewer than `cfg.min_contracts` fillable is "thin".
    """
    try:
        book = parse_book(client.get_orderbook(cand.ticker))
    except KalshiError:
        book = None
    if book is None:
        cand.depth_status = "unknown"
        return cand
    limit = min(cfg.max_price, cand.ask + cfg.max_slippage)
    est = fill_estimate(book, cand.side, cfg.contracts, limit)
    cand.depth_at_ask = round(est["depth_at_ask"], 2)
    cand.fillable = est["filled"]
    if est["vwap"] is None or est["filled"] < cfg.min_contracts:
        cand.depth_status = "thin"
        return cand
    fill = est["vwap"]
    cand.contracts = est["filled"]
    win, loss, fee = win_loss(fill, cand.contracts, cfg.fee_rate)
    cand.fill_price = round(fill, 4)
    cand.fee_usd, cand.win_usd, cand.loss_usd = fee, round(win, 4), round(loss, 4)
    cand.breakeven_prob = round(breakeven_probability(fill, cand.contracts, cfg.fee_rate), 5)
    cand.depth_status = "ok"
    return cand


def new_diag() -> dict:
    return {"reasons": Counter(), "same_day": 0, "top": [], "per_series": {}, "expiries": set(), "games": {}}


def _binary_pairs(markets: list[dict], series: str) -> dict[str, tuple[dict, dict]]:
    """Games with exactly two markets (one per team) in a league where a draw is impossible.

    In these games "Team A wins" YES and "Team B wins" NO pay out together: they are the same bet.
    """
    if series.upper() in DRAW_POSSIBLE:
        return {}
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        if m.get("event_ticker"):
            by_event[m["event_ticker"]].append(m)
    return {ev: (ms[0], ms[1]) for ev, ms in by_event.items() if len(ms) == 2 and ms[0]["ticker"] != ms[1]["ticker"]}


def _add_mirrors(client, markets: list[dict], found: list[Candidate], seen: set, now: datetime,
                 cfg: Config, series: str) -> list[Candidate]:
    """Link the two sides of each two-team bet, and offer the cheaper one when it lags the entry floor.

    The two sides trade in separate order books, so one can sit a cent or two below the other. When a
    side reaches the entry floor, its mirror is offered too, even if it is up to `cfg.mirror_tolerance`
    cents under the floor, so the paper trade can go to whichever is cheaper. Every candidate in such a
    game gets a `mirror_key` so the trade logic knows to prefer the lower price.
    """
    pairs = _binary_pairs(markets, series)
    if not pairs:
        return []
    relaxed = replace(cfg, min_price=cfg.min_price - cfg.mirror_tolerance)
    extra: list[Candidate] = []
    for cand in list(found):
        pair = pairs.get(cand.event_ticker)
        if pair is None:
            continue
        partner = pair[1] if pair[0]["ticker"] == cand.ticker else pair[0]
        opposite = "no" if cand.side == "yes" else "yes"
        cand.mirror_key = f"{partner['ticker']}|{opposite}"
        key = (partner["ticker"], opposite)
        if key in seen or not (cfg.one_trade_per_event and cfg.mirror_tolerance > 0):
            continue
        seen.add(key)
        for mirror in evaluate(partner, now, relaxed):
            if mirror.side != opposite:
                continue
            mirror.mirror_key = f"{cand.ticker}|{cand.side}"
            mirror.via_mirror = mirror.ask < cfg.min_price
            if cfg.check_depth:
                apply_depth(client, mirror, cfg)
            extra.append(mirror)
    return extra


def scan(client, cfg: Config, now: datetime | None = None, diag: dict | None = None) -> list[Candidate]:
    """Scan every configured series. If `diag` (see new_diag) is given, it is filled with diagnostics."""
    now = now or datetime.now(timezone.utc)
    found, seen = [], set()
    for series in cfg.series:
        markets = list(client.list_markets(series_ticker=series, status="open"))
        if diag is not None:
            diag["per_series"][series] = len(markets)
        mine: list[Candidate] = []
        for market in markets:
            market.setdefault("series_ticker", series)
            for cand in evaluate(market, now, cfg, diag):
                key = (cand.ticker, cand.side)
                if key in seen:
                    continue
                seen.add(key)
                if cfg.check_depth:
                    apply_depth(client, cand, cfg)
                    if diag is not None and cand.depth_status == "thin":
                        diag["reasons"][f"thin book (under {cfg.min_contracts} contracts fillable)"] += 1
                    elif diag is not None and cand.depth_status == "unknown":
                        diag["reasons"]["order book unreadable"] += 1
                mine.append(cand)
        found += mine + _add_mirrors(client, markets, mine, seen, now, cfg, series)
    return found
