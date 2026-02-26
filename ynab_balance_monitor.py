#!/usr/bin/env python3
"""YNAB Balance Monitor - Projects minimum checking account balance and alerts via Apprise."""

import calendar
import os
import re
import signal
import socket
import sys
import json
import time
from datetime import datetime, timedelta, date
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import apprise

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

YNAB_API_TOKEN = os.environ.get("YNAB_API_TOKEN", "")
YNAB_BUDGET_ID = os.environ.get("YNAB_BUDGET_ID", "last-used")
# Support single ID or comma-separated list for monitoring multiple accounts
_account_id_raw = os.environ.get("YNAB_ACCOUNT_ID", "")
YNAB_ACCOUNT_IDS = [aid.strip() for aid in _account_id_raw.split(",") if aid.strip()]
YNAB_CC_CATEGORIES = os.environ.get("YNAB_CC_CATEGORIES", "")  # comma-separated IDs, empty = all
YNAB_CC_CLOSE_DATES = os.environ.get("YNAB_CC_CLOSE_DATES", "")  # CardName:DayOfMonth pairs
YNAB_CC_CREATE_PAYMENTS = os.environ.get("YNAB_CC_CREATE_PAYMENTS", "").lower() in ("true", "1", "yes")
MONITOR_DAYS = os.environ.get("MONITOR_DAYS", "")  # empty = end of current month
MIN_BALANCE = int(os.environ.get("MIN_BALANCE", "0"))  # in dollars
YNAB_TARGET_BUFFER_DAYS = int(os.environ.get("YNAB_TARGET_BUFFER_DAYS", "10"))
YNAB_ALERT_BUFFER_DAYS = int(os.environ.get("YNAB_ALERT_BUFFER_DAYS", "5"))
APPRISE_URLS = os.environ.get("APPRISE_URLS", "")  # comma-separated Apprise URLs
SCHEDULE = os.environ.get("SCHEDULE", "")  # e.g. "08:00" for daily at 8am, "6h" for every 6 hours
UPDATE_SCHEDULE = os.environ.get("UPDATE_SCHEDULE", "")  # when to send routine balance update notifications
UPDATE_APPRISE_URLS = os.environ.get("UPDATE_APPRISE_URLS", "")  # defaults to APPRISE_URLS if empty
TZ = os.environ.get("TZ", "")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes")

YNAB_BASE = "https://api.ynab.com/v1"
YNAB_API_TIMEOUT = 30  # seconds

APP_NAME = "YNAB Monitor"
APP_VERSION = "1.1.0"
USER_AGENT = f"YNAB-Balance-Monitor/{APP_VERSION} (+https://github.com/bakerboy448/YNAB-Balance-Monitor)"

# ---------------------------------------------------------------------------
# YNAB API helpers
# ---------------------------------------------------------------------------

def _sanitize_error(body, max_length=500):
    """Remove sensitive data from API error responses before logging."""
    text = body[:max_length]
    text = re.sub(r'(Bearer\s+)\S+', r'\1[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(
        r'(["\']?(?:token|key|secret|password)["\']?\s*[:=]\s*)["\']?\S+',
        r'\1[REDACTED]', text, flags=re.IGNORECASE,
    )
    return text


