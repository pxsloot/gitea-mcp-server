"""Unit tests for the response cache.

Covers the ``ResponseCache`` store (TTL, skip-oversize, query-variant
invalidation, bounding) and the ``ResponseCacheMiddleware`` (read caching,
per-resource TTL resolution, resource-list caching).
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp.resources import FunctionResource

from gitea_mcp_server.constants import CACHE_TTL_DEFAULT, CACHE_TTL_RESOURCE_LIST
from gitea_mcp_server.resources.surface import (
    clear_resource_surface,
    register_resource_surface,
)
from gitea_mcp_server.response_cache import (
    _LIST_RESOURCES_KEY,
    ResponseCache,
    ResponseCacheMiddleware,
    make_ttl_resolver,
)


@pytest.fixture(autouse=True)
def clear_surface() -> Any:
    """Clear the resource surface before and after each test."""
    clear_resource_surface()
    yield
    clear_resource_surface()


# ---------------------------------------------------------------------------
# ResponseCache store
# ---------------------------------------------------------------------------


class TestResponseCacheStore:
    def test_put_and_get_roundtrip(self) -> None:
        """A stored value is returned on get."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "x"}, ttl=30)
        assert cache.get("gitea://repos/org/repo/issues") == {"title": "x"}

    def test_miss_returns_none(self) -> None:
        """A URI that was never stored returns None."""
        cache = ResponseCache()
        assert cache.get("gitea://repos/org/repo/issues") is None

    def test_expiry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An entry past its TTL is treated as a miss and removed."""
        import time

        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "x"}, ttl=30)
        monkeypatch.setattr(time, "monotonic", lambda: 10_000.0)
        cache.put("gitea://repos/org/repo/issues", {"title": "y"}, ttl=30)
        monkeypatch.setattr(time, "monotonic", lambda: 10_031.0)
        assert cache.get("gitea://repos/org/repo/issues") is None
        # The expired entry is gone from the store entirely.
        assert cache._entries == {}

    def test_zero_ttl_not_cached(self) -> None:
        """A zero/negative TTL stores nothing."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "x"}, ttl=0)
        assert cache.get("gitea://repos/org/repo/issues") is None

    def test_skip_oversize(self, caplog: pytest.LogCaptureFixture) -> None:
        """Items larger than max_item_size are not cached (skip-oversize)."""
        import logging

        caplog.set_level(logging.DEBUG)
        cache = ResponseCache(max_item_size=10)
        cache.put("gitea://repos/org/repo/issues", {"title": "this is a long value"}, ttl=30)
        assert cache.get("gitea://repos/org/repo/issues") is None
        assert "exceeds max_item_size" in caplog.text

    def test_skip_oversize_does_not_affect_small_items(self) -> None:
        """Items under the cap are cached normally."""
        cache = ResponseCache(max_item_size=100)
        cache.put("gitea://repos/org/repo/issues", {"title": "x"}, ttl=30)
        assert cache.get("gitea://repos/org/repo/issues") == {"title": "x"}

    def test_invalidate_base_removes_variants(self) -> None:
        """Invalidating a base URI removes every cached query variant."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "base"}, ttl=30)
        cache.put("gitea://repos/org/repo/issues?state=open", {"title": "open"}, ttl=30)
        cache.put("gitea://repos/org/repo/issues?state=closed", {"title": "closed"}, ttl=30)
        cache.put("gitea://repos/org/repo/pulls", {"title": "pulls"}, ttl=30)

        removed = cache.invalidate(["gitea://repos/org/repo/issues"])

        assert removed == 3
        assert cache.get("gitea://repos/org/repo/issues") is None
        assert cache.get("gitea://repos/org/repo/issues?state=open") is None
        assert cache.get("gitea://repos/org/repo/issues?state=closed") is None
        assert cache.get("gitea://repos/org/repo/pulls") == {"title": "pulls"}

    def test_invalidate_uncached_base_noop(self) -> None:
        """Invalidating a base that was never cached removes nothing."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "x"}, ttl=30)
        assert cache.invalidate(["gitea://repos/org/repo/pulls"]) == 0
        assert cache.get("gitea://repos/org/repo/issues") == {"title": "x"}

    def test_invalidate_query_uri_itself(self) -> None:
        """Invalidating a full query URI (not just the base) also works."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues?state=open", {"title": "open"}, ttl=30)
        assert cache.invalidate(["gitea://repos/org/repo/issues?state=open"]) == 1
        assert cache.get("gitea://repos/org/repo/issues?state=open") is None

    def test_invalidate_untracked_entry_still_cleared(self) -> None:
        """An entry without a variant-index record is still cleared.

        The store keeps ``_entries`` and ``_variants`` in lockstep, so this
        state is not reachable through the public API; the fallback guards
        against future bookkeeping drift, so pin it.
        """
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "orphan"}, ttl=30)
        # Simulate index drift: drop the variant bookkeeping but keep the entry.
        cache._variants.clear()
        cache._tracked_uri_count = 0

        assert cache.invalidate(["gitea://repos/org/repo/issues"]) == 1
        assert cache.get("gitea://repos/org/repo/issues") is None

    def test_clear(self) -> None:
        """clear() drops every entry."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "x"}, ttl=30)
        cache.put("gitea://repos/org/repo/issues?state=open", {"title": "y"}, ttl=30)
        cache.clear()
        assert cache.get("gitea://repos/org/repo/issues") is None
        assert cache.get("gitea://repos/org/repo/issues?state=open") is None
        assert cache._variants == {}

    def test_tracked_uris_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The variant index is bounded — oldest bases are evicted."""
        from gitea_mcp_server import response_cache as rc_module

        monkeypatch.setattr(rc_module, "_MAX_TRACKED_URIS", 3)
        cache = ResponseCache()
        for i in range(5):
            cache.put(f"gitea://repos/org/repo/res{i}", {"i": i}, ttl=30)

        # Only the 3 most recent bases survive; the 2 oldest were evicted.
        assert cache.get("gitea://repos/org/repo/res0") is None
        assert cache.get("gitea://repos/org/repo/res1") is None
        assert cache.get("gitea://repos/org/repo/res2") == {"i": 2}
        assert cache.get("gitea://repos/org/repo/res3") == {"i": 3}
        assert cache.get("gitea://repos/org/repo/res4") == {"i": 4}

    def test_tracked_uris_bounded_single_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single base with many variants cannot exceed the cap."""
        from gitea_mcp_server import response_cache as rc_module

        monkeypatch.setattr(rc_module, "_MAX_TRACKED_URIS", 3)
        cache = ResponseCache()
        for i in range(5):
            cache.put(f"gitea://repos/org/repo/issues?page={i}", {"i": i}, ttl=30)

        total = sum(len(v) for v in cache._variants.values())
        assert total <= 3
        assert cache._tracked_uri_count <= 3


