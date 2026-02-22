# YNAB Balance Monitor

Projects the minimum balance of a checking account through the end of the current month based on scheduled transactions and credit card payment obligations from [YNAB](https://www.ynab.com/). Sends an [ntfy](https://ntfy.sh) alert if the balance is projected to drop below a threshold.

Useful for keeping most of your cash in a high-yield savings account while making sure your checking account stays funded.

## How it works

1. Fetches the current balance of your checking account
2. Fetches all scheduled transactions for that account within the monitoring window
3. Fetches credit card payment category balances (money earmarked to pay CC bills)
4. Deduplicates — scheduled transfers to CC accounts aren't double-counted
5. Walks day-by-day to find the **minimum projected balance**
6. If it drops below your threshold, sends an **alert** notification
7. On the update schedule (if configured), sends a routine **update** notification with the projected minimum regardless of threshold

## Setup

### 1. Get your YNAB credentials

- Go to [YNAB Account Settings → Developer Settings](https://app.ynab.com/settings/developer)
- Create a Personal Access Token
- Find your account ID:
  - Visit `https://api.ynab.com/v1/budgets/last-used/accounts?access_token=YOUR_TOKEN` and locate your checking account's `id` or
  - Choose the account in the sidebar and look at the URL: `https://app.ynab.com/(budget ID)/accounts/(account ID)`  

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

The container runs as a long-lived service and checks on your configured schedule. Use `docker compose logs -f` to see output.

**Run once (no schedule):**
```bash
# Leave SCHEDULE empty or unset
docker compose run --rm monitor

# Or without Docker:
python ynab_balance_monitor.py
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `YNAB_API_TOKEN` | Yes | — | YNAB Personal Access Token |
| `YNAB_ACCOUNT_ID` | Yes | — | ID of the checking account to monitor |
| `YNAB_BUDGET_ID` | No | `last-used` | Budget ID (or `last-used`) |
| `YNAB_CC_CATEGORIES` | No | all | Comma-separated category IDs or names to monitor |
| `MONITOR_DAYS` | No | end of month | Number of days to project forward (leave empty for end of current month) |
| `MIN_BALANCE` | No | `0` | Minimum threshold floor in dollars (serves as floor for dynamic thresholds) |
| `YNAB_TARGET_BUFFER_DAYS` | No | `10` | Target buffer — days of average daily expenses the projected minimum should cover |
| `YNAB_ALERT_BUFFER_DAYS` | No | `5` | Alert buffer — fire alert if projected minimum covers fewer than this many days |
| `SCHEDULE` | No | — | `HH:MM` for daily at that time, or `Nh` for every N hours. Empty = run once and exit |
| `UPDATE_SCHEDULE` | No | — | Same format as `SCHEDULE`. When set, sends a routine balance update notification on this cadence, independent of `SCHEDULE` |
| `APPRISE_URLS` | Yes | — | Comma-separated [Apprise URLs](https://github.com/caronc/apprise/wiki) for alert notifications |
| `UPDATE_APPRISE_URLS` | No | `APPRISE_URLS` | Comma-separated Apprise URLs for update notifications. Useful for routing updates to a lower-priority channel |
| `TZ` | No | `UTC` | Timezone for daily schedule (e.g. `America/New_York`) |

## Example output

```
============================================================
YNAB Balance Monitor — 2026-02-07 08:00
Projecting through 2026-02-28, min floor: $500.00
============================================================
Account: Primary Checking
Current balance: $2,450.00

Scheduled transactions through 2026-02-28: 3
  2026-02-10  Rent                            $ -1,500.00
  2026-02-14  Paycheck                        $  3,200.00
  2026-02-28  Car Payment                     $   -450.00

Credit card payments to account for: $1,200.00
  Chase Sapphire                              $    800.00
  Amex Gold                                   $    400.00

Trailing 13-month expenses: $38,400.00 (avg $2,953.85/mo, $97.03/day)
Alert threshold (5 days): $500.00
Target threshold (10 days): $1,000.00

Unscheduled CC payments (applied today): $1,200.00

Projected minimum balance: $950.00 on 2026-02-10

✓ On track (950.00 ≥ alert threshold of 500.00)
```
