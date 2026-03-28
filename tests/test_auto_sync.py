"""Tests for auto-sync/refresh logic in CLI."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from pr_dashboard.cli import _auto_sync_if_stale


class FakeMeta:
    """Minimal stand-in for store.db with get_meta/set_meta."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    def get_meta(self, key, default=""):
        return self._data.get(key, default)

    def set_meta(self, key, value):
        self._data[key] = value


class FakeStore:
    def __init__(self, meta: dict | None = None):
        self.db = FakeMeta(meta)
        self.sync = AsyncMock()
        self.refresh_all = AsyncMock()


def _iso(minutes_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


DEFAULT_SYNC_CFG = {
    "auto_refresh_enabled": True,
    "auto_refresh_interval": 30,
    "auto_sync_enabled": True,
    "auto_sync_interval": 1440,
}


class TestAutoSyncIfStale:
    @pytest.fixture(autouse=True)
    def _patch_config(self, monkeypatch):
        self._sync_cfg = dict(DEFAULT_SYNC_CFG)
        monkeypatch.setattr(
            "pr_dashboard.cli.get_sync_config", lambda: self._sync_cfg
        )

    def test_both_stale_only_sync_runs(self):
        """When both sync and refresh are stale, only sync fires (no double-run)."""
        store = FakeStore({
            "last_sync_time": _iso(1500),     # 25h ago — sync stale
            "last_refresh_time": _iso(60),    # 1h ago — refresh stale too
        })
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_awaited_once()
        store.refresh_all.assert_not_awaited()

    def test_sync_stale_refresh_fresh(self):
        """Sync stale, refresh fresh → sync runs, refresh does not."""
        store = FakeStore({
            "last_sync_time": _iso(1500),
            "last_refresh_time": _iso(5),
        })
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_awaited_once()
        store.refresh_all.assert_not_awaited()

    def test_sync_fresh_refresh_stale(self):
        """Sync fresh, refresh stale → only refresh runs."""
        store = FakeStore({
            "last_sync_time": _iso(60),       # 1h ago — sync fresh (< 24h)
            "last_refresh_time": _iso(45),    # 45m ago — refresh stale (> 30m)
        })
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_not_awaited()
        store.refresh_all.assert_awaited_once()

    def test_both_fresh_nothing_runs(self):
        """Both timestamps fresh → nothing runs."""
        store = FakeStore({
            "last_sync_time": _iso(10),
            "last_refresh_time": _iso(5),
        })
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_not_awaited()
        store.refresh_all.assert_not_awaited()

    def test_no_timestamps_triggers_sync(self):
        """No timestamps recorded → auto-sync triggers (treats as stale)."""
        store = FakeStore({})
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_awaited_once()
        store.refresh_all.assert_not_awaited()

    def test_sync_disabled_only_refresh(self):
        """Auto-sync disabled → only refresh considered."""
        self._sync_cfg["auto_sync_enabled"] = False
        store = FakeStore({
            "last_sync_time": _iso(1500),
            "last_refresh_time": _iso(45),
        })
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_not_awaited()
        store.refresh_all.assert_awaited_once()

    def test_sync_disabled_no_refresh_timestamp_triggers_refresh(self):
        """Auto-sync disabled, no refresh timestamp → refresh triggers."""
        self._sync_cfg["auto_sync_enabled"] = False
        store = FakeStore({})
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_not_awaited()
        store.refresh_all.assert_awaited_once()

    def test_both_disabled_nothing_runs(self):
        """Both disabled → nothing runs even if stale."""
        self._sync_cfg["auto_sync_enabled"] = False
        self._sync_cfg["auto_refresh_enabled"] = False
        store = FakeStore({
            "last_sync_time": _iso(1500),
            "last_refresh_time": _iso(60),
        })
        asyncio.run(_auto_sync_if_stale(store))
        store.sync.assert_not_awaited()
        store.refresh_all.assert_not_awaited()

    def test_sync_updates_both_timestamps(self):
        """After auto-sync, both last_sync_time and last_refresh_time are updated."""
        store = FakeStore({
            "last_sync_time": _iso(1500),
            "last_refresh_time": _iso(60),
        })
        asyncio.run(_auto_sync_if_stale(store))
        assert store.db.get_meta("last_sync_time") != ""
        assert store.db.get_meta("last_refresh_time") != ""
        # Both should be recent (within last second)
        sync_ts = datetime.fromisoformat(store.db.get_meta("last_sync_time"))
        refresh_ts = datetime.fromisoformat(store.db.get_meta("last_refresh_time"))
        assert (datetime.now(UTC) - sync_ts).total_seconds() < 5
        assert (datetime.now(UTC) - refresh_ts).total_seconds() < 5
