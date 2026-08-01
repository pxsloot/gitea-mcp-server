"""Unit tests for World-owned live workflow state."""

from __future__ import annotations

from tests.live.workflows import Workflow
from tests.live.world import World


def test_workflows_share_world_dependency_graph() -> None:
    """Separate workflow facades use one authoritative graph per World."""
    world = World("https://example.test", "token", ["server"])

    first = Workflow(world)
    second = Workflow(world)

    assert first.dependencies is world.dependency_graph
    assert second.dependencies is first.dependencies
