# EV scanner: setup and daily use

The `ev-scan` workflow runs by itself every 2 hours from 11 AM to 11 PM Central. Each run:

1. **Settles** paper trades whose games have finished, using Kalshi's official result.
2. **Pulls Pinnacle moneylines** from The Odds API and devigs them four ways (multiplicative, additive,
   power, Shin), keeping the lowest fair probability for each side.
3. **Builds the board**: every Kalshi NBA/NHL game-winner contract next to its fair price, with EV after
   Kalshi's taker fee.
4. **Updates closing lines** on open paper trades (the newest Pinnacle fair price before the game starts).
5. **Opens paper trades** on anything at or above the minimum EV, sized at quarter Kelly with a 2% cap
   and limited to what the order book could actually fill. One trade per game.
6. **Notifies your phone** for each new +EV paper trade, and with a summary when trades settle.
7. **Commits** everything to `data/ev/`.

Nothing here places real orders or needs Kalshi credentials.

## One-time setup (about 10 minutes)

### 1. Add your Odds API key as a secret
1. On GitHub, open this repo, then **Settings > Secrets and variables > Actions**.
2. Under **Repository secrets**, click **New repository secret**.
3. Name: `ODDS_API_KEY`. Secret: your key from the-odds-api.com. Click **Add secret**.

### 2. Turn on phone notifications (ntfy, free, no account)
1. Install the **ntfy** app (iPhone App Store or Google Play).
2. Tap **+** (subscribe to topic) and enter the topic name Claude gave you. Leave the server as ntfy.sh.
3. On iPhone, allow notifications when asked.
4. Make sure a secret named `NTFY_TOPIC` exists with that same topic name (add it like step 1 if not).
   Anyone who knows the topic name can read or send messages to it, so keep it private.

### 3. Check Actions permissions
**Settings > Actions > General > Workflow permissions** must be **Read and write permissions**.

### 4. Test it
1. Open the **Actions** tab, click **ev-scan** in the left list, then **Run workflow**.
2. Tick **Only send a test phone notification**, then click the green **Run workflow**. Your phone should buzz.
3. Run it again with the box unticked. That's a real scan. When it finishes (about a minute), open
   `data/ev/STATUS.md` in the repo.

## Where to look

| File | What's in it |
|---|---|
| `data/ev/STATUS.md` | **Start here.** Paper results, open trades, health check, and the full board. Reads well on the GitHub mobile app. |
| `data/ev/paper_trades.csv` | Every paper trade. Columns A-J match the workbook's Bet Tracker, so you can paste them in. |
| `data/ev/board_latest.csv` | The full board from the latest run, including each devig method's fair price. |
| `data/ev/runs.csv` | One line per run: credits left, games matched, +EV count, errors. |

## Settings (optional)

Set any of these under **Settings > Secrets and variables > Actions > Variables**. Leave unset for the default.

| Variable | Default | Meaning |
|---|---|---|
| `EV_SPORTS` | `nba,nhl` | Also `nfl`, `ncaaf`, `ncaab`, `mlb`. Each sport costs about 210 Odds API credits a month. |
| `EV_BANKROLL` | `350` | Starting paper bankroll. Settled profit and loss is added to it for sizing. |
| `EV_KELLY` | `0.25` | Kelly multiplier. |
| `EV_MAX_STAKE` | `0.02` | Max share of bankroll on one bet. |
| `EV_MIN_EV` | `0.02` | Minimum EV after fees to open a paper trade. |
| `EV_MAX_LINE_AGE` | `15` | Ignore Pinnacle lines older than this many minutes. |

## Things to know

- **Odds API credits.** The free plan has 500 a month. 7 runs a day x 2 sports uses about 420. Below 20
  remaining, scans pause (settling continues) and you get a notification. More sports or more frequent
  runs need a paid plan.
- **The closing line is approximate.** Runs are 2 hours apart, so the "closing" price is the last one
  seen before the start, up to 2 hours early. `close_lag_min` records how early. CLV measured this way
  understates movement in the final hour.
- **Paper fills are optimistic.** The book is checked at scan time; a real order would arrive later
  and might not get the same price. Treat results as an upper bound.
- **Expect few trades.** Kalshi's NBA/NHL moneylines are efficient. Weeks with zero +EV trades are normal.
- **Fee rate.** 0.07 per Kalshi's general taker formula. Verify against Kalshi's current schedule.
- **Schedule.** Times are in UTC in the workflow file. In daylight saving time (March-November) the runs
  shift one hour later in Central time.
- **The 90c scanner (`scan` workflow) is separate** and untouched. The two write to different files.
