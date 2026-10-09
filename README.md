# Kalshi-Scanner

A **paper-trading** scanner for near-certain Kalshi sports contracts. It places no orders and needs no Kalshi credentials. Its job is to answer one question with real data before any money is risked:

> When a same-day sports contract is priced at 90-99¢, does it actually win more often than the price implies, after fees? And does the answer differ by sport or by price?

## What it does

A GitHub Action runs an adaptive loop. An external scheduler (cron-job.org) starts a run every 30 minutes during game hours (hourly overnight), and one hourly GitHub cron entry is a failsafe. Each loop:

0. **Adapts its pace to the games.** Kalshi gives each game's *expected end time*, so each game gets a watch window from 3 hours before that to 90 minutes after (overtime and extra innings run long). Inside a window it scans every 90 seconds, or every 45 seconds once any contract is priced 85¢+ (a game is getting lopsided; this sits below the 90¢ entry floor so scanning is already fast when a favorite crosses it). If a window opens within 30 minutes it waits; if nothing is on, it exits within seconds so it stops using compute. Tune with `--window-before`, `--window-after`, `--hot-ask`, `--hot-interval`, `--live-interval`.
1. **Settles** any earlier paper trades whose games have finished, using Kalshi's recorded result.
2. **Scans** the configured sports series for contracts that end today (US Central) where one side's ask is 90-99¢ and the bid/ask spread is 5¢ or less. The default series (39, listed in `SPORT_LABELS` in `kalshi_scanner/scanner.py`) cover MLB, NHL, NBA, NFL, college football, men's college basketball, WNBA, UFC, tour-level ATP and WTA tennis plus the Challenger and ITF levels, Korean and Japanese baseball (KBO, NPB), and soccer: MLS, the Premier League, La Liga, Serie A, the Bundesliga, Ligue 1, the Champions, Europa and Conference Leagues, Liga MX, Eredivisie, the EFL Championship, Liga Portugal, the Saudi Pro League, Brasileiro Serie A, Copa Libertadores, the Scottish Premiership, Serie B, Bundesliga 2, La Liga 2, Ligue 2, the FA Cup and the USL Championship. Leagues that are out of season just return no markets.
3. **Checks the order book** for each candidate and sizes the paper order to what could actually fill: as many contracts as the book offers, up to 100, without paying more than the quoted ask plus 2¢ (and never above 99¢). The entry price is the *average fill price* across the levels used, not the top-of-book ask. If fewer than 5 contracts could fill, the book is "thin" and the candidate is logged but not traded. Tune with `--contracts`, `--min-contracts` and `--max-slippage`.
4. **Logs** each candidate to `data/snapshots/<date>.csv` and opens a paper trade (one per game) in `data/trades.csv`, with the sport recorded and Kalshi's fee applied at the fill price and size. In a two-team game it buys the **cheaper side of the same bet** (see below). It also records every game it saw, and the highest ask on any of its contracts, in `data/games.csv`, so the report can count games that never reached the entry floor.
5. **Commits** the new data back to this repo.

## First-time setup

1. In this repo go to **Settings > Actions > General** and make sure Actions are allowed, and that workflow permissions are set to **Read and write**.
2. Go to the **Actions** tab, pick **scan**, and press **Run workflow**. Open the run and read the log.
3. **Check the series.** Every default ticker was confirmed against Kalshi's series list (`docs/kalshi_series.txt`). The first log line of each run lists markets seen per series, and a second line names series that returned nothing (off-season, or a ticker Kalshi has retired). To scan something other than the defaults, set a repo variable named `KALSHI_SERIES` under **Settings > Secrets and variables > Actions > Variables**, for example `KXMLBGAME,KXNHLGAME,KXNBAGAME`. **Leave it unset (or empty) to scan all the defaults:** when it is set, it replaces the default list, so newly added series are ignored.
4. Let it run for a few weeks, then open `notebooks/analysis.ipynb` in Google Colab (instructions are in the first cell).

## Reading the results

```
python -m kalshi_scanner report
```

prints, in order:

- **Data freshness** (when the last trade, settlement and candidate were logged) and **open positions** (money at risk and what resolves next).
- A **fills table**: paper fills by sport and entry-price bucket, open and settled, with average size.
- **ALL SETTLED**, with the **break-even hit rate after fees** and the **edge** (hit rate minus break-even, in percentage points), plus a one-line verdict on whether the 95% confidence interval clears break-even. Every table below uses the same columns, and negative numbers show in parentheses.
- **Win/loss size** (average win vs. average loss, profit factor), **results by game day** (daily and cumulative P&L, max drawdown, current streak), and **how much data is enough** (trades needed to prove a 1, 2 or 3 point edge, and to confirm the observed rate).
- The same table **by price bucket, by sport, by sport within each bucket**, then **by side bought, by bid/ask spread, and by entry time** relative to the game's listed end.
- **Fill quality** (entry price vs. the quote that flagged the trade, partial fills, fees, spread) and the **scan funnel by game day** (games observed, games that hit the entry floor, were fillable, and were traded; the two sides of a game count once).
- A **synthetic combo simulation** (stack one favorite per game until the combined odds are about 50%, once per day).

Edge only exists if the realized hit rate beats the break-even rate after fees, and the confidence interval stays above it. Early on, intervals are very wide, so don't trust a few days of data.

## Things to know

