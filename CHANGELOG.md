# CHANGELOG


## v1.2.0-rc.3 (2026-03-03)

### Bug Fixes

- Show all CC payments with scheduled/unscheduled labels
  ([`dcbd92b`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/dcbd92b4bff7809020dddf9d670a8e264cedcc74))

Instead of hiding covered CC payments (which removed useful context), tag each with (scheduled) or
  (unscheduled) so users can see all CC payment amounts while understanding which are already in
  upcoming bills.

Also adds scripts/release.sh to automate push -> release -> rebuild.

### Chores

- Ignore .claude/settings.local.json
  ([`c548b14`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/c548b146e172cd5405766290689e96445390c4a2))


## v1.2.0-rc.2 (2026-03-03)

### Bug Fixes

- Exclude scheduled CC payments from notification CC section
  ([`7f25c2a`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/7f25c2a135f332adc94e3d0e5ce6212219ae920c))

CC payments with scheduled transfers (e.g. CapitalOne, Discover) were displayed in both "Upcoming
  Bills" and "CC Payments Leaving Checking", appearing double-counted. Now project_minimum_balance()
  returns covered_cc_ids, and _build_notification_context() filters them out. Only truly unscheduled
  CC payments appear in the renamed "Unscheduled CC Payments" section.


## v1.2.0-rc.1 (2026-03-03)

### Bug Fixes

- Pass Notifiarr API key via x-api-key header instead of URL path
  ([`6a8bf53`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/6a8bf53c23bb25ed411359de611ad60cab1407ed))

Headers don't leak in server access logs or proxy logs.

- Resolve ruff lint errors
  ([`6900486`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/6900486a6aaaddb99fde001a2bb519508f6920d3))

- Sort imports (I001) - Replace socket.timeout with TimeoutError (UP041) - Use ternary operators for
  simple if-else blocks (SIM108) - Break long lines under 120 chars (E501) - Remove extraneous
  f-string prefixes (F541)

### Code Style

- Apply ruff format
  ([`2a2faf3`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/2a2faf3d69483ac579bf38cfe7810d19a57e8cad))

Auto-formatted with ruff 0.15.4 to pass CI format check.

### Documentation

- Add CI and license badges, clean up CLAUDE.md
  ([`540f894`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/540f894597db27e68fc6e0950a976b9c6f26b9e9))

### Features

- Add Notifiarr passthrough notifications with rich Discord embeds
  ([`b3c5548`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/b3c5548f44ec6a6fa3e2066c639fc3eec15cdf09))

Send color-coded Discord embeds via Notifiarr passthrough API with inline fields for balances,
  thresholds, upcoming outflows, and actionable transfer recommendations. Alerts and updates use
  message editing (update: true) to avoid notification spam. Apprise remains as fallback when
  Notifiarr is not configured, with improved message detail including buffer days and daily spend.

- Improve notification clarity with self-describing labels and formulas
  ([`8aa8d29`](https://github.com/bakerboy448/YNAB-Balance-Monitor/commit/8aa8d295bda77aebac20d5cc59ad1c3ddb5cc387))

Rewrite all 4 notification builders (Notifiarr alert/update, Apprise alert/update) to use narrative
  descriptions and show the math behind every threshold. Fields renamed from cryptic abbreviations
  (e.g. "Alert (15d)") to plain language ("Alert Cushion (15d spend): $3,600 ($240/day x 15d)"). Add
  40 unit tests covering all notification payloads and message formats.


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
