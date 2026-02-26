# CHANGELOG


## v1.1.0 (2026-02-26)

### Features

- Add User-Agent headers, notification prefixes, CI/CD, and GHCR publishing
  ([`946e516`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/946e5163a95b11986340b3cbfead9fe1da9c51ee))

- Add APP_NAME/APP_VERSION/USER_AGENT constants - Set User-Agent on all YNAB API requests
  (GET/PUT/POST) - Prefix all Apprise notification titles with "YNAB Monitor: " - Add pyproject.toml
  with ruff and semantic-release config - Add CI workflow (ruff check + format) on push/PR - Add
  release workflow (semantic-release + multi-arch Docker to GHCR) - Dockerfile: add OCI labels,
  non-root user, combine RUN layers


## v1.0.0 (2026-02-24)

### Bug Fixes

- Add API timeouts, sanitize error logs, validate config, add CLI args
  ([`f26fd92`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/f26fd92d462d6cfa7febc097c865262648e748f4))

- Add 30s timeout to all YNAB API calls (prevents hanging on slow responses) - Sanitize error log
  output to prevent API token leakage - Validate env vars at startup (UUID format, MONITOR_DAYS
  bounds) - Add argparse CLI with --dry-run and --daemon flags - Update README with statement
  balance docs, CLI usage, current config

- Don't treat CC payments scheduled beyond window as unscheduled
  ([`31643ad`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/31643ad3279d3883c9ffc2eed43db84717e9c00c))

The projection was applying all CC payments not found in the projection window as a day-1 lump sum.
  But these payments ARE scheduled — just for dates after the window. Now checks all scheduled
  transactions (regardless of date) to identify covered CC accounts.

- Include CC-side transfers in scheduled transaction projection
  ([`8f09d31`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/8f09d31e388075d4c000201a5d8108a6f596c9c4))

Transfers stored on the CC account side (e.g. Hilton -> DG Checking) were excluded from the
  day-by-day projection because the filter only checked account_id against monitored accounts. Now
  also includes transactions where transfer_account_id is a monitored account, with sign flipped to
  represent the checking account perspective.

- Search both directions for CC transfer scheduled transactions
  ([`01108e9`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/01108e92738e13c7b057f8352db6b2b096be9ea7))

YNAB stores transfer records on either side of the transfer. The monitor only searched for
  account_id=checking, missing transfers stored on the CC account side (e.g. Hilton). Now checks
  both directions.

Also fixes display to use abs() for old amount and preserves the original account_id in PUT updates.

- Use statement balance for CC payment amounts
  ([`9b8b688`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/9b8b688b18bb0549c79bf91e83d1292eaf7f9b4b))

CC payment amounts were being set from cleared_balance (real-time balance) which changes daily as
  new charges clear. Now computes statement balance by subtracting post-close-date cleared
  transactions from cleared_balance.

Also fixes: - Milliunits round-trip: function now works in milliunits internally - abs() replaced
  with max(0, -val) to correctly handle credit balances

### Features

- Add CC payment auto-scheduling with historical pay date detection
  ([`4bea910`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/4bea910df18223f013aadc1ab9f65d797b6b4ae1))

- Add ynab_put/ynab_post for YNAB API writes - Add get_cc_payment_history to analyze past 6 months -
  Add ensure_cc_payment_scheduled to create/update scheduled payments - Add DRY_RUN mode for safe
  testing - Integrate into run_check() before balance projection

- Support comma-separated account IDs for multi-account monitoring
  ([`2d6a19c`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/2d6a19cd3e9d92a6789f975a3fc032f8872b1b43))

YNAB_ACCOUNT_ID now accepts a comma-separated list of account IDs to monitor multiple checking
  accounts with combined balance projection.

Backwards compatible - single ID still works as before.
