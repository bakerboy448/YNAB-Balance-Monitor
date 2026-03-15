"""Tests for delta sync of scheduled transactions."""

import os
import sys
from unittest.mock import patch

import pytest

os.environ.setdefault("YNAB_API_TOKEN", "fake-token")
os.environ.setdefault("YNAB_ACCOUNT_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("NOTIFIARR_CHANNEL_ID", "123456789")
os.environ.setdefault("NOTIFIARR_API_KEY", "fake-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ynab_balance_monitor as m  # noqa: E402


@pytest.fixture()
def cache_dir(tmp_path):
    """Provide a temporary cache directory and patch CACHE_DIR."""
    cache = tmp_path / "cache"
    cache.mkdir()
    with patch.object(m, "CACHE_DIR", str(cache)):
        yield cache


class TestDeltaSync:
    """Test fetch_scheduled_transactions_delta with delta sync."""

    def _base_transactions(self):
        return [
            {
                "id": "txn-1",
                "account_id": m.YNAB_ACCOUNT_IDS[0],
                "amount": -100000,
                "payee_name": "Electric",
                "date_next": "2026-04-01",
                "frequency": "monthly",
                "deleted": False,
            },
            {
                "id": "txn-2",
                "account_id": m.YNAB_ACCOUNT_IDS[0],
                "amount": -50000,
                "payee_name": "Internet",
                "date_next": "2026-04-15",
                "frequency": "monthly",
                "deleted": False,
            },
        ]

    @patch("ynab_balance_monitor.ynab_get")
    def test_full_fetch_on_first_run(self, mock_get, cache_dir):
        """First run with no cache should do a full fetch."""
        txns = self._base_transactions()
        mock_get.return_value = {
            "scheduled_transactions": txns,
            "server_knowledge": 100,
        }

        result = m.fetch_scheduled_transactions_delta()

        mock_get.assert_called_once()
        assert len(result) == 2
        assert result[0]["payee_name"] == "Electric"

        # Cache file should be written
        cache_files = list(cache_dir.glob("delta_scheduled_*.json"))
        assert len(cache_files) == 1

    @patch("ynab_balance_monitor.ynab_get")
    def test_delta_merge_with_additions(self, mock_get, cache_dir):
        """Second run should use delta sync and merge new transactions."""
        base_txns = self._base_transactions()

        # First call — full fetch
        mock_get.return_value = {
            "scheduled_transactions": base_txns,
            "server_knowledge": 100,
        }
        m.fetch_scheduled_transactions_delta()

        # Second call — delta with a new transaction
        new_txn = {
            "id": "txn-3",
            "account_id": m.YNAB_ACCOUNT_IDS[0],
            "amount": -75000,
            "payee_name": "Water",
            "date_next": "2026-04-20",
            "frequency": "monthly",
            "deleted": False,
        }
        mock_get.return_value = {
            "scheduled_transactions": [new_txn],
            "server_knowledge": 105,
        }

        result = m.fetch_scheduled_transactions_delta()

        assert len(result) == 3
        payees = {t["payee_name"] for t in result}
        assert payees == {"Electric", "Internet", "Water"}

    @patch("ynab_balance_monitor.ynab_get")
    def test_delta_merge_with_deletions(self, mock_get, cache_dir):
        """Delta should remove transactions marked as deleted."""
        base_txns = self._base_transactions()

        # First call — full fetch
        mock_get.return_value = {
            "scheduled_transactions": base_txns,
            "server_knowledge": 100,
        }
        m.fetch_scheduled_transactions_delta()

        # Second call — delta with a deletion
        mock_get.return_value = {
            "scheduled_transactions": [{"id": "txn-1", "deleted": True}],
            "server_knowledge": 110,
        }

        result = m.fetch_scheduled_transactions_delta()

        assert len(result) == 1
        assert result[0]["payee_name"] == "Internet"

    @patch("ynab_balance_monitor.ynab_get")
    def test_delta_fallback_on_api_error(self, mock_get, cache_dir):
        """If delta fetch fails, should fall back to full fetch."""
        base_txns = self._base_transactions()

        # First call — full fetch
        mock_get.return_value = {
            "scheduled_transactions": base_txns,
            "server_knowledge": 100,
        }
        m.fetch_scheduled_transactions_delta()

        # Second call — delta fails, then full fetch succeeds
        updated_txns = [self._base_transactions()[0]]  # Only one txn now
        mock_get.side_effect = [
            m.YNABAPIError("delta failed"),  # Delta attempt fails
            {  # Full fetch succeeds
                "scheduled_transactions": updated_txns,
                "server_knowledge": 200,
            },
        ]

        result = m.fetch_scheduled_transactions_delta()
        assert len(result) == 1

    @patch("ynab_balance_monitor.ynab_get")
    def test_cache_invalidation_on_corruption(self, mock_get, cache_dir):
        """Corrupted cache should trigger a full fetch."""
        # Write garbage to cache file
        cache_file = cache_dir / f"delta_scheduled_{m.YNAB_BUDGET_ID}.json"
        cache_file.write_text("not valid json{{{")

        txns = self._base_transactions()
        mock_get.return_value = {
            "scheduled_transactions": txns,
            "server_knowledge": 100,
        }

        result = m.fetch_scheduled_transactions_delta()
        assert len(result) == 2
