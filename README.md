# YNAB Balance Monitor

[![CI](https://github.com/bakerboy448/YNAB-Balance-Monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/bakerboy448/YNAB-Balance-Monitor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Projects the minimum balance of your checking account(s) over a configurable window based on scheduled transactions and credit card statement balances from [YNAB](https://www.ynab.com/). Sends an [Apprise](https://github.com/caronc/apprise) alert if the balance is projected to drop below a dynamic threshold.

Useful for keeping most of your cash in a high-yield savings account while making sure your checking account stays funded.

## How it works

1. Fetches the current cleared balance of your checking account(s) — supports monitoring multiple accounts
2. Fetches all scheduled transactions for those accounts within the projection window
3. Computes **statement balances** for each credit card using configured close dates — this is the balance at the last statement close, not the current balance
4. Searches for existing CC payment transfers **bidirectionally** — YNAB may store the transfer on either the checking or credit card side
5. Updates scheduled CC payment amounts if they don't match the statement balance
6. Deduplicates — CC payments with scheduled transfers aren't double-counted as unscheduled
7. Walks day-by-day to find the **minimum projected balance**
8. Compares the minimum against **dynamic thresholds** based on trailing average daily expenses
9. If it drops below the alert threshold, sends a notification

## Statement Balance Computation

For cards with configured close dates (`YNAB_CC_CLOSE_DATES`):

```
statement_balance = cleared_balance - sum(post_close_cleared_transactions)
```

The monitor looks at the most recent close date, fetches all cleared transactions after that date, and subtracts them from the current cleared balance. This gives the amount owed on the last statement — what autopay will actually charge.

For cards without close dates, falls back to the YNAB category balance.

## Dynamic Thresholds

Thresholds are computed from trailing 13-month average daily expenses:

- **Alert threshold** = `max(MIN_BALANCE, avg_daily_expenses * YNAB_ALERT_BUFFER_DAYS)` — fires a notification
- **Target threshold** = `max(MIN_BALANCE, avg_daily_expenses * YNAB_TARGET_BUFFER_DAYS)` — the "comfortable" level

## Setup

### 1. Get your YNAB credentials

- Go to [YNAB Account Settings > Developer Settings](https://app.ynab.com/settings/developer)
- Create a Personal Access Token
- Find your account ID via the API or URL bar in YNAB

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Run

**With Docker (recommended):**
```bash
# Set SCHEDULE in .env (e.g. SCHEDULE=08:00), then:
docker compose up -d
```

The container runs as a long-lived service and checks on your configured schedule.

**Run once (dry run):**
```bash
docker exec <container> python ynab_balance_monitor.py --dry-run
```

**CLI usage:**
```
python ynab_balance_monitor.py [--dry-run | --daemon]

  --dry-run   Run once immediately, skip notifications and CC payment updates
  --daemon    Run on SCHEDULE (default when SCHEDULE env var is set)
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `YNAB_API_TOKEN` | Yes | -- | YNAB Personal Access Token |
| `YNAB_ACCOUNT_ID` | Yes | -- | Checking account ID(s) to monitor, comma-separated for multiple |
| `YNAB_BUDGET_ID` | No | `last-used` | Budget ID (or `last-used`) |
| `YNAB_CC_CLOSE_DATES` | No | -- | Statement close dates as `CardName:DayOfMonth` pairs, comma-separated |
| `YNAB_CC_CREATE_PAYMENTS` | No | `false` | Auto-create scheduled CC payment transfers if none exist |
| `YNAB_CC_CATEGORIES` | No | all | Comma-separated category IDs or names to include |
| `MONITOR_DAYS` | No | end of month | Number of days to project forward |
| `MIN_BALANCE` | No | `0` | Minimum threshold floor in dollars |
| `YNAB_ALERT_BUFFER_DAYS` | No | `5` | Alert if projected minimum covers fewer than this many days of expenses |
| `YNAB_TARGET_BUFFER_DAYS` | No | `10` | Target buffer in days of expenses for "comfortable" level |
| `SCHEDULE` | No | -- | `HH:MM` for daily, `Nh` for interval. Empty = run once |
| `UPDATE_SCHEDULE` | No | -- | Separate schedule for routine balance update notifications |
| `APPRISE_URLS` | Yes | -- | Comma-separated [Apprise URLs](https://github.com/caronc/apprise/wiki) for alerts |
| `UPDATE_APPRISE_URLS` | No | `APPRISE_URLS` | Separate Apprise URLs for routine updates |
| `TZ` | No | `UTC` | Timezone for schedule (e.g. `America/Chicago`) |
| `DRY_RUN` | No | `false` | Skip notifications and CC updates (env var equivalent of `--dry-run`) |

## Example output

```
============================================================
YNAB Balance Monitor -- 2026-02-24 19:13
Projecting through 2026-04-25, min floor: $500.00
============================================================
Account: DG Checking 3969
  Balance: $11,686.73
Account: Ally Checking 9958
  Balance: $1,918.52
Combined balance: $13,605.25

Scheduled transactions through 2026-04-25: 28
  2026-02-26  Turnberry Manor Condo Association (monthly)  $   -274.95
  2026-02-27  Transfer : Chase Unlimited 9765 (monthly)    $ -2,902.05
  2026-02-28  CIBC Bank USA (monthly)                      $  3,568.01
  ...

Credit card payments to account for: $10,217.30
  Chase Freedom 9177              $  1,788.03 (statement (2026-01-25))
  Discover 9356                   $      7.99 (statement (2026-02-09))
  Chase Unlimited 9765            $  2,902.05 (statement (2026-02-02))
  ...

Trailing 13-month expenses:
     Average  $ 11,115.60/mo  ($365.16/day)
Alert threshold (15d): $5,500
Target threshold (30d): $11,000

All CC payments are scheduled.

Projected minimum balance: $4,930.03 on 2026-04-22
```
