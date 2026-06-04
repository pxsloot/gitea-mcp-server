"""Unit tests for GiteaNamespace transform.

Verifies that resource operations pass through unchanged while
tool/prompt operations are handled by the parent Namespace class.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate

from gitea_mcp_server.tools.namespace import GiteaNamespace


@pytest.fixture
def ns():
    return GiteaNamespace(prefix="gitea_")


@pytest.mark.asyncio
async def test_list_resources_returns_unchanged(ns):
    resources = [Resource(uri="gitea://version", name="Version")]
    result = await ns.list_resources(resources)
    assert result is resources
    assert len(result) == 1
    assert str(result[0].uri) == "gitea://version"


@pytest.mark.asyncio
async def test_get_resource_passes_version(ns):
    call_next = AsyncMock(return_value=Resource(uri="gitea://version", name="Version"))
    result = await ns.get_resource("gitea://version", call_next, version="1.0")
    assert result is not None
    assert str(result.uri) == "gitea://version"
    call_next.assert_called_once_with("gitea://version", version="1.0")


@pytest.mark.asyncio
async def test_get_resource_without_version(ns):
    call_next = AsyncMock(return_value=Resource(uri="gitea://version", name="Version"))
    result = await ns.get_resource("gitea://version", call_next)
    call_next.assert_called_once_with("gitea://version", version=None)


@pytest.mark.asyncio
async def test_get_resource_returns_none_when_not_found(ns):
    call_next = AsyncMock(return_value=None)
    result = await ns.get_resource("gitea://nonexistent", call_next)
    assert result is None


@pytest.mark.asyncio
async def test_list_resource_templates_returns_unchanged(ns):
    templates = [ResourceTemplate(uri_template="gitea://repos/{owner}/{repo}", name="Repo", parameters={})]
    result = await ns.list_resource_templates(templates)
    assert result is templates
    assert len(result) == 1
    assert result[0].uri_template == "gitea://repos/{owner}/{repo}"


@pytest.mark.asyncio
async def test_get_resource_template_passes_version(ns):
    call_next = AsyncMock(return_value=ResourceTemplate(uri_template="gitea://repos/{owner}/{repo}", name="Repo", parameters={}))
    result = await ns.get_resource_template("gitea://repos/owner/repo", call_next, version="1.0")
    assert result is not None
    assert result.uri_template == "gitea://repos/{owner}/{repo}"
    call_next.assert_called_once_with("gitea://repos/owner/repo", version="1.0")


@pytest.mark.asyncio
async def test_get_resource_template_without_version(ns):
    call_next = AsyncMock(return_value=ResourceTemplate(uri_template="gitea://repos/{owner}/{repo}", name="Repo", parameters={}))
    result = await ns.get_resource_template("gitea://repos/owner/repo", call_next)
    call_next.assert_called_once_with("gitea://repos/owner/repo", version=None)


@pytest.mark.asyncio
async def test_get_resource_template_returns_none_when_not_found(ns):
    call_next = AsyncMock(return_value=None)
    result = await ns.get_resource_template("gitea://nonexistent", call_next)
    assert result is None
