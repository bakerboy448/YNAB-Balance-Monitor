"""Tests for per-account monitoring (YNAB_ACCOUNT_ID_CC / YNAB_ACCOUNT_ID_NO_CC)."""

import os
import sys
from datetime import date, timedelta
from unittest.mock import patch

import pytest

os.environ.setdefault("YNAB_API_TOKEN", "fake-token")
os.environ.setdefault("YNAB_ACCOUNT_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
os.environ.setdefault("NOTIFIARR_CHANNEL_ID", "123456789")
os.environ.setdefault("NOTIFIARR_API_KEY", "fake-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ynab_balance_monitor as m  # noqa: E402

ACCT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ACCT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CC_1 = "cc-card-one"
CC_2 = "cc-card-two"


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


class TestParseMonitorTargets:
    def test_legacy_pooled_target(self):
        targets = m.parse_monitor_targets(cc_raw="", no_cc_raw="", legacy_ids=[ACCT_A, ACCT_B], legacy_min=250)
        assert len(targets) == 1
        t = targets[0]
        assert t.account_ids == [ACCT_A, ACCT_B]
        assert t.min_balance == 250
        assert t.include_cc is True
        assert t.per_account is False

    def test_per_account_targets(self):
        targets = m.parse_monitor_targets(
            cc_raw=f"{ACCT_A}:1000", no_cc_raw=f"{ACCT_B}:500", legacy_ids=[], legacy_min=0
        )
        assert [(t.account_ids, t.min_balance, t.include_cc, t.per_account) for t in targets] == [
            ([ACCT_A], 1000, True, True),
            ([ACCT_B], 500, False, True),
        ]

    def test_threshold_defaults_to_zero_and_whitespace_ignored(self):
        targets = m.parse_monitor_targets(cc_raw=f" {ACCT_A} , ", no_cc_raw="", legacy_ids=[], legacy_min=0)
        assert len(targets) == 1
        assert targets[0].min_balance == 0

    def test_per_account_takes_precedence_over_legacy(self):
        targets = m.parse_monitor_targets(cc_raw="", no_cc_raw=ACCT_B, legacy_ids=[ACCT_A], legacy_min=0)
        assert len(targets) == 1
        assert targets[0].account_ids == [ACCT_B]
        assert targets[0].include_cc is False

    def test_bad_threshold_raises(self):
        with pytest.raises(ValueError):
            m.parse_monitor_targets(cc_raw=f"{ACCT_A}:lots", no_cc_raw="", legacy_ids=[], legacy_min=0)

    def test_nothing_configured(self):
        assert m.parse_monitor_targets(cc_raw="", no_cc_raw="", legacy_ids=[], legacy_min=0) == []


class TestValidateConfig:
    def test_rejects_legacy_combined_with_per_account(self):
        with (
            patch.object(m, "YNAB_ACCOUNT_IDS", [ACCT_A]),
            patch.object(m, "YNAB_ACCOUNT_ID_CC", ACCT_B),
            pytest.raises(SystemExit),
        ):
            m.validate_config()

    def test_rejects_bad_threshold(self):
        with (
            patch.object(m, "YNAB_ACCOUNT_IDS", []),
            patch.object(m, "YNAB_ACCOUNT_ID_CC", f"{ACCT_A}:abc"),
            pytest.raises(SystemExit),
        ):
            m.validate_config()

    def test_rejects_invalid_uuid(self):
        with (
            patch.object(m, "YNAB_ACCOUNT_IDS", []),
            patch.object(m, "YNAB_ACCOUNT_ID_NO_CC", "not-a-uuid:100"),
            pytest.raises(SystemExit),
        ):
            m.validate_config()

    def test_accepts_per_account_only(self):
        with (
            patch.object(m, "YNAB_ACCOUNT_IDS", []),
            patch.object(m, "YNAB_ACCOUNT_ID_CC", f"{ACCT_A}:1000"),
            patch.object(m, "YNAB_ACCOUNT_ID_NO_CC", ACCT_B),
        ):
            m.validate_config()


# ---------------------------------------------------------------------------
# run_check with per-account targets
# ---------------------------------------------------------------------------


def _accounts():
    return [
        {"id": ACCT_A, "name": "Main Checking", "balance": 1_200_000, "type": "checking"},
        {"id": ACCT_B, "name": "Bills Account", "balance": 300_000, "type": "checking"},
    ]


def _run(targets, raw_scheduled, cc_payments, send_update=False):
    """Run run_check with all I/O mocked."""
    with (
        patch.object(m, "ynab_get", return_value={"accounts": _accounts()}),
        patch.object(m, "fetch_scheduled_transactions_delta", return_value=raw_scheduled),
        patch.object(m, "calculate_monthly_expenses", return_value=(0.0, 0.0)),
        patch.object(m, "get_cc_payment_amounts", return_value=(cc_payments, 0)) as cc_amounts,
        patch.object(m, "update_cc_payment_amount") as cc_update,
        patch.object(m, "send_alert_notification") as alert,
        patch.object(m, "send_update_notification") as update,
    ):
        m.run_check(send_update=send_update, targets=targets)
    return alert, update, cc_update, cc_amounts


class TestRunCheckPerAccount:
    def test_cc_applied_only_to_cc_account(self):
        targets = [
            m.MonitorTarget([ACCT_A], 1000, include_cc=True, per_account=True),
            m.MonitorTarget([ACCT_B], 100, include_cc=False, per_account=True),
        ]
        cc_payments = {CC_1: {"name": "Visa", "amount": 500.0, "source": "category_balance"}}
        alert, _, _, _ = _run(targets, [], cc_payments)

        # A: 1200 - 500 unscheduled CC = 700 < 1000 floor -> alert. B: 300, no CC, > 100 -> no alert.
        assert alert.call_count == 1
        ctx = alert.call_args[0][0]
        assert ctx["account_label"] == "Main Checking"
        assert ctx["min_balance"] == 700.0
        assert ctx["alert_threshold"] == 1000

    def test_each_account_uses_its_own_scheduled_transactions_and_floor(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        raw = [
            {
                "id": "s1",
                "account_id": ACCT_B,
                "transfer_account_id": None,
                "deleted": False,
                "date_next": tomorrow,
                "frequency": "never",
                "amount": -250_000,
                "payee_name": "Utility",
            }
        ]
        targets = [
            m.MonitorTarget([ACCT_A], 1000, include_cc=True, per_account=True),
            m.MonitorTarget([ACCT_B], 100, include_cc=False, per_account=True),
        ]
        with patch.object(m, "MONITOR_DAYS", "30"):
            alert, _, _, _ = _run(targets, raw, {})

        # A: 1200 >= 1000 -> no alert. B: 300 - 250 = 50 < 100 -> alert.
        assert alert.call_count == 1
        ctx = alert.call_args[0][0]
        assert ctx["account_label"] == "Bills Account"
        assert ctx["min_balance"] == 50.0

    def test_cc_scheduled_from_other_account_is_not_lumped(self):
        raw = [
            {
                "id": "s2",
                "account_id": ACCT_B,
                "transfer_account_id": CC_2,
                "deleted": False,
                "date_next": (date.today() + timedelta(days=60)).isoformat(),
                "frequency": "monthly",
                "amount": -900_000,
                "payee_name": "Transfer : Amex",
            }
        ]
        targets = [
            m.MonitorTarget([ACCT_A], 0, include_cc=True, per_account=True),
            m.MonitorTarget([ACCT_B], 0, include_cc=False, per_account=True),
        ]
        cc_payments = {
            CC_1: {"name": "Visa", "amount": 500.0, "source": "category_balance"},
            CC_2: {"name": "Amex", "amount": 900.0, "source": "category_balance"},
        }
        with patch.object(m, "MONITOR_DAYS", "30"):
            _, update, _, _ = _run(targets, raw, cc_payments, send_update=True)

        ctx_a = update.call_args_list[0][0][0]
        # Only the unscheduled Visa payment hits A; Amex is paid from B via a scheduled transfer.
        assert ctx_a["min_balance"] == 700.0
        assert ctx_a["cc_payments"][CC_2]["scheduled"] is True

    def test_update_sent_per_account(self):
        targets = [
            m.MonitorTarget([ACCT_A], 0, include_cc=True, per_account=True),
            m.MonitorTarget([ACCT_B], 0, include_cc=False, per_account=True),
        ]
        _, update, _, _ = _run(targets, [], {}, send_update=True)
        labels = [c[0][0]["account_label"] for c in update.call_args_list]
        assert labels == ["Main Checking", "Bills Account"]

    def test_no_cc_only_skips_cc_fetch_and_updates(self):
        targets = [m.MonitorTarget([ACCT_B], 0, include_cc=False, per_account=True)]
        _, _, cc_update, cc_amounts = _run(targets, [], {})
        cc_amounts.assert_not_called()
        cc_update.assert_not_called()

    def test_legacy_pooled_keeps_generic_label(self):
        targets = [m.MonitorTarget([ACCT_A, ACCT_B], 0, include_cc=True)]
        _, update, _, _ = _run(targets, [], {}, send_update=True)
        assert update.call_count == 1
        ctx = update.call_args[0][0]
        assert ctx["account_label"] is None
        assert ctx["current_balance"] == 1500.0


class TestLabelInNotifications:
    def test_apprise_alert_uses_account_name(self):
        ctx = {
            "account_label": "Bills Account",
            "current_balance": 300,
            "min_balance": 50,
            "min_date": date(2026, 3, 22),
            "end_date": date(2026, 3, 31),
            "shortfall": 50,
            "transfer_to_target": 50,
            "alert_threshold": 100,
            "target_threshold": 100,
            "alert_buffer_days": 5,
            "target_buffer_days": 10,
            "avg_daily_expenses": 0,
            "upcoming_outflows": [],
            "scheduled_inflows": {},
            "cc_payments": {},
        }
        with (
            patch.object(m, "_notifiarr_configured", return_value=False),
            patch.object(m, "_build_notifier") as notifier,
        ):
            notifier.return_value.notify.return_value = True
            m.send_alert_notification(ctx)
        kwargs = notifier.return_value.notify.call_args[1]
        assert "to Bills Account" in kwargs["title"]
        assert kwargs["body"].startswith("After all scheduled bills and CC payments, Bills Account bottoms out")
