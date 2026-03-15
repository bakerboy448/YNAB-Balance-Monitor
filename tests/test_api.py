"""Tests for YNAB API retry logic and data deduplication."""

import os
import sys
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

os.environ.setdefault("YNAB_API_TOKEN", "fake-token")
os.environ.setdefault("YNAB_ACCOUNT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("NOTIFIARR_CHANNEL_ID", "123456789")
os.environ.setdefault("NOTIFIARR_API_KEY", "fake-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ynab_balance_monitor as m  # noqa: E402


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Test _ynab_request retry behavior."""

    def _make_http_error(self, code, body="error", headers=None):
        resp = MagicMock()
        resp.read.return_value = body.encode()
        resp.code = code
        resp.headers = headers or {}
        return HTTPError("http://test", code, "error", headers or {}, resp)

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_retries_on_5xx(self, mock_urlopen, mock_sleep):
        """Should retry on 5xx and succeed on the last attempt."""
        good_resp = MagicMock()
        good_resp.read.return_value = b'{"data": {"ok": true}}'
        good_resp.__enter__ = MagicMock(return_value=good_resp)
        good_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            self._make_http_error(503),
            self._make_http_error(500),
            good_resp,
        ]

        result = m._ynab_request("GET", "/test")
        assert result == {"ok": True}
        assert mock_sleep.call_count == 2
        # Backoff: 30s, 60s
        mock_sleep.assert_any_call(30)
        mock_sleep.assert_any_call(60)

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_fails_immediately_on_401(self, mock_urlopen, mock_sleep):
        """Should not retry on 401 auth errors."""
        mock_urlopen.side_effect = self._make_http_error(401, "unauthorized")

        with pytest.raises(m.YNABAPIError, match="auth error"):
            m._ynab_request("GET", "/test")

        mock_sleep.assert_not_called()

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_fails_immediately_on_403(self, mock_urlopen, mock_sleep):
        """Should not retry on 403 auth errors."""
        mock_urlopen.side_effect = self._make_http_error(403, "forbidden")

        with pytest.raises(m.YNABAPIError, match="auth error"):
            m._ynab_request("GET", "/test")

        mock_sleep.assert_not_called()

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_429_honors_retry_after(self, mock_urlopen, mock_sleep):
        """Should use Retry-After header value for 429 responses."""
        good_resp = MagicMock()
        good_resp.read.return_value = b'{"data": {"ok": true}}'
        good_resp.__enter__ = MagicMock(return_value=good_resp)
        good_resp.__exit__ = MagicMock(return_value=False)

        headers = {"Retry-After": "45"}
        mock_urlopen.side_effect = [
            self._make_http_error(429, headers=headers),
            good_resp,
        ]

        result = m._ynab_request("GET", "/test")
        assert result == {"ok": True}
        mock_sleep.assert_called_once_with(45)

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_retries_on_network_error(self, mock_urlopen, mock_sleep):
        """Should retry on URLError (network issues)."""
        good_resp = MagicMock()
        good_resp.read.return_value = b'{"data": {"ok": true}}'
        good_resp.__enter__ = MagicMock(return_value=good_resp)
        good_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            URLError("Connection refused"),
            good_resp,
        ]

        result = m._ynab_request("GET", "/test")
        assert result == {"ok": True}
        mock_sleep.assert_called_once_with(30)

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_retries_on_timeout(self, mock_urlopen, mock_sleep):
        """Should retry on TimeoutError."""
        good_resp = MagicMock()
        good_resp.read.return_value = b'{"data": {"ok": true}}'
        good_resp.__enter__ = MagicMock(return_value=good_resp)
        good_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            TimeoutError("timed out"),
            good_resp,
        ]

        result = m._ynab_request("GET", "/test")
        assert result == {"ok": True}

    @patch("ynab_balance_monitor.time.sleep")
    @patch("ynab_balance_monitor.urlopen")
    def test_raises_after_exhausting_retries(self, mock_urlopen, mock_sleep):
        """Should raise YNABAPIError after all retries exhausted."""
        mock_urlopen.side_effect = [
            self._make_http_error(503),
            self._make_http_error(503),
            self._make_http_error(503),
        ]

        with pytest.raises(m.YNABAPIError, match="failed after 3 attempts"):
            m._ynab_request("GET", "/test")

        assert mock_sleep.call_count == 3


# ---------------------------------------------------------------------------
# Deduplication: get_covered_cc_ids with pre-fetched data
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Test functions accept pre-fetched data to avoid duplicate API calls."""

    def test_get_covered_cc_ids_with_prefetched_data(self):
        """get_covered_cc_ids extracts CC IDs from raw scheduled transactions."""
        checking_id = m.YNAB_ACCOUNT_IDS[0]
        cc_id_1 = "cc-1111"
        cc_id_2 = "cc-2222"
        raw = [
            # Transfer from checking to CC
            {"account_id": checking_id, "transfer_account_id": cc_id_1, "deleted": False},
            # Transfer from CC to checking (reverse direction)
            {"account_id": cc_id_2, "transfer_account_id": checking_id, "deleted": False},
            # Deleted — should be ignored
            {"account_id": checking_id, "transfer_account_id": "cc-deleted", "deleted": True},
            # Non-transfer — should be ignored
            {"account_id": checking_id, "transfer_account_id": None, "deleted": False},
        ]

        result = m.get_covered_cc_ids(raw)
        assert cc_id_1 in result
        assert cc_id_2 in result
        assert "cc-deleted" not in result

    @patch("ynab_balance_monitor.ynab_get")
    def test_get_scheduled_transactions_uses_prefetched(self, mock_get):
        """get_scheduled_transactions should not call API when raw_scheduled is provided."""
        from datetime import date

        raw = [
            {
                "account_id": m.YNAB_ACCOUNT_IDS[0],
                "transfer_account_id": None,
                "deleted": False,
                "date_next": "2026-04-01",
                "frequency": "never",
                "amount": -100000,
                "payee_name": "Test Payee",
            }
        ]

        result = m.get_scheduled_transactions(date(2026, 4, 30), raw_scheduled=raw)
        mock_get.assert_not_called()
        assert len(result) == 1
        assert result[0]["payee"] == "Test Payee"

    @patch("ynab_balance_monitor.ynab_get")
    def test_get_account_balances_uses_prefetched(self, mock_get):
        """get_account_balances should not call API when all_accounts is provided."""
        all_accounts = [
            {
                "id": m.YNAB_ACCOUNT_IDS[0],
                "name": "Test Checking",
                "balance": 5000000,  # $5000 in milliunits
                "type": "checking",
            }
        ]

        total, accounts = m.get_account_balances(all_accounts=all_accounts)
        mock_get.assert_not_called()
        assert total == 5000.0
        assert accounts[0]["name"] == "Test Checking"
