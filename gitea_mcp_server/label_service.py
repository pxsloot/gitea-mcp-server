"""Unified label management, caching, and validation service.

Consolidates all label business logic — fetching, caching, validation,
and conversion — into a single ``LabelService`` class.  Replaces the
previous ``LabelManager`` (caching only) and fragmented helpers in
``tools/labels.py``.
"""

import logging
from datetime import UTC, datetime
from typing import Any, TypedDict

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import LABEL_CACHE_TTL
from gitea_mcp_server.exceptions import ValidationError

logger = logging.getLogger(__name__)


class _LabelCacheEntry(TypedDict):
    map: dict[str, dict[str, Any]]
    id_map: dict[int, dict[str, Any]]
    timestamp: datetime


class _LabelCacheStats(TypedDict, total=False):
    hit_ratio: float
    entry_count: int
    oldest_entry_age_seconds: float | None
    total_hits: int
    total_misses: int
    total_fetches: int


class LabelService:
    """Unified service for label caching, validation, and conversion.

    Encapsulates all label business logic: fetching the remote label map,
    caching with configurable TTL, validating both string names and integer
    IDs against the map, and formatting available labels for error messages.

    .. note::

       Every call to ``validate_and_convert`` — even for already-known
       integer IDs — triggers a cache lookup (and on first use per repo, an
       HTTP ``GET /repos/{owner}/{repo}/labels``).  Previously, integer-only
       label lists skipped validation entirely.  The cost is intentional:
       integer IDs from one repo are meaningless in another, and silent
       failures confuse agents more than a cache miss.

    Usage::

        service = LabelService()
        converted = await service.validate_and_convert(
            ["bug", 42], owner="org", repo="repo", client=gitea_client
        )
        # → [1, 42]  (assuming "bug" → id 1, and 42 exists)
    """

    def __init__(self, cache_ttl: int = LABEL_CACHE_TTL) -> None:
        """Initialize LabelService.

        Args:
            cache_ttl: Cache TTL in seconds (default from constants).
        """
        self._cache_ttl = cache_ttl
        self._label_cache: dict[tuple[str, str], _LabelCacheEntry] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._cache_fetches: int = 0

    async def get_label_map(
        self, owner: str, repo: str, client: GiteaClient
    ) -> dict[str, dict[str, Any]]:
        """Get or fetch the name→info label map for a repository.

        The returned dict maps **lowercased** label names to
        ``{"id": int, "name": str}``.

        Args:
            owner: Repository owner.
            repo: Repository name.
            client: GiteaClient for API calls on cache miss.

        Returns:
            Dict mapping lowercase label names to label info.
        """
        entry = await self._get_or_fetch(owner, repo, client)
        return entry["map"]

    async def get_id_map(
        self, owner: str, repo: str, client: GiteaClient
    ) -> dict[int, dict[str, Any]]:
        """Get or fetch the id→info label map for a repository.

        The returned dict maps integer label IDs to
        ``{"id": int, "name": str}``.

        Args:
            owner: Repository owner.
            repo: Repository name.
            client: GiteaClient for API calls on cache miss.

        Returns:
            Dict mapping integer label IDs to label info.
        """
        entry = await self._get_or_fetch(owner, repo, client)
        return entry["id_map"]

    async def validate_and_convert(
        self,
        labels: list[str | int],
        owner: str,
        repo: str,
        client: GiteaClient,
    ) -> list[int]:
        """Validate and convert labels, returning integer IDs.

        This is the **single entry point** for label processing.  It validates
        **both** string names and integer IDs against the remote label map,
        collecting all unknowns before raising a single ``ValidationError``.

        Args:
            labels: List of label names (strings) or IDs (integers).
            owner: Repository owner.
            repo: Repository name.
            client: GiteaClient for API calls on cache miss.

        Returns:
            List of validated integer label IDs.

        Raises:
            ValidationError: If any label is unknown (name not found or
                ID not found in the repository's labels).
        """
        if not labels:
            return []

        entry = await self._get_or_fetch(owner, repo, client)
        name_map = entry["map"]
        id_map = entry["id_map"]

        converted: list[int] = []
        unknown_names: list[str] = []
        unknown_ids: list[int] = []

        for label in labels:
            if isinstance(label, str):
                label_lower = label.lower()
                if label_lower in name_map:
                    converted.append(name_map[label_lower]["id"])
                else:
                    unknown_names.append(label)
            elif isinstance(label, int):
                if label in id_map:
                    converted.append(label)
                else:
                    unknown_ids.append(label)

        if unknown_names or unknown_ids:
            available = await self.format_available(owner, repo, client)
            parts: list[str] = []
            if unknown_names:
                parts.append(f"Unknown label name(s): {unknown_names}")
            if unknown_ids:
                parts.append(f"Unknown label ID(s): {unknown_ids}")
            msg = (
                f"{'; '.join(parts)}.\n\n"
                f"Available labels for {owner}/{repo}:\n"
                f"{available}\n\n"
                f"Use list_labels({owner}, {repo}) or read "
                f"gitea://repos/{owner}/{repo}/labels to see details."
            )
            raise ValidationError(message=msg, field="labels")

        return converted

    async def format_available(
        self, owner: str, repo: str, client: GiteaClient
    ) -> str:
        """Return a human-readable, grouped listing of available labels.

        Labels with a ``/`` prefix (e.g. ``Kind/Bug``) are grouped by their
        prefix for readability.

        Args:
            owner: Repository owner.
            repo: Repository name.
            client: GiteaClient for API calls on cache miss.

        Returns:
            Grouped, formatted string of available label names.
        """
        name_map = await self.get_label_map(owner, repo, client)
        label_names = sorted(v["name"] for v in name_map.values())
        return self._format_label_names(label_names)

    def clear_cache(self) -> None:
        """Clear all cached label mappings."""
        self._label_cache.clear()

    def clear_cache_for(self, owner: str, repo: str) -> None:
        """Clear the cached label mapping for a specific repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
        """
        key = (owner, repo)
        if key in self._label_cache:
            del self._label_cache[key]
            logger.debug("Cleared label cache for %s/%s", owner, repo)

    def stats(self) -> _LabelCacheStats:
        """Return operational statistics about the label cache.

        Returns:
            Dict with hit_ratio, entry_count, oldest_entry_age_seconds,
            total_hits, total_misses, total_fetches.
        """
        total = self._cache_hits + self._cache_misses
        now = datetime.now(UTC)
        oldest_ts: datetime | None = None

        for entry in self._label_cache.values():
            ts = entry["timestamp"]
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts

        oldest_age: float | None = None
        if oldest_ts is not None:
            oldest_age = (now - oldest_ts).total_seconds()

        return {
            "hit_ratio": self._cache_hits / max(total, 1),
            "entry_count": len(self._label_cache),
            "oldest_entry_age_seconds": oldest_age,
            "total_hits": self._cache_hits,
            "total_misses": self._cache_misses,
            "total_fetches": self._cache_fetches,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _log_ctx_info(self, msg: str, **extra: Any) -> None:
        """Log a message via MCP context if available, otherwise via stdlib."""
        logger.debug("%s | extra=%s", msg, extra)
        try:
            # Deferred import: CurrentContext only works inside an MCP
            # request scope. Importing at the top level would fail when
            # LabelService is used outside a request (e.g., unit tests).
            # PLC0415 suppressed intentionally.
            from fastmcp.dependencies import CurrentContext  # noqa: PLC0415

            async with CurrentContext() as ctx:
                if ctx is not None:
                    await ctx.info(msg, extra=extra)
        except RuntimeError:
            pass

    async def _get_or_fetch(
        self, owner: str, repo: str, client: GiteaClient
    ) -> _LabelCacheEntry:
        """Return a cached entry or fetch + cache from the API."""
        cache_key = (owner, repo)
        now: datetime = datetime.now(UTC)

        if cache_key in self._label_cache:
            entry = self._label_cache[cache_key]
            age = now - entry["timestamp"]
            if age.total_seconds() < self._cache_ttl:
                self._cache_hits += 1
                await self._log_ctx_info(
                    "LabelService cache hit for %s/%s",
                    owner=owner,
                    repo=repo,
                    cache_age_seconds=age.total_seconds(),
                )
                return entry
            self._cache_misses += 1
            await self._log_ctx_info(
                "LabelService cache expired for %s/%s",
                owner=owner,
                repo=repo,
                cache_age_seconds=age.total_seconds(),
            )
        else:
            self._cache_misses += 1

        self._cache_fetches += 1
        await self._log_ctx_info(
            "LabelService fetching labels for %s/%s",
            owner=owner,
            repo=repo,
        )
        labels = await client.request("GET", f"/repos/{owner}/{repo}/labels")
        name_map: dict[str, dict[str, Any]] = {}
        id_map: dict[int, dict[str, Any]] = {}
        for label in labels:
            label_id = label["id"]
            label_name = label.get("name", "")
            info = {"id": label_id, "name": label_name}
            if label_name:
                name_map[label_name.lower()] = info
            id_map[label_id] = info

        entry = {
            "map": name_map,
            "id_map": id_map,
            "timestamp": now,
        }
        self._label_cache[cache_key] = entry
        return entry

    @staticmethod
    def _format_label_names(label_names: list[str]) -> str:
        """Group label names by ``/`` prefix and format for display."""
        groups: dict[str, list[str]] = {}
        for name in label_names:
            prefix = name.split("/", 1)[0] if "/" in name else ""
            groups.setdefault(prefix, []).append(name)

        lines: list[str] = []
        for prefix in sorted(groups, key=lambda p: (p == "", p)):
            label_list = sorted(groups[prefix])
            lines.append(f"  - {', '.join(label_list)}")
        return "\n".join(lines)


__all__ = [
    "LabelService",
    "_LabelCacheStats",
]
