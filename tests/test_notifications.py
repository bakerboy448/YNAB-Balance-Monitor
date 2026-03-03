"""Tests for notification payload builders and Apprise message formatting."""

import os
import sys
from collections import OrderedDict
from datetime import date
from unittest.mock import patch

import pytest

# Ensure the module env vars are set before import
os.environ.setdefault("YNAB_API_TOKEN", "fake-token")
os.environ.setdefault("YNAB_ACCOUNT_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("NOTIFIARR_CHANNEL_ID", "123456789")
os.environ.setdefault("NOTIFIARR_API_KEY", "fake-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ynab_balance_monitor as m  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def alert_ctx():
    """Context dict for an alert scenario (projected min below alert threshold)."""
    return {
        "current_balance": 10234,
        "min_balance": 3100,
        "min_date": date(2026, 3, 22),
        "end_date": date(2026, 3, 31),
        "shortfall": 500,
        "transfer_to_target": 4100,
        "alert_threshold": 3600,
        "target_threshold": 7200,
        "alert_buffer_days": 15,
        "target_buffer_days": 30,
        "avg_daily_expenses": 240,
        "buffer_days_remaining": 12.9,
        "upcoming_outflows": [
            {"date": date(2026, 3, 15), "payee": "Mortgage", "amount": -2500},
            {"date": date(2026, 3, 20), "payee": "Car Payment", "amount": -450},
        ],
        "cc_payments": OrderedDict(
            [
                ("chase", {"name": "Chase Visa", "amount": -800, "scheduled": True}),
                ("amex", {"name": "Amex Gold", "amount": -350, "scheduled": False}),
            ]
        ),
    }


@pytest.fixture()
def update_ctx_on_track():
    """Context dict for a routine update — on track."""
    return {
        "current_balance": 10234,
        "min_balance": 8100,
        "min_date": date(2026, 3, 22),
        "end_date": date(2026, 3, 31),
        "alert_threshold": 3600,
        "target_threshold": 7200,
        "alert_buffer_days": 15,
        "target_buffer_days": 30,
        "avg_daily_expenses": 240,
        "buffer_days_remaining": 33.75,
    }


@pytest.fixture()
def update_ctx_below_target():
    """Context dict for a routine update — below target but above alert."""
    return {
        "current_balance": 10234,
        "min_balance": 5000,
        "min_date": date(2026, 3, 22),
        "end_date": date(2026, 3, 31),
        "alert_threshold": 3600,
        "target_threshold": 7200,
        "alert_buffer_days": 15,
        "target_buffer_days": 30,
        "avg_daily_expenses": 240,
        "buffer_days_remaining": 20.8,
    }


@pytest.fixture()
def update_ctx_below_alert():
    """Context dict for a routine update — below alert threshold."""
    return {
        "current_balance": 10234,
        "min_balance": 3100,
        "min_date": date(2026, 3, 22),
        "end_date": date(2026, 3, 31),
        "alert_threshold": 3600,
        "target_threshold": 7200,
        "alert_buffer_days": 15,
        "target_buffer_days": 30,
        "avg_daily_expenses": 240,
        "buffer_days_remaining": 12.9,
    }


# ---------------------------------------------------------------------------
# _fmt_dollars
# ---------------------------------------------------------------------------


class TestFmtDollars:
    def test_positive(self):
        assert m._fmt_dollars(1234) == "$1,234"

    def test_zero(self):
        assert m._fmt_dollars(0) == "$0"

    def test_negative(self):
        assert m._fmt_dollars(-500) == "-$500"


# ---------------------------------------------------------------------------
# _build_notification_context — CC payment filtering
# ---------------------------------------------------------------------------


class TestNotificationContextCCTagging:
    def test_covered_cc_tagged_scheduled(self):
        """CC payments with scheduled transfers should be tagged scheduled=True."""
        cc_payments = OrderedDict(
            [
                ("cc-1", {"name": "Chase Visa", "amount": -800}),
                ("cc-2", {"name": "Amex Gold", "amount": -350}),
                ("cc-3", {"name": "Discover", "amount": -150}),
            ]
        )
        covered = {"cc-1", "cc-3"}
        ctx = m._build_notification_context(
            balance=10000,
            accounts=[],
            min_balance=5000,
            min_date=date(2026, 3, 22),
            end_date=date(2026, 3, 31),
            alert_threshold=3600,
            target_threshold=7200,
            avg_daily=240,
            transactions=[],
            cc_payments=cc_payments,
            covered_cc_ids=covered,
        )
        # All CCs preserved, but tagged
        assert len(ctx["cc_payments"]) == 3
        assert ctx["cc_payments"]["cc-1"]["scheduled"] is True
        assert ctx["cc_payments"]["cc-2"]["scheduled"] is False
        assert ctx["cc_payments"]["cc-3"]["scheduled"] is True

    def test_no_covered_ids_all_unscheduled(self):
        """When no covered_cc_ids, all CC payments tagged as unscheduled."""
        cc_payments = OrderedDict(
            [
                ("cc-1", {"name": "Chase Visa", "amount": -800}),
                ("cc-2", {"name": "Amex Gold", "amount": -350}),
            ]
        )
        ctx = m._build_notification_context(
            balance=10000,
            accounts=[],
            min_balance=5000,
            min_date=date(2026, 3, 22),
            end_date=date(2026, 3, 31),
            alert_threshold=3600,
            target_threshold=7200,
            avg_daily=240,
            transactions=[],
            cc_payments=cc_payments,
        )
        assert len(ctx["cc_payments"]) == 2
        assert ctx["cc_payments"]["cc-1"]["scheduled"] is False
        assert ctx["cc_payments"]["cc-2"]["scheduled"] is False

    def test_all_covered_all_scheduled(self):
        """When all CCs covered, all tagged scheduled=True."""
        cc_payments = OrderedDict(
            [
                ("cc-1", {"name": "Chase Visa", "amount": -800}),
            ]
        )
        ctx = m._build_notification_context(
            balance=10000,
            accounts=[],
            min_balance=5000,
            min_date=date(2026, 3, 22),
            end_date=date(2026, 3, 31),
            alert_threshold=3600,
            target_threshold=7200,
            avg_daily=240,
            transactions=[],
            cc_payments=cc_payments,
            covered_cc_ids={"cc-1"},
        )
        assert len(ctx["cc_payments"]) == 1
        assert ctx["cc_payments"]["cc-1"]["scheduled"] is True


# ---------------------------------------------------------------------------
# _build_notifiarr_alert_payload
# ---------------------------------------------------------------------------


class TestNotifiarrAlertPayload:
    def test_structure(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        assert "notification" in payload
        assert "discord" in payload
        assert payload["notification"]["event"] == "ynab-alert"

    def test_title_contains_transfer(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        title = payload["discord"]["text"]["title"]
        assert "Transfer" in title
        assert "$4,100" in title

    def test_description_narrative(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        desc = payload["discord"]["text"]["description"]
        assert "bottom out" in desc
        assert "$3,100" in desc
        assert "Mar 22" in desc
        assert "15-day" in desc

    def test_field_labels(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        titles = [f["title"] for f in fields]
        assert "Balance Now" in titles
        assert "Lowest Point" in titles
        assert "Below Alert By" in titles
        assert "Transfer Needed" in titles

    def test_cushion_fields_show_formula(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        alert_cushion = next(f for f in fields if "Alert Cushion" in f["title"])
        assert "$240/day" in alert_cushion["text"]
        assert "15d" in alert_cushion["text"]
        target_cushion = next(f for f in fields if "Target Cushion" in f["title"])
        assert "$240/day" in target_cushion["text"]
        assert "30d" in target_cushion["text"]

    def test_upcoming_bills_field(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        bills = next(f for f in fields if f["title"] == "Upcoming Bills")
        assert "Mortgage" in bills["text"]
        assert "Car Payment" in bills["text"]

    def test_cc_payments_field(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        cc = next(f for f in fields if f["title"] == "CC Payments")
        assert "Chase Visa" in cc["text"]
        assert "Amex Gold" in cc["text"]

    def test_cc_payments_scheduled_labels(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        cc = next(f for f in fields if f["title"] == "CC Payments")
        assert "*(scheduled)*" in cc["text"]
        assert "*(unscheduled)*" in cc["text"]

    def test_action_field(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        action = next(f for f in fields if f["title"] == "Action")
        assert "$4,100" in action["text"]
        assert "HYSA" in action["text"]
        assert "30-day cushion" in action["text"]

    def test_no_upcoming_outflows(self, alert_ctx):
        alert_ctx["upcoming_outflows"] = []
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        titles = [f["title"] for f in fields]
        assert "Upcoming Bills" not in titles

    def test_no_cc_payments(self, alert_ctx):
        alert_ctx["cc_payments"] = {}
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        fields = payload["discord"]["text"]["fields"]
        titles = [f["title"] for f in fields]
        assert "CC Payments" not in titles

    def test_negative_balance_color(self, alert_ctx):
        alert_ctx["min_balance"] = -500
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        assert payload["discord"]["color"] == "E74C3C"

    def test_positive_balance_color(self, alert_ctx):
        payload = m._build_notifiarr_alert_payload(alert_ctx)
        assert payload["discord"]["color"] == "FF8C00"


# ---------------------------------------------------------------------------
# _build_notifiarr_update_payload
# ---------------------------------------------------------------------------


class TestNotifiarrUpdatePayload:
    def test_structure(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        assert payload["notification"]["event"] == "ynab-update"

    def test_on_track_status(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        title = payload["discord"]["text"]["title"]
        assert "On Track" in title
        assert payload["discord"]["color"] == "2ECC71"

    def test_below_target_status(self, update_ctx_below_target):
        payload = m._build_notifiarr_update_payload(update_ctx_below_target)
        title = payload["discord"]["text"]["title"]
        assert "Below Target" in title
        assert payload["discord"]["color"] == "F39C12"

    def test_below_alert_status(self, update_ctx_below_alert):
        payload = m._build_notifiarr_update_payload(update_ctx_below_alert)
        title = payload["discord"]["text"]["title"]
        assert "BELOW ALERT" in title
        assert payload["discord"]["color"] == "E74C3C"

    def test_title_simplified(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        title = payload["discord"]["text"]["title"]
        assert title.startswith("Checking")
        # Should NOT contain dollar amount in title
        assert "$" not in title

    def test_description_present(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        desc = payload["discord"]["text"]["description"]
        assert "bottoms out" in desc
        assert "$8,100" in desc
        assert "~34 days of spending" in desc

    def test_field_labels(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        fields = payload["discord"]["text"]["fields"]
        titles = [f["title"] for f in fields]
        assert "Balance Now" in titles
        assert "Lowest Point" in titles
        assert "Covers" in titles
        assert "Avg Daily Spend" in titles

    def test_cushion_fields_show_formula(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        fields = payload["discord"]["text"]["fields"]
        alert_cushion = next(f for f in fields if "Alert Cushion" in f["title"])
        assert "\u00d7" in alert_cushion["text"]
        assert "$240/day" in alert_cushion["text"]

    def test_avg_daily_shows_trailing_period(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        fields = payload["discord"]["text"]["fields"]
        avg = next(f for f in fields if f["title"] == "Avg Daily Spend")
        assert "(13-mo avg)" in avg["text"]

    def test_footer(self, update_ctx_on_track):
        payload = m._build_notifiarr_update_payload(update_ctx_on_track)
        footer = payload["discord"]["text"]["footer"]
        assert "Through Mar 31, 2026" in footer


# ---------------------------------------------------------------------------
# send_alert_notification — Apprise message format
# ---------------------------------------------------------------------------


class TestAlertAppriseMessage:
    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_narrative_lead(self, mock_notifier, _mock_conf, alert_ctx):
        mock_notifier.return_value.notify.return_value = True
        m.send_alert_notification(alert_ctx)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert body.startswith("After all scheduled bills")
        assert "bottoms out" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_structured_fields(self, mock_notifier, _mock_conf, alert_ctx):
        mock_notifier.return_value.notify.return_value = True
        m.send_alert_notification(alert_ctx)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "Balance now:" in body
        assert "Lowest point:" in body
        assert "Avg daily spend:" in body
        assert "Alert cushion:" in body
        assert "Target cushion:" in body
        assert "(13-mo avg)" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_formula_in_cushion(self, mock_notifier, _mock_conf, alert_ctx):
        mock_notifier.return_value.notify.return_value = True
        m.send_alert_notification(alert_ctx)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "$240/day x 15d" in body
        assert "$240/day x 30d" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_cc_section(self, mock_notifier, _mock_conf, alert_ctx):
        mock_notifier.return_value.notify.return_value = True
        m.send_alert_notification(alert_ctx)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "CC payments:" in body
        assert "Chase Visa" in body
        assert "Amex Gold" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_upcoming_bills(self, mock_notifier, _mock_conf, alert_ctx):
        mock_notifier.return_value.notify.return_value = True
        m.send_alert_notification(alert_ctx)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "Upcoming bills:" in body
        assert "Mortgage" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_action_line(self, mock_notifier, _mock_conf, alert_ctx):
        mock_notifier.return_value.notify.return_value = True
        m.send_alert_notification(alert_ctx)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "Action:" in body
        assert "$4,100" in body
        assert "HYSA" in body
        assert "30-day cushion" in body


# ---------------------------------------------------------------------------
# send_update_notification — Apprise message format
# ---------------------------------------------------------------------------


class TestUpdateAppriseMessage:
    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_narrative_lead(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert body.startswith("After all scheduled bills")
        assert "bottoms out" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_structured_fields(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "Balance now:" in body
        assert "Lowest point:" in body
        assert "Avg daily spend:" in body
        assert "Alert cushion:" in body
        assert "Target cushion:" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_formula_in_cushion(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "$240/day x 15d" in body
        assert "$240/day x 30d" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_covers_text(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "~34 days" in body
        assert "of spending" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_through_date(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        body = mock_notifier.return_value.notify.call_args[1]["body"]
        assert "Through Mar 31, 2026" in body

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_title_status_on_track(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        title = mock_notifier.return_value.notify.call_args[1]["title"]
        assert "On Track" in title
        # Title should NOT have dollar amount
        assert "$" not in title

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_title_status_below_alert(self, mock_notifier, _mock_conf, update_ctx_below_alert):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_below_alert)
        title = mock_notifier.return_value.notify.call_args[1]["title"]
        assert "BELOW ALERT" in title

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_notify_type_warning_below_alert(self, mock_notifier, _mock_conf, update_ctx_below_alert):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_below_alert)
        import apprise

        notify_type = mock_notifier.return_value.notify.call_args[1]["notify_type"]
        assert notify_type == apprise.NotifyType.WARNING

    @patch.object(m, "_notifiarr_configured", return_value=False)
    @patch.object(m, "_build_notifier")
    def test_notify_type_success_on_track(self, mock_notifier, _mock_conf, update_ctx_on_track):
        mock_notifier.return_value.notify.return_value = True
        m.send_update_notification(update_ctx_on_track)
        import apprise

        notify_type = mock_notifier.return_value.notify.call_args[1]["notify_type"]
        assert notify_type == apprise.NotifyType.SUCCESS
