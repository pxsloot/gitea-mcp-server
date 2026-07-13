"""Unit tests for LabelService — stats, cache clearing, and context logging."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitea_mcp_server.label_service import LabelService


class TestLabelService:
    """Tests for LabelService core functionality."""

    @pytest.fixture
    def label_service(self):
        return LabelService(cache_ttl=300)

    @pytest.fixture
    def gitea_client(self):
        client = AsyncMock()
        # Return two labels: id=1 name="bug", id=42 name="feature"
        client.request.return_value = [
            {"id": 1, "name": "bug"},
            {"id": 42, "name": "feature"},
        ]
        return client

    # ------------------------------------------------------------------
    # stats()
    # ------------------------------------------------------------------

    def test_stats_empty_cache(self, label_service):
        """stats() on empty cache returns zero counts."""
        stats = label_service.stats()
        assert stats["hit_ratio"] == 0.0
        assert stats["entry_count"] == 0
        assert stats["oldest_entry_age_seconds"] is None
        assert stats["total_hits"] == 0
        assert stats["total_misses"] == 0
        assert stats["total_fetches"] == 0

    @pytest.mark.asyncio
    async def test_stats_tracks_hits(self, label_service, gitea_client):
        """stats() hit_ratio reflects cache hits."""
        # First call: fetch from API (miss)
        await label_service.get_label_map("owner", "repo", gitea_client)
        stats = label_service.stats()
        assert stats["total_misses"] == 1
        assert stats["total_fetches"] == 1
        assert stats["total_hits"] == 0
        assert stats["hit_ratio"] == 0.0

        # Second call: cache hit
        await label_service.get_label_map("owner", "repo", gitea_client)
        stats = label_service.stats()
        assert stats["total_hits"] == 1
        assert stats["total_misses"] == 1
        assert stats["total_fetches"] == 1
        # hit_ratio = hits / (hits + misses) = 1 / 2
        assert stats["hit_ratio"] == 0.5

    @pytest.mark.asyncio
    async def test_stats_entry_count(self, label_service, gitea_client):
        """stats() entry_count reflects number of cached repos."""
        await label_service.get_label_map("owner1", "repo1", gitea_client)
        await label_service.get_label_map("owner2", "repo2", gitea_client)
        stats = label_service.stats()
        assert stats["entry_count"] == 2

    @pytest.mark.asyncio
    async def test_stats_oldest_entry(self, label_service, gitea_client):
        """stats() oldest_entry_age_seconds is set when cache is populated."""
        await label_service.get_label_map("owner", "repo", gitea_client)
        stats = label_service.stats()
        assert stats["oldest_entry_age_seconds"] is not None
        assert stats["oldest_entry_age_seconds"] >= 0

    # ------------------------------------------------------------------
    # clear_cache_for()
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_clear_cache_for_removes_entry(self, label_service, gitea_client):
        """clear_cache_for removes only the specified repo from cache."""
        # Populate cache with two repos
        await label_service.get_label_map("owner1", "repo1", gitea_client)
        await label_service.get_label_map("owner2", "repo2", gitea_client)
        assert label_service.stats()["entry_count"] == 2

        # Clear one
        label_service.clear_cache_for("owner1", "repo1")
        assert label_service.stats()["entry_count"] == 1

        # The other repo should still be cached
        # (no additional API call needed)
        gitea_client.request.reset_mock()
        await label_service.get_label_map("owner2", "repo2", gitea_client)
        gitea_client.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_cache_for_missing_key_no_error(self, label_service):
        """clear_cache_for with a non-existent key does not raise."""
        # Should not raise
        label_service.clear_cache_for("nonexistent", "repo")

    @pytest.mark.asyncio
    async def test_clear_cache_for_triggers_refetch(self, label_service, gitea_client):
        """After clear_cache_for, the next access fetches from API."""
        await label_service.get_label_map("owner", "repo", gitea_client)
        gitea_client.request.reset_mock()

        label_service.clear_cache_for("owner", "repo")
        await label_service.get_label_map("owner", "repo", gitea_client)
        gitea_client.request.assert_awaited_once()

    # ------------------------------------------------------------------
    # Context logging
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_context_logging_on_cache_miss(self, label_service, gitea_client):
        """Context logging is called on cache miss (outside request scope, no crash)."""
        # Should not raise even though no CurrentContext is active
        await label_service.get_label_map("owner", "repo", gitea_client)
        # No assertion needed — we just verify no exception occurs

    @pytest.mark.asyncio
    async def test_context_logging_on_cache_hit(self, label_service, gitea_client):
        """Context logging is called on cache hit (outside request scope, no crash)."""
        await label_service.get_label_map("owner", "repo", gitea_client)
        # Second call: cache hit
        await label_service.get_label_map("owner", "repo", gitea_client)
        # No assertion needed — verify no exception

    @pytest.mark.asyncio
    async def test_context_logging_on_expiry(self, label_service, gitea_client):
        """Context logging on cache expiry does not crash."""
        service = LabelService(cache_ttl=0)  # immediate expiry
        await service.get_label_map("owner", "repo", gitea_client)
        # TTL is 0 so next call will be expired
        await service.get_label_map("owner", "repo", gitea_client)
        # No exception = pass
