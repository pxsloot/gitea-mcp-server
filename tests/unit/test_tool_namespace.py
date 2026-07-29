"""Unit tests for GiteaNamespace transform.

Verifies that resource operations pass through unchanged while
tool/prompt operations are handled by the parent Namespace class.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.utilities.versions import VersionSpec
from pydantic import AnyUrl

from gitea_mcp_server.tools.namespace import GiteaNamespace

_V1 = "gitea://version"
_V1_SPEC = VersionSpec(eq="1.0")
_REPO_TEMPLATE = "gitea://repos/{owner}/{repo}"
_REPO_URI = "gitea://repos/owner/repo"
_NONEXISTENT = "gitea://nonexistent"


@pytest.fixture
def ns() -> GiteaNamespace:
    return GiteaNamespace(prefix="gitea_")


def _make_repo_template() -> ResourceTemplate:
    """Create a standard repository ResourceTemplate for test use."""
    return ResourceTemplate(uri_template=_REPO_TEMPLATE, name="Repo", parameters={})


@pytest.mark.asyncio
async def test_list_resources_returns_unchanged(ns: GiteaNamespace) -> None:
    resources = [Resource(uri=AnyUrl(_V1), name="Version")]
    result = await ns.list_resources(resources)
    assert result is resources
    assert len(result) == 1
    assert str(result[0].uri) == _V1


@pytest.mark.asyncio
async def test_get_resource_passes_version(ns: GiteaNamespace) -> None:
    call_next = AsyncMock(return_value=Resource(uri=AnyUrl(_V1), name="Version"))
    result = await ns.get_resource(_V1, call_next, version=_V1_SPEC)
    assert result is not None
    assert str(result.uri) == _V1
    call_next.assert_called_once_with(_V1, version=_V1_SPEC)


@pytest.mark.asyncio
async def test_get_resource_without_version(ns: GiteaNamespace) -> None:
    call_next = AsyncMock(return_value=Resource(uri=AnyUrl(_V1), name="Version"))
    result = await ns.get_resource(_V1, call_next)
    call_next.assert_called_once_with(_V1, version=None)


@pytest.mark.asyncio
async def test_get_resource_returns_none_when_not_found(ns: GiteaNamespace) -> None:
    call_next = AsyncMock(return_value=None)
    result = await ns.get_resource(_NONEXISTENT, call_next)
    assert result is None


@pytest.mark.asyncio
async def test_list_resource_templates_returns_unchanged(ns: GiteaNamespace) -> None:
    templates = [_make_repo_template()]
    result = await ns.list_resource_templates(templates)
    assert result is templates
    assert len(result) == 1
    assert result[0].uri_template == _REPO_TEMPLATE


@pytest.mark.asyncio
async def test_get_resource_template_passes_version(ns: GiteaNamespace) -> None:
    call_next = AsyncMock(return_value=_make_repo_template())
    result = await ns.get_resource_template(_REPO_URI, call_next, version=_V1_SPEC)
    assert result is not None
    assert result.uri_template == _REPO_TEMPLATE
    call_next.assert_called_once_with(_REPO_URI, version=_V1_SPEC)


@pytest.mark.asyncio
async def test_get_resource_template_without_version(ns: GiteaNamespace) -> None:
    call_next = AsyncMock(return_value=_make_repo_template())
    result = await ns.get_resource_template(_REPO_URI, call_next)
    call_next.assert_called_once_with(_REPO_URI, version=None)


@pytest.mark.asyncio
async def test_get_resource_template_returns_none_when_not_found(ns: GiteaNamespace) -> None:
    call_next = AsyncMock(return_value=None)
    result = await ns.get_resource_template(_NONEXISTENT, call_next)
    assert result is None
