"""Unit tests for live workflow dependency caching."""

from __future__ import annotations

import asyncio

import pytest

from tests.live.dependency_graph import DependencyGraph, node_key


@pytest.mark.asyncio
async def test_ensure_runs_factory_once() -> None:
    graph = DependencyGraph()
    calls = 0

    async def create() -> str:
        nonlocal calls
        calls += 1
        return "verified"

    key = node_key("repo", "owner", "name")
    assert await graph.ensure(key, create) == "verified"
    assert await graph.ensure(key, create) == "verified"
    assert calls == 1


@pytest.mark.asyncio
async def test_concurrent_ensure_shares_in_flight_factory() -> None:
    graph = DependencyGraph()
    calls = 0
    release = asyncio.Event()

    async def create() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        return "verified"

    key = node_key("issue", "owner", "repo", "title")
    first = asyncio.create_task(graph.ensure(key, create))
    second = asyncio.create_task(graph.ensure(key, create))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == ["verified", "verified"]
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_factory_is_retryable() -> None:
    graph = DependencyGraph()
    key = node_key("user", "name")
    calls = 0

    async def create() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            message = "bootstrap failed"
            raise RuntimeError(message)
        return "verified"

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        await graph.ensure(key, create)
    assert await graph.ensure(key, create) == "verified"
    assert calls == 2
