# YNAB Balance Monitor

## Project overview

A lightweight Python tool that projects the minimum balance of a checking account through the end of the current month using YNAB data, and sends alerts via Apprise if the balance is projected to drop below a threshold. Designed for users who keep most cash in an HYSA and need early warning to transfer funds to checking.

## Architecture

- **Single file**: `ynab_balance_monitor.py` — all logic in one Python script (only external dependency: `apprise`)
- **Docker**: `Dockerfile` + `docker-compose.yml` — runs as a long-lived service with built-in scheduling
- **Config**: Environment variables via `stack.env` (Portainer) or `.env`

## Key concepts

- **YNAB API** (`https://api.ynab.com/v1`): Amounts are in milliunits (1 dollar = 1000). Scheduled transactions use `date_next`/`date_first` fields (not `date`). Rate limit: 200 requests/hour.
- **Recurrence expansion**: YNAB only returns the next occurrence of scheduled transactions. `_expand_occurrences()` generates all occurrences within the monitoring window for all 13 YNAB frequency types.
- **CC payment deduplication**: Credit card payment category balances represent money earmarked to leave checking. Scheduled transfers to CC accounts are identified and subtracted to avoid double-counting. Remaining unscheduled CC payments are applied on day 1 (conservative).
- **Projection**: Day-by-day balance walk to find the minimum point, not just end-of-period balance.
- **Dynamic thresholds**: Fetches trailing 13 months of actual YNAB budget data via `/budgets/{id}/months/{month}` endpoint. Sums negative category activity (expenses) excluding "Credit Card Payments" and "Internal Master Category" groups. Computes average monthly expenses and daily rate. Alert/target thresholds = daily rate × buffer days (configurable via `YNAB_ALERT_BUFFER_DAYS` / `YNAB_TARGET_BUFFER_DAYS`), rounded to nearest $100, with `MIN_BALANCE` as floor.
- **Buffer days concept**: Projected minimum = the balance valley after all known obligations clear (cushion after scheduled transactions + CC payments). Threshold = days of average spending the projected minimum should cover (e.g., 5 days = alert if projected min < 5 days of expenses).

## Development notes

- Uses Python stdlib (`urllib`, `json`, `calendar`) plus `apprise` for notifications
- `python -u` flag in Dockerfile for unbuffered output (required for Docker log visibility)
- `stack.env` in docker-compose.yml for Portainer compatibility
- `SCHEDULE` env var supports `HH:MM` (daily) or `Nh` (interval) formats
