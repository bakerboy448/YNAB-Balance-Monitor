"""Tests for disk caching of monthly expenses."""

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


class TestReadWriteCache:
    """Test _read_cache and _write_cache round-trip."""

    def test_write_and_read(self, cache_dir):
        filepath = str(cache_dir / "test.json")
        m._write_cache(filepath, {"key": "value"})

        result = m._read_cache(filepath, ttl_seconds=3600)
        assert result is not None
        assert result["key"] == "value"
        assert "cached_at" in result

    def test_expired_cache_returns_none(self, cache_dir):
        filepath = str(cache_dir / "expired.json")
        m._write_cache(filepath, {"key": "value"})

        # Simulate expired TTL by reading with 0s TTL
        result = m._read_cache(filepath, ttl_seconds=0)
        assert result is None

    def test_missing_file_returns_none(self, cache_dir):
        result = m._read_cache(str(cache_dir / "missing.json"), ttl_seconds=3600)
        assert result is None

    def test_corrupt_json_returns_none(self, cache_dir):
        filepath = str(cache_dir / "corrupt.json")
        with open(filepath, "w") as f:
            f.write("not valid json{{{")

        result = m._read_cache(filepath, ttl_seconds=3600)
        assert result is None


class TestMonthlyExpensesCache:
    """Test calculate_monthly_expenses caching behavior."""

    def _mock_month_response(self, month_str):
        """Create a mock YNAB month response."""
        return {
            "month": {
                "categories": [
                    {
                        "category_group_name": "Bills",
                        "activity": -500000,  # -$500 in milliunits
                        "deleted": False,
                        "hidden": False,
                    },
                    {
                        "category_group_name": "Food",
                        "activity": -300000,
                        "deleted": False,
                        "hidden": False,
                    },
                    {
                        "category_group_name": "Credit Card Payments",
                        "activity": -200000,
                        "deleted": False,
                        "hidden": False,
                    },
                ]
            }
        }

    @patch("ynab_balance_monitor.ynab_get")
    def test_caches_monthly_expenses(self, mock_get, cache_dir):
        """First call should fetch from API and write cache."""
        mock_get.side_effect = lambda path: self._mock_month_response(path)

        avg_daily, avg_monthly = m.calculate_monthly_expenses()

        # 13 API calls (one per month)
        assert mock_get.call_count == 13
        assert avg_monthly > 0

        # Cache file should exist
        cache_files = list(cache_dir.glob("monthly_expenses_*.json"))
        assert len(cache_files) == 1

    @patch("ynab_balance_monitor.ynab_get")
    def test_uses_cached_data_on_second_call(self, mock_get, cache_dir):
        """Second call should use cache and not call API."""
        mock_get.side_effect = lambda path: self._mock_month_response(path)

        # First call — populates cache
        m.calculate_monthly_expenses()
        assert mock_get.call_count == 13

        mock_get.reset_mock()

        # Second call — should use cache
        avg_daily2, avg_monthly2 = m.calculate_monthly_expenses()
        mock_get.assert_not_called()
        assert avg_monthly2 > 0

    @patch("ynab_balance_monitor.ynab_get")
    def test_dry_run_bypasses_cache(self, mock_get, cache_dir):
        """--dry-run should always fetch fresh data."""
        mock_get.side_effect = lambda path: self._mock_month_response(path)

        # First call — populates cache
        m.calculate_monthly_expenses()
        mock_get.reset_mock()

        # Dry-run should bypass cache
        with patch.object(m, "DRY_RUN", True):
            m.calculate_monthly_expenses()
            assert mock_get.call_count == 13