- **Second tier: wait for 95c (separate experiment).** When a contract that already has a main paper trade later reaches a 95c ask, a second paper trade on that same contract is logged to `data/trades_95.csv` (never to `trades.csv`). The report prints it as its own "SECOND TIER" section after the combo simulation, and none of it feeds the main P&L. Because both trades are the same contract they win or lose together, so the comparison is about the price paid and about the games that never reached 95c (what waiting gives up). Only main trades opened after the tier was switched on (`data/trades_95_start.txt`) are compared. Tune with `--second-tier-ask` or turn off with `--no-second-tier`.
- **One paper trade per game.** Contracts in the same game move together (a soccer game has win, draw and loss contracts, and a game that flips can qualify on both sides), so counting each as a trade would overstate the sample size and make the confidence intervals look too tight. A game that already has a trade is skipped, and every candidate is still logged in the snapshots. `--multi-per-game` turns the rule off.
- **The cheaper side wins in two-team games.** "Team A wins" YES and "Team B wins" NO are the same bet, but they trade in separate order books and can sit a cent or two apart. When one side reaches the entry floor, the scanner also looks at the other side, even down to 2¢ under the floor (`--mirror-tolerance`, 0 turns it off), and the paper trade goes to whichever fills cheaper (ties go to the larger fill). Such a trade can therefore enter at 88-89¢ and lands in the `<90c` bucket; `trades.csv` records `via_mirror` (1 if the contract was itself under the floor) and `partner_fill` (the other side's price). This only applies where a draw is impossible. Soccer leagues keep the old rule (the contracts are different bets, so the highest-priced one is taken), as does any game with more than two markets. KBO and NPB are treated like soccer here because regular-season baseball games there can end tied after extra innings. The leagues affected are listed in `DRAW_POSSIBLE` in `scanner.py`; any new league where a game can end level belongs in that set.
- **Adding a series.** Look the ticker up in `docs/kalshi_series.txt`, add it to `SPORT_LABELS` (and to `DRAW_POSSIBLE` if a game can end level), and run the tests; one test checks that every configured ticker appears in that file. Prefer plain game-winner series (`...GAME`, `...MATCH`, `...FIGHT`). Spread, total and BTTS series for the same games are separate series with different event tickers, so they would count one game twice. Several leagues also have two similar series (for example `KXATPGAME` and `KXATPMATCH`); only one of each is configured until the overlap is checked against live data.
- **Overnight games.** KBO and NPB games finish around 6-8 AM Central, and the external scheduler only starts a run hourly from 2 AM to 10 AM. A run lasts up to about 27 minutes, so roughly half of the late-game window is missed unless the scheduler also fires at :32 in those hours.
- **Fees are per order, rounded up to the next cent.** 1 contract at 98¢ pays a full cent in fees, while 100 contracts pay about 0.2¢ each. Because fills are now sized to the book, small fills carry proportionally heavier fees, so read hit rate vs. implied first and treat ROI on small fills as pessimistic. The fee rate in `kalshi_scanner/fees.py` (0.07) should be checked against Kalshi's current fee schedule.
- **Combining legs does not create edge.** Multiplying 95-99% legs only changes variance. The combo simulation here is an upper bound, because real Kalshi combo markets carry market-maker margin. Same-sport legs on one day can also be correlated.
- **"Ends today" uses the game's expected end time**, not Kalshi's `close_time`, which can be days later on sports markets.
- **The 90-93¢ and 93-95¢ buckets are a wider net than the original question.** They are there to get more trades and to show where, if anywhere, the hit rate beats the price. Judge each bucket on its own.
- **Paper fills are still somewhat optimistic.** The depth check removes thin books and prices in slippage, but the book can change in the minutes between scans, and a real order competes with other buyers. `--skip-depth` turns the check off (fills at the top ask), which flatters the results.
- If the log warns that the **order book was unreadable**, no paper trade is opened for those candidates. Paste the log to Claude so the parser can be adjusted.
- **GitHub's cron is best-effort, so it is not the main trigger.** Scheduled runs are often late or dropped. Runs are started by a cron-job.org job (timezone America/Chicago, so daylight saving is handled) that sends a POST to `https://api.github.com/repos/UseSoap/Kalshi-Scanner/actions/workflows/scan.yml/dispatches` with body `{"ref":"main"}` at :02 and :32 from 11 AM to 1:59 AM Central, and at :02 hourly from 2 AM to 10 AM. It uses a fine-grained GitHub token with **Actions: Read and write** on this repo only; the token lives in cron-job.org, never in the repo. Runs do not re-dispatch themselves; the scheduler is the only thing that starts them. One GitHub cron entry (:47 hourly) remains as a failsafe if cron-job.org stops working. To restart by hand, press **Run workflow**.
- Only one run waits in the queue at a time, so external starts and the failsafe cron never pile up. A queued run that gets replaced by a newer one shows as cancelled in the Actions tab; that is expected.
- **GitHub Actions minutes are limited on private repos** (as I understand it, 2,000 per month on the free plan; check Settings > Billing), and loops running through game hours use them steadily. This repo is public, so minutes are free, and nothing in it is secret (no credentials are stored). If it goes private, switch to an always-on computer running `python -m kalshi_scanner loop --max-minutes 0`.
- Markets that Kalshi still lists as open up to 3 hours after a game's expected end are scanned too, since games run long.

## Layout

```
kalshi_scanner/   client, scanner, order book, fees, settlement, report (+ insights: extra stats), combo simulator
tests/            mock-based tests (python -m pytest)
docs/             kalshi_series.txt: Sports series that matched `discover`'s keywords (ticker, title), Oct 2026.
                  Regenerate with `python -m kalshi_scanner discover`, or add `--all-series` for the full list.
data/             snapshots, paper trades and games seen, committed by the Action
notebooks/        Colab analysis notebook
.github/workflows/scan.yml
```

Live trading is intentionally **not** implemented. If the paper results show a real edge, it would be added later with Kalshi's demo environment first, API keys stored only in GitHub/Colab secrets, dry-run by default, and hard caps on position size and daily loss.