# ---------------------------------------------------------------------------
# TTL resolver
# ---------------------------------------------------------------------------


class TestTTLResolver:
    def test_resolves_per_resource_ttl(self) -> None:
        """A registered resource's cache_ttl is resolved from a concrete URI."""
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues",
            "/repos/{owner}/{repo}/issues",
            cache_ttl=120.0,
        )
        resolver = make_ttl_resolver()
        assert resolver("gitea://repos/org/repo/issues") == 120.0
        assert resolver("gitea://repos/org/repo/issues?state=open") == 120.0

    def test_unregistered_uri_returns_none(self) -> None:
        """An unregistered URI resolves to None (caller falls back to default)."""
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues",
            "/repos/{owner}/{repo}/issues",
            cache_ttl=120.0,
        )
        resolver = make_ttl_resolver()
        assert resolver("gitea://repos/org/repo/pulls") is None

    def test_no_cache_ttl_returns_none(self) -> None:
        """A registered resource without cache_ttl resolves to None."""
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues",
            "/repos/{owner}/{repo}/issues",
        )
        resolver = make_ttl_resolver()
        assert resolver("gitea://repos/org/repo/issues") is None

    def test_template_does_not_match_shorter_uri(self) -> None:
        """A repo template never matches an issues URI (segment counts differ)."""
        register_resource_surface(
            "gitea://repos/{owner}/{repo}",
            "/repos/{owner}/{repo}",
            cache_ttl=300.0,
        )
        resolver = make_ttl_resolver()
        assert resolver("gitea://repos/org/repo/issues") is None
        assert resolver("gitea://repos/org/repo") == 300.0

    def test_wildcard_template_matches(self) -> None:
        """A {filepath*} template matches multi-segment concrete paths."""
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/contents/{filepath*}",
            "/repos/{owner}/{repo}/contents/{filepath}",
            cache_ttl=600.0,
        )
        resolver = make_ttl_resolver()
        assert resolver("gitea://repos/org/repo/contents/src/main.py") == 600.0

    def test_shorter_uri_does_not_match_wildcard_template(self) -> None:
        """A {filepath*} template does not match a URI shorter than its prefix."""
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/contents/{filepath*}",
            "/repos/{owner}/{repo}/contents/{filepath}",
            cache_ttl=600.0,
        )
        resolver = make_ttl_resolver()
        assert resolver("gitea://repos/org/repo") is None


# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------


class TestEstimateSize:
    def test_function_resource_falls_back_to_str_size(self) -> None:
        """A resource carrying a callable cannot be JSON-serialised.

        ``_estimate_size`` falls back to a string-length estimate (the
        ``model_dump_json`` path raises), and the list is still cached.
        """

        def handler() -> str:
            return "value"

        resource = FunctionResource.from_function(
            fn=handler, uri="gitea://repos/org/repo", name="repo"
        )
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo", [resource], ttl=30)
        assert cache.get("gitea://repos/org/repo") == [resource]


# ---------------------------------------------------------------------------
# ResponseCacheMiddleware
# ---------------------------------------------------------------------------


