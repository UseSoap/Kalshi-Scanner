# Kalshi-Scanner

A **paper-trading** scanner for near-certain Kalshi sports contracts. It places no orders and needs no Kalshi credentials. Its job is to answer one question with real data before any money is risked:

> When a same-day sports contract is priced at 95-99¢, does it actually win more often than the price implies, after fees?

## What it does

A GitHub Action starts every 30 minutes during game hours (hourly overnight) and runs an adaptive loop:

0. **Adapts its pace to the games.** Kalshi gives each game's *expected end time*, so each game gets a watch window from 3 hours before that to 90 minutes after (overtime and extra innings run long). Inside a window it scans every 90 seconds, or every 45 seconds once any contract is priced 90¢+ (a game is getting lopsided). If a window opens within 30 minutes it waits; if nothing is on, it exits within seconds so it stops using compute. Tune with `--window-before`, `--window-after`, `--hot-ask`, `--hot-interval`, `--live-interval`.
1. **Settles** any earlier paper trades whose games have finished, using Kalshi's recorded result.
2. **Scans** the configured sports series (default: MLB, NHL, NBA game winners) for contracts that end today (US Central) where one side's ask is 95-99¢ with a tight bid/ask spread.
3. **Checks the order book** for each candidate. A paper trade is opened only if the full order (100 contracts) could fill at 99¢ or better. The entry price is the *average fill price* across the book, not the top-of-book ask. Thin books are logged but not traded.
4. **Logs** each candidate to `data/snapshots/<date>.csv` and opens a paper trade (one per contract and side) in `data/trades.csv`, with Kalshi's fee applied at the fill price.
5. **Commits** the new data back to this repo.

## First-time setup

1. In this repo go to **Settings > Actions > General** and make sure Actions are allowed, and that workflow permissions are set to **Read and write**.
2. Go to the **Actions** tab, pick **scan**, and press **Run workflow**. Open the run and read the log.
3. **Check the series tickers.** The defaults (`KXMLBGAME`, `KXNHLGAME`, `KXNBAGAME`) are my best recollection of Kalshi's naming. If the log says `0 candidates` on a day with games, run `python -m kalshi_scanner discover` (locally or in Colab) to list the real series, then set a repo variable named `KALSHI_SERIES` under **Settings > Secrets and variables > Actions > Variables**, for example `KXMLBGAME,KXNHLGAME,KXNBAGAME`.
4. Let it run for a few weeks, then open `notebooks/analysis.ipynb` in Google Colab (instructions are in the first cell).

## Reading the results

```
python -m kalshi_scanner report
```

prints hit rate vs. implied probability by price bucket and by sport, with a 95% confidence interval, plus a **synthetic combo simulation** (stack one favorite per game until the combined odds are about 50%, once per day). Edge only exists if the realized hit rate beats the break-even rate after fees, and the confidence interval stays above it. Early on, intervals are very wide, so don't trust a few days of data.

## Things to know

- **Fees are per order, rounded up to the next cent.** 1 contract at 98¢ pays a full cent in fees, while 100 contracts pay about 0.2¢ each. The fee rate in `kalshi_scanner/fees.py` (0.07) should be checked against Kalshi's current fee schedule.
- **Combining legs does not create edge.** Multiplying 95-99% legs only changes variance. The combo simulation here is an upper bound, because real Kalshi combo markets carry market-maker margin. Same-sport legs on one day can also be correlated.
- **"Ends today" uses the game's expected end time**, not Kalshi's `close_time`, which can be days later on sports markets.
- **Paper fills are still somewhat optimistic.** The depth check removes thin books and prices in slippage, but the book can change in the minutes between scans, and a real order competes with other buyers. `--skip-depth` turns the check off (fills at the top ask), which flatters the results.
- If the log warns that the **order book was unreadable**, no paper trade is opened for those candidates. Paste the log to Claude so the parser can be adjusted.
- GitHub's cron timing is approximate, so a run can start a few minutes late, leaving short gaps between 27-minute loops.
- **GitHub Actions minutes are limited on private repos** (as I understand it, 2,000 per month on the free plan; check Settings > Billing). Scanning every minute through evening games can use far more than that. Public repos get free minutes, and nothing in this repo is secret (no credentials are stored). Otherwise, run `python -m kalshi_scanner loop --max-minutes 0` on any always-on computer.
- Markets that Kalshi still lists as open up to 3 hours after a game's expected end are scanned too, since games run long.

## Layout

```
kalshi_scanner/   client, scanner, fees, settlement, report, combo simulator
tests/            mock-based tests (python -m pytest)
data/             snapshots and paper trades, committed by the Action
notebooks/        Colab analysis notebook
.github/workflows/scan.yml
```

Live trading is intentionally **not** implemented. If the paper results show a real edge, it would be added later with Kalshi's demo environment first, API keys stored only in GitHub/Colab secrets, dry-run by default, and hard caps on position size and daily loss.
