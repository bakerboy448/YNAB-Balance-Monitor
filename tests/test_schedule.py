"""Tests for daemon scheduling: digest (update) notifications."""

import os
import sys
from unittest.mock import patch

import pytest

os.environ.setdefault("YNAB_API_TOKEN", "fake-token")
os.environ.setdefault("YNAB_ACCOUNT_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
os.environ.setdefault("NOTIFIARR_CHANNEL_ID", "123456789")
os.environ.setdefault("NOTIFIARR_API_KEY", "fake-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ynab_balance_monitor as m  # noqa: E402


class TestShouldSendUpdate:
    def test_schedule_only_sends_digest_on_every_check(self):
        assert m._should_send_update(do_check=True, do_update=False, update_schedule=None) is True

    def test_update_schedule_set_check_only_no_digest(self):
        assert m._should_send_update(do_check=True, do_update=False, update_schedule=("daily", 18, 0)) is False

    def test_update_schedule_fires(self):
        assert m._should_send_update(do_check=False, do_update=True, update_schedule=("daily", 18, 0)) is True


class TestDaemonLoop:
    def _run_first_cycle(self, schedule, update_schedule):
        """Run main() until the first run_check call, then stop the loop."""
        with (
            patch.object(m, "SCHEDULE", schedule),
            patch.object(m, "UPDATE_SCHEDULE", update_schedule),
            patch.object(m, "validate_config"),
            patch.object(m.signal, "signal"),
            patch.object(m, "run_check", side_effect=KeyboardInterrupt) as run_check,
            pytest.raises(KeyboardInterrupt),
        ):
            m.main()
        return run_check

    def test_schedule_only_sends_digest(self):
        run_check = self._run_first_cycle("6h", "")
        run_check.assert_called_once_with(send_update=True)

    def test_separate_update_schedule_check_has_no_digest(self):
        # Interval check fires immediately; daily update schedule is still in the future
        run_check = self._run_first_cycle("6h", "23:59")
        run_check.assert_called_once_with(send_update=False)