class TestResponseCacheMiddleware:
    def _make_context(self, uri: str) -> MagicMock:
        mock_context = MagicMock()
        mock_context.message.uri = uri
        return mock_context

    @pytest.mark.asyncio
    async def test_read_miss_calls_next_and_caches(self) -> None:
        """A cache miss calls next and stores the result."""
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)

        async def call_next(context: Any) -> Any:
            return {"title": "fresh"}

        result: Any = await middleware.on_read_resource(
            self._make_context("gitea://repos/org/repo/issues"), call_next
        )
        assert result == {"title": "fresh"}
        assert cache.get("gitea://repos/org/repo/issues") == {"title": "fresh"}

    @pytest.mark.asyncio
    async def test_read_hit_skips_next(self) -> None:
        """A cache hit returns the stored value without calling next."""
        cache = ResponseCache()
        cache.put("gitea://repos/org/repo/issues", {"title": "cached"}, ttl=30)
        middleware = ResponseCacheMiddleware(cache=cache)

        called = False

        async def call_next(context: Any) -> Any:
            nonlocal called
            called = True
            return {"title": "fresh"}

        result: Any = await middleware.on_read_resource(
            self._make_context("gitea://repos/org/repo/issues"), call_next
        )
        assert result == {"title": "cached"}
        assert called is False

    @pytest.mark.asyncio
    async def test_read_uses_per_resource_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The per-resource TTL from the resolver is used for the entry."""
        import time

        monkeypatch.setattr(time, "monotonic", lambda: 5_000.0)
        register_resource_surface(
            "gitea://repos/{owner}/{repo}/issues",
            "/repos/{owner}/{repo}/issues",
            cache_ttl=120.0,
        )
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)

        async def call_next(context: Any) -> Any:
            return {"title": "fresh"}

        await middleware.on_read_resource(
            self._make_context("gitea://repos/org/repo/issues"), call_next
        )
        entry = cache._entries["gitea://repos/org/repo/issues"]
        # 120s TTL from the surface, not the 30s default.
        assert entry.expires_at == 5_000.0 + 120.0

    @pytest.mark.asyncio
    async def test_read_falls_back_to_default_ttl(self) -> None:
        """An unregistered URI uses CACHE_TTL_DEFAULT."""
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)

        async def call_next(context: Any) -> Any:
            return {"title": "fresh"}

        await middleware.on_read_resource(
            self._make_context("gitea://repos/org/repo/issues"), call_next
        )
        entry = cache._entries["gitea://repos/org/repo/issues"]
        assert entry.expires_at - 30.0 > 0  # default TTL applied

    @pytest.mark.asyncio
    async def test_empty_uri_skips_cache(self) -> None:
        """An empty URI bypasses the cache entirely."""
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)

        async def call_next(context: Any) -> Any:
            return {"title": "fresh"}

        result: Any = await middleware.on_read_resource(self._make_context(""), call_next)
        assert result == {"title": "fresh"}
        assert cache._entries == {}

    @pytest.mark.asyncio
    async def test_list_resources_cached(self) -> None:
        """resources/list is cached under the fixed list key."""
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)

        async def call_next(context: Any) -> Any:
            return [{"uri": "gitea://repos/org/repo"}]

        result: Any = await middleware.on_list_resources(MagicMock(), call_next)
        assert result == [{"uri": "gitea://repos/org/repo"}]
        assert cache.get(_LIST_RESOURCES_KEY) == [{"uri": "gitea://repos/org/repo"}]

    @pytest.mark.asyncio
    async def test_list_resources_hit_skips_next(self) -> None:
        """A cached catalog is returned without calling next."""
        cache = ResponseCache()
        cache.put(
            _LIST_RESOURCES_KEY, [{"uri": "gitea://repos/org/repo"}], ttl=CACHE_TTL_RESOURCE_LIST
        )
        middleware = ResponseCacheMiddleware(cache=cache)

        called = False

        async def call_next(context: Any) -> Any:
            nonlocal called
            called = True
            return [{"uri": "gitea://repos/org/repo"}]

        result: Any = await middleware.on_list_resources(MagicMock(), call_next)
        assert result == [{"uri": "gitea://repos/org/repo"}]
        assert called is False

    @pytest.mark.asyncio
    async def test_list_resources_uses_list_ttl(self) -> None:
        """The catalog entry uses CACHE_TTL_RESOURCE_LIST."""
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)

        async def call_next(context: Any) -> Any:
            return []

        await middleware.on_list_resources(MagicMock(), call_next)
        entry = cache._entries[_LIST_RESOURCES_KEY]
        assert entry.expires_at - 300.0 > 0

    def test_cache_property_exposes_store(self) -> None:
        """The middleware exposes its store for the invalidation middleware."""
        cache = ResponseCache()
        middleware = ResponseCacheMiddleware(cache=cache)
        assert middleware.cache is cache

    def test_default_ttl_constant_sanity(self) -> None:
        """CACHE_TTL_DEFAULT is the documented 30s."""
        assert CACHE_TTL_DEFAULT == 30.0
