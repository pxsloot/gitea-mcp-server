"""Response cache for resource reads and resource listings.

This module implements the response cache for the two methods this server
caches (``resources/read`` and ``resources/list``).

Design decisions:

* **Single global key space.** Keys are the *canonical* resource URIs (see
  below); the format is defined here, so the writer and the invalidator can
  never drift apart.
* **Canonical keys.** A URI is canonicalised by percent-decoding it once, so
  equivalent spellings of the same resource (raw and percent-encoded) share
  one entry.  FastMCP's resource matcher ``unquote``s captured path parameters,
  so the decoded form is the logical resource identity; it is also what
  ``cache_invalidation`` reconstructs from tool arguments.  Canonicalisation is
  applied exactly once, at this store boundary (``unquote`` is not idempotent).
* **Per-resource TTL.** Each resource may declare a ``cache_ttl`` (via the
  resource surface, populated from ``make_api_resource(cache_ttl=...)``);
  resources without one fall back to ``CACHE_TTL_DEFAULT``.  Resource
  listings use ``CACHE_TTL_RESOURCE_LIST``.
* **Skip-oversize.** Items larger than ``max_item_size`` are not cached —
  the read still succeeds and is returned to the caller, it is simply not
  stored.  Caching a pathological huge response would pin memory; serving
  it uncached is the safe choice.
* **Query-variant invalidation.** The store indexes every cached URI under
  its base (query-stripped) URI, so a write can delete every variant of a
  base it invalidates — not just the base itself.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    import mcp
    from fastmcp.resources.base import Resource, ResourceResult
    from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext

    from gitea_mcp_server.resources.surface import ResourceSurfaceEntry

from fastmcp.server.middleware.middleware import Middleware

from gitea_mcp_server.constants import (
    CACHE_MAX_ITEM_SIZE,
    CACHE_TTL_DEFAULT,
    CACHE_TTL_RESOURCE_LIST,
)
from gitea_mcp_server.resources.surface import get_resource_surface

logger = logging.getLogger(__name__)

# Fixed key for the resource catalog (``resources/list``).  The catalog is
# static per server run and never invalidated by writes, so a single entry
# per server is all it needs.
_LIST_RESOURCES_KEY = "__gitea_list_resources__"

# Maximum number of distinct cached URIs tracked for query-variant
# invalidation.  Bounds memory on long-lived servers.  Cache entries expire
# via TTL anyway, so evicting the oldest tracked base only risks leaving a
# variant stale until its TTL expires — never permanently.
_MAX_TRACKED_URIS = 50


def _canonical_uri(uri: str) -> str:
    """Return the canonical cache key for a resource URI.

    Percent-decodes the URI once so equivalent spellings of the same resource
    (raw and percent-encoded) share one entry.  Applied exactly once, at the
    store boundary: ``unquote`` is not idempotent (``unquote("a%2520b")`` is
    ``"a%20b"``, and decoding again would yield ``"a b"``), so callers pass
    the URI as received, never an already-decoded form.
    """
    return unquote(uri)


class _CacheEntry:
    """One cached value with its expiry deadline."""

    __slots__ = ("expires_at", "value")

    def __init__(self, value: Any, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class ResponseCache:
    """In-memory TTL cache for resource reads and resource listings.

    Single global key space keyed by canonical URI (see module docstring).
    Keys are owned here, so invalidation can never drift from the writer.

    Entries expire lazily on read (TTL check) and are evicted eagerly on
    write invalidation.  Items larger than ``max_item_size`` are skipped
    (not cached); the read still succeeds.

    The store also indexes every cached URI under its base (query-stripped)
    URI, so :meth:`invalidate` can delete every query variant of a base —
    the cache key includes the query string, so a write must clear every
    variant that has been read, not just the base URI.
    """

    def __init__(self, max_item_size: int = CACHE_MAX_ITEM_SIZE) -> None:
        self._max_item_size = max_item_size
        self._entries: dict[str, _CacheEntry] = {}
        # base URI -> set of full URIs currently cached under that base.
        self._variants: dict[str, set[str]] = defaultdict(set)
        self._tracked_uri_count = 0

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def get(self, uri: str) -> Any | None:
        """Return the cached value for ``uri``, or ``None`` on miss/expiry."""
        uri = _canonical_uri(uri)
        entry = self._entries.get(uri)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._remove(uri)
            return None
        return entry.value

    def put(self, uri: str, value: Any, ttl: float) -> None:
        """Store ``value`` under ``uri`` for ``ttl`` seconds.

        Items larger than ``max_item_size`` are skipped (not cached).  The
        URI is canonicalised and indexed under its base for query-variant
        invalidation.
        """
        if ttl <= 0:
            return
        uri = _canonical_uri(uri)
        size = _estimate_size(value)
        if size > self._max_item_size:
            logger.debug(
                "Not caching %s: serialized size %d exceeds max_item_size %d",
                uri,
                size,
                self._max_item_size,
            )
            return
        self._entries[uri] = _CacheEntry(value, time.monotonic() + ttl)
        base = uri.split("?", 1)[0]
        variants = self._variants[base]
        if uri not in variants:
            variants.add(uri)
            self._tracked_uri_count += 1
            self._evict_oldest_base_if_needed()

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(self, uris: Iterable[str]) -> int:
        """Delete every cached entry whose base URI is in ``uris``.

        URIs are canonicalised before lookup, so a raw invalidation target
        (as reconstructed from tool arguments) matches a percent-encoded read.
        Includes query variants recorded at read time.  Returns the number
        of entries removed.
        """
        removed = 0
        for raw_uri in uris:
            uri = _canonical_uri(raw_uri)
            base = uri.split("?", 1)[0]
            variants = self._variants.pop(base, None)
            if variants is not None:
                for variant in variants:
                    if self._entries.pop(variant, None) is not None:
                        removed += 1
                self._tracked_uri_count -= len(variants)
            elif self._entries.pop(base, None) is not None:
                removed += 1
        return removed

    def clear(self) -> None:
        """Drop every cached entry (test isolation / server teardown)."""
        self._entries.clear()
        self._variants.clear()
        self._tracked_uri_count = 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _remove(self, uri: str) -> None:
        """Remove one entry (expiry path) and keep the variant index accurate.

        ``uri`` is already canonical (``get`` canonicalises before calling);
        canonicalise exactly once at the public boundary, never here.
        """
        self._entries.pop(uri, None)
        base = uri.split("?", 1)[0]
        variants = self._variants.get(base)
        if variants is not None and uri in variants:
            variants.discard(uri)
            self._tracked_uri_count -= 1
            if not variants:
                del self._variants[base]

    def _evict_oldest_base_if_needed(self) -> None:
        """Evict the oldest tracked base (and its entries) past the cap."""
        while self._tracked_uri_count > _MAX_TRACKED_URIS:
            oldest_base = next(iter(self._variants))
            variants = self._variants.pop(oldest_base)
            self._tracked_uri_count -= len(variants)
            for variant in variants:
                self._entries.pop(variant, None)


def _estimate_size(value: Any) -> int:
    """Rough serialized size of a cached value in bytes.

    A safety-valve estimate for skip-oversize — exactness is not required.
    Falls back to a string-length estimate when the value cannot be
    serialized (e.g. ``FunctionResource`` objects carrying handler
    callables in their ``fn`` field).
    """
    if hasattr(value, "model_dump_json"):
        try:
            return len(value.model_dump_json())
        except (TypeError, ValueError):
            return len(str(value))
    if isinstance(value, (list, tuple)):
        return sum(_estimate_size(item) for item in value)
    return len(str(value))


def _uri_matches(template: str, concrete: str) -> bool:
    """True if a URI template matches a concrete URI segment-wise.

    ``{param}`` template segments match any single concrete segment; a
    trailing ``{param*}`` wildcard matches one or more remaining segments
    (multi-segment values like file paths).  Segment counts must otherwise
    be equal — a repo template never matches an issues URI.
    """
    t = template.split("/")
    c = concrete.split("/")
    # A trailing ``{param*}`` wildcard matches one or more remaining segments.
    if t and t[-1].startswith("{") and t[-1].endswith("*}"):
        prefix = t[:-1]
        if len(c) < len(prefix):
            return False
        return all(ts.startswith("{") or ts == cs for ts, cs in zip(prefix, c))
    if len(t) != len(c):
        return False
    return all(ts.startswith("{") or ts == cs for ts, cs in zip(t, c))


def make_ttl_resolver(
    surface: Mapping[str, ResourceSurfaceEntry] | None = None,
) -> Callable[[str], float | None]:
    """Build a resolver mapping a concrete resource URI to its cache TTL.

    Matches the concrete URI (query stripped) against the registered
    resource surface and returns the entry's ``cache_ttl``, or ``None``
    when the resource is not registered (caller falls back to the default).

    Args:
        surface: The resource surface (base URI template -> entry).
            Defaults to the live module-level registry — the same dict the
            registration code mutates, so a resolver built before resource
            registration still sees the populated surface at read time.
    """
    surface = surface if surface is not None else get_resource_surface()

    def resolve(uri: str) -> float | None:
        base = uri.split("?", 1)[0]
        for template, entry in surface.items():
            if _uri_matches(template, base):
                return entry.cache_ttl
        return None

    return resolve


class ResponseCacheMiddleware(Middleware):
    """Cache resource reads and resource listings in the store.

    TTL policy: per-resource ``cache_ttl`` from the resource surface when
    set, else ``CACHE_TTL_DEFAULT``.  Resource listings use
    ``CACHE_TTL_RESOURCE_LIST``.
    """

    def __init__(
        self,
        cache: ResponseCache | None = None,
        ttl_resolver: Callable[[str], float | None] | None = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            cache: The cache store.  Defaults to a fresh ``ResponseCache``.
            ttl_resolver: Maps a concrete resource URI to its per-resource
                TTL (``None`` means "use the default").  Defaults to a
                resolver over the live resource surface.
        """
        self._cache = cache if cache is not None else ResponseCache()
        self._ttl_resolver = ttl_resolver if ttl_resolver is not None else make_ttl_resolver()

    @property
    def cache(self) -> ResponseCache:
        """The cache store (handed to the invalidation middleware)."""
        return self._cache

    async def on_read_resource(
        self,
        context: MiddlewareContext[mcp.types.ReadResourceRequestParams],
        call_next: CallNext[mcp.types.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        """Serve a cached resource read, or read, store, and return."""
        uri = str(context.message.uri)
        if not uri:
            return await call_next(context)

        cached: ResourceResult | None = self._cache.get(uri)
        if cached is not None:
            return cached

        result = await call_next(context)
        self._cache.put(uri, result, self._ttl_for(uri))
        return result

    async def on_list_resources(
        self,
        context: MiddlewareContext[mcp.types.ListResourcesRequest],
        call_next: CallNext[mcp.types.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        """Serve the cached resource catalog, or list, store, and return."""
        cached: Sequence[Resource] | None = self._cache.get(_LIST_RESOURCES_KEY)
        if cached is not None:
            return cached

        result = await call_next(context)
        self._cache.put(_LIST_RESOURCES_KEY, result, CACHE_TTL_RESOURCE_LIST)
        return result

    def _ttl_for(self, uri: str) -> float:
        """Resolve the per-resource TTL for a URI, falling back to the default."""
        ttl = self._ttl_resolver(uri)
        return ttl if ttl is not None else CACHE_TTL_DEFAULT


__all__ = [
    "ResponseCache",
    "ResponseCacheMiddleware",
    "make_ttl_resolver",
]
