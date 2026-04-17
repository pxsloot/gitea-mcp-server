"""Label management and caching utilities."""

import logging
from datetime import UTC, datetime
from typing import Any

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.constants import LABEL_CACHE_TTL

logger = logging.getLogger(__name__)


class LabelManager:
    """Manages label caching and retrieval for Gitea repositories.

    Encapsulates the label cache logic that was previously module-level state.
    Provides methods to get label maps and clear the cache.
    """

    def __init__(self, cache_ttl: int = LABEL_CACHE_TTL) -> None:
        """Initialize LabelManager.

        Args:
            cache_ttl: Cache TTL in seconds (default from constants)
        """
        self._cache_ttl = cache_ttl
        self._label_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    async def get_label_map(
        self, owner: str, repo: str, client: GiteaClient
    ) -> dict[str, dict[str, Any]]:
        """Get or fetch label map for a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            client: GiteaClient for API calls if cache miss

        Returns:
            Dict mapping lowercase label names to label info (id, name)
        """
        cache_key = (owner, repo)
        now: datetime = datetime.now(UTC)

        # Check cache
        if cache_key in self._label_cache:
            entry = self._label_cache[cache_key]
            timestamp = entry["timestamp"]
            age = now - timestamp  # type: ignore[operator]
            if age.total_seconds() < self._cache_ttl:
                return entry["map"]

        # Fetch labels from API
        labels = await client.request("GET", f"/repos/{owner}/{repo}/labels")
        # Response is a list of label objects: {id, name, color, description, ...}
        label_map = {}
        for label in labels:
            name = label.get("name", "")
            if name:
                label_map[name.lower()] = {"id": label["id"], "name": label["name"]}

        # Update cache
        self._label_cache[cache_key] = {"map": label_map, "timestamp": now}  # type: ignore[dict-item]
        return label_map

    def clear_cache(self) -> None:
        """Clear all cached label mappings."""
        self._label_cache.clear()


__all__ = ["LabelManager"]
