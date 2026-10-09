"""Second-tier analysis: is waiting for the price to reach 95c better than buying at the first 90c+ price?

The main scanner buys a game once, at the first scan that shows a contract at the entry floor, and never looks
at that game again. When the same contract later reaches `tier_ask`, storage.record_tier_trades logs a second,
separate paper trade in data/trades_95.csv. Nothing here reads or changes the main P&L: the main report is
built from trades.csv alone.

Because the two trades are the same contract, they win or lose together, so on the games that reached 95c
the hit rate is identical and only the price paid differs. The real question waiting answers is a different
one: do the games that never reach 95c do worse than the games that do? That is what sections 1 and 2 set side
by side.
"""

from __future__ import annotations

from pathlib import Path

from .insights import edge_verdict
from .report import _money, _pct, settled, summarize, table_header, table_row
from .scanner import parse_ts
from .storage import TIER_FILE, load_trades, tier_start

W = 36   # label column width


def _table(rows: list[tuple[str, dict]]) -> list[str]:
    return ["  " + table_header(W)] + ["  " + table_row(label, s, W) for label, s in rows]


def _per_trade(s: dict) -> str:
    return _money(s["pnl_usd"] / s["n"]) if s["n"] else "n/a"


def split_cohort(base: list[dict], tier: list[dict], tier_ask: float, start) -> dict:
    """Group the settled trades the tier analysis can fairly compare.

    Only main trades opened after the tier was switched on are used, since earlier ones never had a
    chance to get a tier trade. Returns lists of trades keyed "waited", "waited_base" (the same games'
    main trades, in the same order), "jumped", "jumped_base", "skipped" and counts of open ones.
    """
    base_by_id = {}
    for t in base:
        ts = parse_ts(t.get("first_seen"))
        if start is not None and ts is not None and ts >= start:
            base_by_id[t["trade_id"]] = t
    tier = [t for t in tier if t["base_trade_id"] in base_by_id]
    tiered_ids = {t["base_trade_id"] for t in tier}

    waited, waited_base, jumped, jumped_base = [], [], [], []
    for t in settled(tier):
        b = base_by_id[t["base_trade_id"]]
        if b["status"] != "settled":
            continue
        if float(t["base_entry_price"]) < tier_ask:
            waited.append(t)
            waited_base.append(b)
        else:
            jumped.append(t)
            jumped_base.append(b)
    skipped = [b for tid, b in base_by_id.items() if b["status"] == "settled" and tid not in tiered_ids]
    return {"waited": waited, "waited_base": waited_base, "jumped": jumped, "jumped_base": jumped_base,
            "skipped": skipped, "tier_all": settled(tier), "tier_n": len(tier),
            "tier_open": sum(1 for t in tier if t["status"] == "open"),
            "base_n": len(base_by_id), "base_open": sum(1 for b in base_by_id.values() if b["status"] == "open")}


def render(data_dir: Path | None = None, tier_ask: float = 95.0) -> str:
    # The report's "6. Second tier" banner is printed by __main__; this is the description under it.
    lines = [f"What this tests: does waiting until the same contract reaches {tier_ask:g}c beat buying at the main trigger?",
             "Kept apart from the main P&L: it reads data/trades_95.csv only and is never added to sections 1-5."]
    start = tier_start(data_dir)
    if start is None:
        lines.append("Not started yet: the second tier begins logging on the next scan after this code is deployed.")
        return "\n".join(lines)
    tier = load_trades(data_dir, TIER_FILE)
    c = split_cohort(load_trades(data_dir), tier, tier_ask, start)
    lines.append(f"Watching since {start:%Y-%m-%d %H:%MZ}. Main trades opened since then: {c['base_n']} "
                 f"({c['base_open']} still open). Of those, {c['tier_n']} later reached {tier_ask:g}c "
                 f"({c['tier_open']} still open).")
    lines.append("These numbers are NOT added to the main report, and the two tiers overlap, so never sum them.")
    if not c["tier_all"] and not c["skipped"]:
        lines += ["", "Nothing settled yet in this group. Results appear as the games finish."]
        return "\n".join(lines)

    w, wb = c["waited"], c["waited_base"]
    lines += ["", f"1. Same games, two entry prices (main trade was under {tier_ask:g}c, same contract reached {tier_ask:g}c later)"]
    if w:
        sw, sb = summarize(w), summarize(wb)
        lines += _table([("bought at the main trigger", sb), (f"bought at {tier_ask:g}c", sw)])
        diff = sw["pnl_usd"] - sb["pnl_usd"]
        lines.append(f"  Same {sw['n']} games, so the hit rate is identical; only the price paid differs. "
                     f"P&L per game: {_per_trade(sb)} early vs {_per_trade(sw)} waiting "
                     f"(waiting {'gained' if diff >= 0 else 'lost'} {_money(abs(diff))} in total).")
    else:
        lines.append("  No settled pairs yet.")

    lines += ["", f"2. Games the {tier_ask:g}c tier skipped (main trigger bought, price never reached {tier_ask:g}c)"]
    if c["skipped"]:
        lines += _table([("main trigger only", summarize(c["skipped"]))])
        lines.append("  This is what waiting gives up. Waiting only pays if these games do clearly worse than the ones in section 1.")
    else:
        lines.append("  None settled yet.")

    lines += ["", f"3. Games already at {tier_ask:g}c+ on the first scan (both tiers entered together; set aside)"]
    if c["jumped"]:
        lines += _table([("same trade in both tiers", summarize(c["jumped"]))])
    else:
        lines.append("  None settled yet.")

    lines += ["", "4. Each rule as a whole (the 95c rule trades only the games in sections 1 and 3)"]
    rows = []
    main_all = c["waited_base"] + c["jumped_base"] + c["skipped"]
    if main_all:
        rows.append(("main trigger, every game", summarize(main_all)))
    if c["tier_all"]:
        rows.append((f"{tier_ask:g}c trigger, every game", summarize(c["tier_all"])))
    lines += _table(rows)
    if c["tier_all"]:
        s = summarize(c["tier_all"])
        lines.append("  " + edge_verdict(s["hit_rate"], s["ci_low"], s["ci_high"], s["breakeven"]))
    lines += ["", "Read it this way: the 95c rule beats the main rule only if (a) its ROI is higher in section 4 and",
              "(b) section 2's games are worse than section 1's, not just fewer. Games of the same contract are not",
              "independent samples: section 1 counts each game once, so its n is the real sample size. Expect",
              "several hundred settled games before any difference here means much."]
    return "\n".join(lines)
