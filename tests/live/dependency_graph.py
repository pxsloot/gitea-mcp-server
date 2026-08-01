"""Small async dependency graph used by live workflow tests.

The graph is deliberately independent of Forgejo and ``World``.  A node is
created by an async factory the first time it is requested; its verified value
is then reused for the remainder of the isolated test world.  Concurrent
requests for the same node share one in-flight task instead of duplicating
setup traffic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Hashable


@dataclass(frozen=True, slots=True)
class NodeKey:
    """Stable identity for one desired dependency node."""

    kind: str
    identity: tuple[Hashable, ...]

    def task_name(self) -> str:
        """Return a useful name for an in-flight setup task."""
        return f"live-ensure-{self.kind}"


def node_key(kind: str, *identity: Hashable) -> NodeKey:
    """Build a node key without exposing the dataclass construction syntax."""
    return NodeKey(kind, identity)


class DependencyGraph:
    """Cache verified workflow dependencies and deduplicate async setup."""

    def __init__(self) -> None:
        self._values: dict[NodeKey, Any] = {}
        self._pending: dict[NodeKey, asyncio.Task[Any]] = {}

    async def ensure(
        self,
        key: NodeKey,
        factory: Any,
    ) -> Any:
        """Return a cached value, or create and cache it with *factory*.

        Failed factories are removed from the graph so a later test can retry
        setup.  Successful factories are cached only after they return; the
        factory therefore represents both setup and its verification.
        """
        if key in self._values:
            return self._values[key]

        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(
                factory(),
                name=key.task_name(),
            )
            self._pending[key] = task

        try:
            value = await task
        except BaseException:
            if self._pending.get(key) is task:
                del self._pending[key]
            raise
        else:
            self._pending.pop(key, None)
            self._values[key] = value
            return value

    def get(self, key: NodeKey) -> Any | None:
        """Return a verified value, or ``None`` when the node is unknown."""
        return self._values.get(key)

    def __contains__(self, key: NodeKey) -> bool:
        return key in self._values

    def __len__(self) -> int:
        return len(self._values)