def ynab_get(path):
    """Make an authenticated GET request to the YNAB API."""
    url = f"{YNAB_BASE}{path}"
    req = Request(url, headers={"Authorization": f"Bearer {YNAB_API_TOKEN}", "User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=YNAB_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode())["data"]
    except HTTPError as e:
        body = e.read().decode()
        print(f"YNAB API error ({e.code}): {_sanitize_error(body)}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except socket.timeout:
        print(f"Timeout connecting to YNAB API ({YNAB_API_TIMEOUT}s)", file=sys.stderr)
        sys.exit(1)


def ynab_put(path, payload):
    """Make an authenticated PUT request to the YNAB API."""
    url = f"{YNAB_BASE}{path}"
    data = json.dumps(payload).encode()
    req = Request(url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {YNAB_API_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urlopen(req, timeout=YNAB_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode())["data"]
    except HTTPError as e:
        body = e.read().decode()
        print(f"YNAB API PUT error ({e.code}): {_sanitize_error(body)}", file=sys.stderr)
        raise


def ynab_post(path, payload):
    """Make an authenticated POST request to the YNAB API."""
    url = f"{YNAB_BASE}{path}"
    data = json.dumps(payload).encode()
    req = Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {YNAB_API_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urlopen(req, timeout=YNAB_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode())["data"]
    except HTTPError as e:
        body = e.read().decode()
        print(f"YNAB API POST error ({e.code}): {_sanitize_error(body)}", file=sys.stderr)
        raise


def milliunits_to_dollars(milliunits):
    """YNAB stores amounts in milliunits (1 dollar = 1000 milliunits)."""
    return milliunits / 1000.0


def parse_cc_close_dates():
    """Parse YNAB_CC_CLOSE_DATES into {card_name: close_day} dict.

    Format: "CardName:DayOfMonth,CardName2:DayOfMonth2"
    Validates day is 1-28 (avoids month-length edge cases).
    """
    if not YNAB_CC_CLOSE_DATES:
        return {}
    result = {}
    for pair in YNAB_CC_CLOSE_DATES.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            print(f"Warning: invalid CC close date entry (missing ':'): '{pair}'", file=sys.stderr)
            continue
        # Split on last colon only (card names may contain colons)
        name, day_str = pair.rsplit(":", 1)
        name = name.strip()
        try:
            day = int(day_str.strip())
        except ValueError:
            print(f"Warning: invalid day in CC close date for '{name}': '{day_str}'", file=sys.stderr)
            continue
        if day < 1 or day > 28:
            print(f"Warning: CC close day for '{name}' must be 1-28, got {day}", file=sys.stderr)
            continue
        result[name] = day
    return result


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def get_end_date():
    """Compute the projection end date.

    If MONITOR_DAYS is set, project that many days forward.
    Otherwise, project through the end of the current month.
    """
    today = datetime.now().date()
    if MONITOR_DAYS:
        return today + timedelta(days=int(MONITOR_DAYS))
    last_day = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last_day)


def get_account_balances():
    """Get current balances for all monitored accounts.

    Returns tuple of (total_balance, list of account details).
    Supports single account or multiple comma-separated accounts.
    """
    total_balance = 0.0
    accounts = []

    for account_id in YNAB_ACCOUNT_IDS:
        data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/accounts/{account_id}")
        account = data["account"]
        balance = milliunits_to_dollars(account["balance"])
        accounts.append({
            "id": account_id,
            "name": account["name"],
            "balance": balance,
        })
        total_balance += balance
        print(f"Account: {account['name']}")
        print(f"  Balance: ${balance:,.2f}")

    if len(accounts) > 1:
        print(f"Combined balance: ${total_balance:,.2f}")

    return total_balance, accounts


def _add_months(d, months):
    """Add months to a date, clamping to the last day of the target month."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _expand_occurrences(next_date, frequency, start, end):
    """Generate all occurrence dates of a recurring transaction within [start, end].

    next_date:  the next scheduled occurrence (from the API)
    frequency:  YNAB frequency string
    start/end:  the monitoring window bounds
    """
    # Map YNAB frequency to a delta-generating function.
    # Each function returns the next date given the current one.
    DELTAS = {
        "daily":          lambda d: d + timedelta(days=1),
        "weekly":         lambda d: d + timedelta(weeks=1),
        "everyOtherWeek": lambda d: d + timedelta(weeks=2),
        "every4Weeks":    lambda d: d + timedelta(weeks=4),
        "monthly":        lambda d: _add_months(d, 1),
        "everyOtherMonth":lambda d: _add_months(d, 2),
        "every3Months":   lambda d: _add_months(d, 3),
        "every4Months":   lambda d: _add_months(d, 4),
        "twiceAMonth":    None,  # special case
        "twiceAYear":     lambda d: _add_months(d, 6),
        "yearly":         lambda d: _add_months(d, 12),
        "everyOtherYear": lambda d: _add_months(d, 24),
    }

    if frequency == "never" or frequency not in DELTAS:
        # One-time transaction — just return it if in range
        if start <= next_date <= end:
            return [next_date]
        return []

    # Special handling for twiceAMonth: YNAB schedules on the 1st & 15th
    # (or the original day and that day + ~15). We approximate by using
    # the next_date's day-of-month and that day ± 15.
    if frequency == "twiceAMonth":
        dates = []
        # Generate monthly anchors, then add both the "first" and "second" hit
        day1 = next_date.day
        day2 = day1 + 15 if day1 <= 15 else day1 - 15
        d = next_date.replace(day=1)  # start of month
        # Back up one month to make sure we don't miss anything
        d = _add_months(d, -1)
        month_end = end
        while d <= month_end:
            last_day = calendar.monthrange(d.year, d.month)[1]
            for target_day in (day1, day2):
                clamped = min(target_day, last_day)
                candidate = date(d.year, d.month, clamped)
                if start <= candidate <= end:
                    dates.append(candidate)
            d = _add_months(d, 1)
        return sorted(set(dates))

    # General case: walk forward from next_date using the delta function
    advance = DELTAS[frequency]
    dates = []
    d = next_date
    while d <= end:
        if d >= start:
            dates.append(d)
        d = advance(d)
    return dates


def get_scheduled_transactions(end_date):
    """Get all scheduled transactions for the monitored account.

    Expands recurring transactions into individual occurrences within the
    monitoring window.
    """
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/scheduled_transactions")
    today = datetime.now().date()

    transactions = []
    for txn in data["scheduled_transactions"]:
        if txn.get("deleted", False):
            continue

        acct_id = txn["account_id"]
        xfer_id = txn.get("transfer_account_id")

        # Include transactions ON a monitored account, OR transfers TO a
        # monitored account (stored on the other side, e.g. CC -> checking)
        on_checking = acct_id in YNAB_ACCOUNT_IDS
        xfer_to_checking = xfer_id in YNAB_ACCOUNT_IDS
        if not on_checking and not xfer_to_checking:
            continue

        next_date = datetime.strptime(txn.get("date_next") or txn.get("date_first", ""), "%Y-%m-%d").date()
        frequency = txn.get("frequency", "never")
        # If stored on the CC side (transfer to checking), flip the sign
        # so it appears as an outflow from checking's perspective
        raw_amount = milliunits_to_dollars(txn["amount"])
        amount = -raw_amount if xfer_to_checking and not on_checking else raw_amount
        payee = txn.get("payee_name", "Unknown")
        transfer_account_id = xfer_id if on_checking else acct_id

        occurrences = _expand_occurrences(next_date, frequency, today, end_date)
        for occ_date in occurrences:
            freq_label = f" ({frequency})" if frequency != "never" else ""
            transactions.append({
                "date": occ_date,
                "amount": amount,
                "payee": payee,
                "transfer_account_id": transfer_account_id,
                "frequency": frequency,
                "label": f"{payee}{freq_label}",
            })

    transactions.sort(key=lambda t: t["date"])
    print(f"\nScheduled transactions through {end_date}: {len(transactions)}")
    for t in transactions:
        print(f"  {t['date']}  {t['label']:40s}  ${t['amount']:>10,.2f}")
    return transactions


def _get_last_close_date(close_day):
    """Return the most recent statement close date for a given close day-of-month."""
    today = datetime.now().date()
    # Try this month's close date first
    try:
        this_month_close = today.replace(day=close_day)
    except ValueError:
        this_month_close = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if this_month_close <= today:
        return this_month_close
    # Close date hasn't happened yet this month, use last month's
    last_month = _add_months(today, -1)
    try:
        return last_month.replace(day=min(close_day, calendar.monthrange(last_month.year, last_month.month)[1]))
    except ValueError:
        return last_month.replace(day=calendar.monthrange(last_month.year, last_month.month)[1])


def _compute_statement_balance(account_id, cleared_balance_milliunits, close_day):
    """Compute the statement balance as of the last close date.

    statement_balance = cleared_balance - (sum of post-close cleared transactions)
    All arithmetic in milliunits. Returns (dollars, last_close_date).

    CC balances are negative in YNAB (debt). The result is negated so that
    a $500 debt returns 500.0 as the payment amount. Credit balances (positive
    cleared_balance) return 0.
    """
    last_close = _get_last_close_date(close_day)
    # Get transactions after the close date (since_date is inclusive, so use day after)
    day_after_close = last_close + timedelta(days=1)
    since_str = day_after_close.strftime("%Y-%m-%d")
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/accounts/{account_id}/transactions?since_date={since_str}")

    post_close_sum = 0
    for txn in data.get("transactions", []):
        if txn.get("deleted"):
            continue
        if txn.get("cleared", "") in ("cleared", "reconciled"):
            post_close_sum += txn["amount"]

    statement_balance_milliunits = cleared_balance_milliunits - post_close_sum
    # CC balances are negative (debt); negate to get positive payment amount.
    # If balance is positive (credit/overpayment), payment is $0.
    payment_dollars = max(0, -milliunits_to_dollars(statement_balance_milliunits))
    return payment_dollars, last_close


def get_cc_payment_amounts():
    """Get credit card payment amounts.

    For cards with configured close dates in YNAB_CC_CLOSE_DATES: computes
    the statement balance (balance at the close date) by subtracting
    post-close transactions from cleared_balance.
    For cards without: falls back to category balance (original behavior).

    Returns a dict of {account_id: {name, amount}} and the total.
    """
    close_dates = parse_cc_close_dates()

    # Get all accounts to identify CC accounts and their cleared balances
    accounts_data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/accounts")
    cc_accounts = {}  # name -> id
    cc_cleared = {}   # id -> {name, cleared_balance_milliunits}
    for acct in accounts_data["accounts"]:
        if acct["type"] == "creditCard" and not acct.get("deleted", False) and not acct.get("closed", False):
            cc_accounts[acct["name"]] = acct["id"]
            cc_cleared[acct["id"]] = {
                "name": acct["name"],
                "cleared_balance_milliunits": acct["cleared_balance"],
            }

    cc_payments = {}

    # Cards WITH close dates: compute statement balance (balance at close date)
    for card_name, close_day in close_dates.items():
        account_id = cc_accounts.get(card_name)
        if not account_id:
            print(f"  Warning: CC close date configured for '{card_name}' but no matching YNAB account found")
            continue
        info = cc_cleared[account_id]
        amount, last_close = _compute_statement_balance(account_id, info["cleared_balance_milliunits"], close_day)
        if amount > 0:
            cc_payments[account_id] = {
                "name": card_name,
                "amount": amount,
                "source": f"statement ({last_close})",
            }

    # Cards WITHOUT close dates: fall back to category balance
    cc_filter = set()
    if YNAB_CC_CATEGORIES:
        cc_filter = {c.strip() for c in YNAB_CC_CATEGORIES.split(",")}

    categories_data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/categories")
    for group in categories_data["category_groups"]:
        if group["name"] != "Credit Card Payments":
            continue
        for cat in group["categories"]:
            if cat.get("deleted", False) or cat.get("hidden", False):
                continue
            if cc_filter and cat["id"] not in cc_filter and cat["name"] not in cc_filter:
                continue

            account_id = cc_accounts.get(cat["name"])
            if account_id and account_id not in cc_payments:
                # Only use category balance for cards not already handled by close dates
                available = milliunits_to_dollars(cat["balance"])
                if available > 0:
                    cc_payments[account_id] = {
                        "name": cat["name"],
                        "amount": available,
                        "source": "category_balance",
                    }

    total = sum(p["amount"] for p in cc_payments.values())
    print(f"\nCredit card payments to account for: ${total:,.2f}")
    for p in cc_payments.values():
        source_label = f" ({p['source']})" if close_dates else ""
        print(f"  {p['name']:30s}  ${p['amount']:>10,.2f}{source_label}")
    return cc_payments, total


def get_cc_payment_history(cc_account_id, months_back=6):
    """Get historical CC payment transactions to determine typical pay date.

    Looks at transfers FROM checking TO this CC account.
    Returns the most common day-of-month for payments, or None if no history.
    """
    from collections import Counter

    since_date = (datetime.now() - timedelta(days=months_back * 30)).strftime("%Y-%m-%d")
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/accounts/{cc_account_id}/transactions?since_date={since_date}")

    pay_days = []
    for txn in data.get("transactions", []):
        if txn.get("deleted"):
            continue
        # Payment = transfer from checking (positive amount from CC's perspective)
        if txn["amount"] > 0 and txn.get("transfer_account_id") in YNAB_ACCOUNT_IDS:
            pay_date = datetime.strptime(txn["date"], "%Y-%m-%d").date()
            pay_days.append(pay_date.day)

    if not pay_days:
        return None

    return Counter(pay_days).most_common(1)[0][0]


def update_cc_payment_amount(cc_account_id, cc_name, payment_amount, checking_account_id):
    """Update the scheduled payment amount for a CC if it differs.

    Finds existing scheduled transfer from checking to this CC.
    If found and amount differs: PUT update.
    If not found and YNAB_CC_CREATE_PAYMENTS is enabled: create using
    historical pay date analysis. Otherwise: log warning and skip.
    """
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/scheduled_transactions")

    existing = None
    reverse = False
    for txn in data["scheduled_transactions"]:
        if txn.get("deleted"):
            continue
        # Find transfer between checking and this CC (either direction)
        if (txn["account_id"] == checking_account_id and
            txn.get("transfer_account_id") == cc_account_id):
            existing = txn
            break
        if (txn["account_id"] == cc_account_id and
            txn.get("transfer_account_id") == checking_account_id):
            existing = txn
            reverse = True
            break

    # Amount in milliunits — sign depends on which account holds the record
    # Checking side: negative (outflow). CC side: positive (inflow).
    amount_milliunits = int(payment_amount * (1000 if reverse else -1000))

    if existing:
        current_amount = existing["amount"]
        if current_amount != amount_milliunits:
            # Display as positive dollars regardless of sign direction
            old_dollars = abs(current_amount / 1000)
            if DRY_RUN:
                print(f"  [DRY-RUN] Would update {cc_name}: ${old_dollars:,.2f} -> ${payment_amount:,.2f}")
            else:
                print(f"  Updating {cc_name}: ${old_dollars:,.2f} -> ${payment_amount:,.2f}")
                # YNAB PUT requires date (future, max 1 week past) and account_id.
                # Use date_next if valid, otherwise use today.
                today = datetime.now().date()
                week_ago = today - timedelta(days=7)
                existing_date_str = existing.get("date_next") or existing.get("date_first", "")
                if existing_date_str:
                    existing_date = datetime.strptime(existing_date_str, "%Y-%m-%d").date()
                else:
                    existing_date = None
                if existing_date and existing_date >= week_ago:
                    valid_date = existing_date
                else:
                    valid_date = today
                ynab_put(f"/budgets/{YNAB_BUDGET_ID}/scheduled_transactions/{existing['id']}", {
                    "scheduled_transaction": {
                        "account_id": existing["account_id"],
                        "date": valid_date.strftime("%Y-%m-%d"),
                        "amount": amount_milliunits
                    }
                })
        else:
            print(f"  {cc_name}: already correct at ${payment_amount:,.2f}")
    else:
        if YNAB_CC_CREATE_PAYMENTS:
            pay_day = get_cc_payment_history(cc_account_id)
            if pay_day is None:
                print(f"  Warning: no payment history found for {cc_name}, cannot determine pay date — skipping creation")
            else:
                # Calculate next occurrence of pay_day
                today = datetime.now().date()
                if today.day <= pay_day:
                    try:
                        next_pay = today.replace(day=pay_day)
                    except ValueError:
                        next_pay = today.replace(day=calendar.monthrange(today.year, today.month)[1])
                else:
                    next_month = _add_months(today, 1)
                    last_day = calendar.monthrange(next_month.year, next_month.month)[1]
                    next_pay = next_month.replace(day=min(pay_day, last_day))

                if DRY_RUN:
                    print(f"  [DRY-RUN] Would create {cc_name}: ${payment_amount:,.2f} on {next_pay} (day {pay_day} from history)")
                else:
                    print(f"  Creating {cc_name}: ${payment_amount:,.2f} on {next_pay} (day {pay_day} from history)")
                    ynab_post(f"/budgets/{YNAB_BUDGET_ID}/scheduled_transactions", {
                        "scheduled_transaction": {
                            "account_id": checking_account_id,
                            "date": next_pay.strftime("%Y-%m-%d"),
                            "amount": amount_milliunits,
                            "payee_id": None,
                            "transfer_account_id": cc_account_id,
                            "memo": "Auto-scheduled by YNAB Monitor",
                            "frequency": "monthly"
                        }
                    })
        else:
            print(f"  Warning: no scheduled payment found for {cc_name}, skipping")


def _get_all_cc_transfer_ids():
    """Fetch all scheduled CC transfer account IDs (regardless of date).

    Returns a set of CC account IDs that have a scheduled transfer
    from/to any monitored checking account.
    """
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/scheduled_transactions")
    covered = set()
    for txn in data["scheduled_transactions"]:
        if txn.get("deleted"):
            continue
        acct = txn["account_id"]
        xfer = txn.get("transfer_account_id")
        if not xfer:
            continue
        # Transfer from checking to CC
        if acct in YNAB_ACCOUNT_IDS:
            covered.add(xfer)
        # Transfer from CC to checking (stored on CC side)
        if xfer in YNAB_ACCOUNT_IDS:
            covered.add(acct)
    return covered


def project_minimum_balance(current_balance, scheduled_transactions, cc_payments, end_date):
    """Walk day-by-day to find the minimum projected balance.

    CC payments with scheduled transfers (even beyond the projection window)
    are excluded from the day-1 lump sum. Only truly unscheduled CC payments
    are applied on day 1 as a conservative estimate.
    """
    today = datetime.now().date()

    # Identify CC accounts that have ANY scheduled transfer (regardless of date)
    covered_cc_ids = _get_all_cc_transfer_ids()

    # Remove covered CC payments; also dedup against in-window transfers
    remaining_cc = {}
    for cc_id, info in cc_payments.items():
        if cc_id in covered_cc_ids:
            continue  # Has a scheduled transfer — will be handled when it hits
        remaining_cc[cc_id] = info

    # Unscheduled CC payment total — apply on day 1
    unscheduled_cc_total = sum(p["amount"] for p in remaining_cc.values())
    if unscheduled_cc_total > 0:
        print(f"\nUnscheduled CC payments (applied today): ${unscheduled_cc_total:,.2f}")
    else:
        print(f"\nAll CC payments are scheduled.")

    # Build day-by-day projection
    balance = current_balance - unscheduled_cc_total
    min_balance = balance
    min_date = today

    # Group scheduled transactions by date
    txn_by_date = {}
    for txn in scheduled_transactions:
        txn_by_date.setdefault(txn["date"], []).append(txn)

    day = today
    while day <= end_date:
        if day in txn_by_date:
            for txn in txn_by_date[day]:
                balance += txn["amount"]
        if balance < min_balance:
            min_balance = balance
            min_date = day
        day += timedelta(days=1)

    print(f"\nProjected minimum balance: ${min_balance:,.2f} on {min_date}")
    return min_balance, min_date


def calculate_monthly_expenses():
    """Calculate average monthly expenses from trailing 13 months of YNAB data.

    Fetches MonthDetail for each of the last 13 complete months, sums negative
    category activity (excluding CC payment and internal categories), and
    returns the average.  13 months captures seasonal variation and ensures
    every calendar month is represented at least once.

    Returns (avg_daily_expenses, avg_monthly_expenses).
    """
    today = datetime.now().date()
    first_of_month = date(today.year, today.month, 1)
    skip_groups = {"Credit Card Payments", "Internal Master Category"}

    monthly_totals = []
    for i in range(1, 14):  # 1..13 months back
        month_start = _add_months(first_of_month, -i)
        month_str = month_start.strftime("%Y-%m-01")
        data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/months/{month_str}")
        month_detail = data["month"]

        total = 0.0
        for cat in month_detail["categories"]:
            if cat.get("deleted", False) or cat.get("hidden", False):
                continue
            if cat.get("category_group_name", "") in skip_groups:
                continue
            activity = milliunits_to_dollars(cat["activity"])
            if activity < 0:
                total += abs(activity)

        monthly_totals.append((month_start, total))

    avg_monthly = sum(t for _, t in monthly_totals) / len(monthly_totals)
    avg_daily = avg_monthly / 30.44  # average days per month

    print(f"\nTrailing 13-month expenses:")
    for month_start, total in monthly_totals:
        print(f"  {month_start.strftime('%b %Y'):>10s}  ${total:>10,.2f}")
    print(f"  {'Average':>10s}  ${avg_monthly:>10,.2f}/mo  (${avg_daily:,.2f}/day)")

    return avg_daily, avg_monthly


def get_dynamic_thresholds(avg_daily_expenses):
    """Compute alert and target thresholds for the projected minimum.

    The projected minimum is the balance AFTER all known obligations clear.
    Thresholds represent how many days of average spending that cushion
    should cover for unplanned expenses:
    - alert: YNAB_ALERT_BUFFER_DAYS (default 5) — transfer from HYSA now
    - target: YNAB_TARGET_BUFFER_DAYS (default 10) — consider transferring

    MIN_BALANCE is used as a floor.

    Returns (alert_threshold, target_threshold).
    """
    alert_threshold = round(max(MIN_BALANCE, avg_daily_expenses * YNAB_ALERT_BUFFER_DAYS), -2)
    target_threshold = round(max(MIN_BALANCE, avg_daily_expenses * YNAB_TARGET_BUFFER_DAYS), -2)
    return alert_threshold, target_threshold


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def _build_notifier(urls_str):
    """Build an Apprise notifier from a comma-separated URL string."""
    notifier = apprise.Apprise()
    for url in urls_str.split(","):
        url = url.strip()
        if url:
            notifier.add(url)
    return notifier


def send_alert_notification(current_balance, min_balance, min_date, alert_threshold, target_threshold):
    """Send a below-threshold alert via Apprise."""
    shortfall = alert_threshold - min_balance
    title = f"{APP_NAME}: Transfer ${shortfall:,.0f} to checking"
    message = (
        f"Projected minimum ${min_balance:,.0f} on {min_date.strftime('%b %d')} "
        f"is ${shortfall:,.0f} below the ${alert_threshold:,.0f} alert threshold.\n"
        f"Current balance: ${current_balance:,.0f}\n"
        f"Target: ${target_threshold:,.0f}"
    )

    notifier = _build_notifier(APPRISE_URLS)
    notify_type = apprise.NotifyType.WARNING if min_balance < 0 else apprise.NotifyType.INFO

    if not notifier.notify(title=title, body=message, notify_type=notify_type):
        print("Failed to send alert via Apprise", file=sys.stderr)
        sys.exit(1)

    print("\nAlert notification sent via Apprise")


def send_update_notification(current_balance, min_balance, min_date, end_date, alert_threshold, target_threshold):
    """Send a routine projected-balance update via Apprise."""
    if min_balance < alert_threshold:
        status = "BELOW ALERT"
        title = f"{APP_NAME}: Checking ${min_balance:,.0f} min — {status}"
    elif min_balance < target_threshold:
        status = "below target"
        title = f"{APP_NAME}: Checking ${min_balance:,.0f} min — {status}"
    else:
        status = "on track"
        title = f"{APP_NAME}: Checking ${min_balance:,.0f} min — {status}"
    message = (
        f"Current: ${current_balance:,.0f} | "
        f"Min: ${min_balance:,.0f} on {min_date.strftime('%b %d')}\n"
        f"Alert: ${alert_threshold:,.0f} | Target: ${target_threshold:,.0f}\n"
        f"Through {end_date.strftime('%b %d, %Y')}"
    )

    urls = UPDATE_APPRISE_URLS or APPRISE_URLS
    notifier = _build_notifier(urls)
    notify_type = apprise.NotifyType.WARNING if min_balance < alert_threshold else apprise.NotifyType.SUCCESS

    if not notifier.notify(title=title, body=message, notify_type=notify_type):
        print("Failed to send update notification via Apprise", file=sys.stderr)
    else:
        print("\nUpdate notification sent via Apprise")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _is_valid_uuid(value):
    """Check if value looks like a UUID or known YNAB alias."""
    if value in ("last-used",):
        return True
    return bool(re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', value, re.IGNORECASE))


def validate_config():
    """Check required configuration is present and valid."""
    errors = []
    if not YNAB_API_TOKEN:
        errors.append("YNAB_API_TOKEN is required")
    if not YNAB_ACCOUNT_IDS:
        errors.append("YNAB_ACCOUNT_ID is required (single ID or comma-separated list)")
    else:
        for aid in YNAB_ACCOUNT_IDS:
            if not _is_valid_uuid(aid):
                errors.append(f"YNAB_ACCOUNT_ID contains invalid UUID: '{aid}'")
    if not _is_valid_uuid(YNAB_BUDGET_ID):
        errors.append(f"YNAB_BUDGET_ID must be a valid UUID or 'last-used', got: '{YNAB_BUDGET_ID}'")
    if not APPRISE_URLS:
        errors.append("APPRISE_URLS is required")
    if MONITOR_DAYS:
        try:
            days = int(MONITOR_DAYS)
            if days < 1 or days > 365:
                errors.append(f"MONITOR_DAYS must be 1-365, got: {days}")
        except ValueError:
            errors.append(f"MONITOR_DAYS must be an integer, got: '{MONITOR_DAYS}'")
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def run_check(send_update=False):
    """Run one balance check cycle.

    Computes dynamic alert/target thresholds from monthly outflows and fires
    an alert if the projected minimum falls below the alert threshold.
    When send_update is True, also fires a routine update notification.
    """
    end_date = get_end_date()

    print("=" * 60)
    print(f"YNAB Balance Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Projecting through {end_date}, min floor: ${MIN_BALANCE:,.2f}")
    print("=" * 60)

    balance, accounts = get_account_balances()
    transactions = get_scheduled_transactions(end_date)
    cc_payments, _ = get_cc_payment_amounts()

    # Update CC scheduled payment amounts
    if YNAB_ACCOUNT_IDS:
        print("\nChecking CC payment amounts...")
        checking_id = YNAB_ACCOUNT_IDS[0]  # Primary checking account
        for cc_id, payment_info in cc_payments.items():
            if payment_info["amount"] > 0:
                update_cc_payment_amount(cc_id, payment_info["name"], payment_info["amount"], checking_id)

    # Calculate dynamic thresholds based on last month's actual expenses
    avg_daily, avg_monthly = calculate_monthly_expenses()
    alert_threshold, target_threshold = get_dynamic_thresholds(avg_daily)
    print(f"Alert threshold ({YNAB_ALERT_BUFFER_DAYS}d): ${alert_threshold:,.0f}")
    print(f"Target threshold ({YNAB_TARGET_BUFFER_DAYS}d): ${target_threshold:,.0f}")

    min_balance, min_date = project_minimum_balance(balance, transactions, cc_payments, end_date)

    if min_balance < alert_threshold:
        shortfall = alert_threshold - min_balance
        print(f"\n⚠ ALERT: Projected balance drops ${shortfall:,.0f} below alert threshold!")
        send_alert_notification(balance, min_balance, min_date, alert_threshold, target_threshold)
    else:
        print(f"\n✓ Balance stays above ${alert_threshold:,.0f} alert threshold.")

    if send_update:
        send_update_notification(balance, min_balance, min_date, end_date, alert_threshold, target_threshold)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def _parse_schedule(schedule):
    """Parse a schedule string into a descriptor tuple.

    Supported formats:
      "08:00"  - run daily at this time (24h format)
      "6h"     - run every N hours
      ""       - run once and exit (returns None)
    """
    s = schedule.strip()
    if not s:
        return None

    # Interval format: "6h", "12h", etc.
    if s.endswith("h"):
        try:
            hours = float(s[:-1])
            return ("interval", hours * 3600)
        except ValueError:
            pass

    # Daily time format: "HH:MM"
    if ":" in s:
        try:
            hour, minute = s.split(":")
            return ("daily", int(hour), int(minute))
        except ValueError:
            pass

    print(f"Invalid schedule format: '{s}'. Use 'HH:MM' or 'Nh'.", file=sys.stderr)
    sys.exit(1)


def _next_occurrence(schedule, after=None):
    """Return the datetime of the next occurrence of schedule after `after` (default: now)."""
    if after is None:
        after = datetime.now()
    if schedule[0] == "interval":
        return after + timedelta(seconds=schedule[1])
    else:
        _, hour, minute = schedule
        t = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if t <= after:
            t += timedelta(days=1)
        return t


def _describe_schedule(label, parsed):
    """Print a human-readable description of a parsed schedule."""
    if parsed[0] == "interval":
        print(f"{label}: every {parsed[1] / 3600:.4g} hours")
    else:
        _, hour, minute = parsed
        print(f"{label}: daily at {hour:02d}:{minute:02d}")


def main():
    validate_config()

    schedule = _parse_schedule(SCHEDULE)
    update_schedule = _parse_schedule(UPDATE_SCHEDULE) if UPDATE_SCHEDULE else None

    if schedule is None and update_schedule is None:
        # Run once and exit
        run_check()
        return

    # Run on a schedule (at least one of check / update is recurring)
    shutdown = False

    def handle_signal(signum, frame):
        nonlocal shutdown
        print("\nShutting down...")
        shutdown = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Determine initial next-run times.
    # Interval schedules fire immediately on startup; daily schedules wait for
    # their first configured time.
    now = datetime.now()

    if schedule:
        _describe_schedule("Check schedule", schedule)
        next_check = now if schedule[0] == "interval" else _next_occurrence(schedule)
    else:
        next_check = None

    if update_schedule:
        _describe_schedule("Update schedule", update_schedule)
        next_update = now if update_schedule[0] == "interval" else _next_occurrence(update_schedule)
    else:
        next_update = None

    while not shutdown:
        # Sleep until the sooner of the two pending events
        candidates = [t for t in [next_check, next_update] if t is not None]
        if not candidates:
            break
        wake_time = min(candidates)

        wait = (wake_time - datetime.now()).total_seconds()
        if wait > 0:
            print(f"Next event in {wait / 3600:.4g} hours")
            while wait > 0 and not shutdown:
                time.sleep(min(wait, 60))
                wait -= 60

        if shutdown:
            break

        now = datetime.now()
        do_check = next_check is not None and now >= next_check
        do_update = next_update is not None and now >= next_update

        # Run the projection (always checks alert threshold; optionally sends
        # an update notification when the update schedule fires).
        run_check(send_update=do_update)

        if do_check and schedule:
            next_check = _next_occurrence(schedule)
        if do_update and update_schedule:
            next_update = _next_occurrence(update_schedule)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="YNAB Balance Monitor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="Run once immediately, skip notifications and CC updates")
    group.add_argument("--daemon", action="store_true",
                       help="Run on SCHEDULE (default when SCHEDULE env var is set)")
    args = parser.parse_args()

    if args.dry_run:
        DRY_RUN = True
        validate_config()
        run_check()
    elif args.daemon or SCHEDULE:
        main()
    else:
        # No schedule, no flags — run once
        validate_config()
        run_check()
