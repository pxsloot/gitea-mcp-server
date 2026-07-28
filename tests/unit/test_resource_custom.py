"""Tests for resources/custom.py.

Covers:
    - register_custom_resources registration (count, URIs, server_info_md)
    - String response handling for each custom resource
    - Base64 decoding for file/readme resources
    - Validation error paths (invalid state/type params)
    - Token/scopes resource
    - Server info resource
"""

import base64
import json
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.exceptions import ResourceError
from mcp.server.fastmcp import FastMCP

from gitea_mcp_server.resources.custom import register_custom_resources


class TestRegisterCustomResources:
    """Tests for register_custom_resources."""

    @pytest.fixture
    def mock_mcp(self) -> MagicMock:
        """Create a mock FastMCP instance."""
        return MagicMock(spec=FastMCP)

    @pytest.fixture
    def mock_gitea_client(self) -> AsyncMock:
        """Create a mock GiteaClient."""
        return AsyncMock()

    async def test_registers_all_custom_resources(self, mock_mcp: MagicMock, mock_gitea_client: AsyncMock) -> None:
        """Test that all expected custom resources are registered."""
        register_custom_resources(mock_mcp, mock_gitea_client)
        assert mock_mcp.resource.call_count == 12

    async def test_custom_resources_have_expected_uris(self, mock_mcp: MagicMock, mock_gitea_client: AsyncMock) -> None:
        """Test that the 12 custom resources have the expected URI templates."""
        register_custom_resources(mock_mcp, mock_gitea_client)
        uri_templates = [call[0][0] for call in mock_mcp.resource.call_args_list]
        expected = [
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/readme",
            "gitea://repos/{owner}/{repo}/issues{?state,type}",
            "gitea://repos/{owner}/{repo}/pulls{?state}",
            "gitea://repos/{owner}/{repo}/files/{path*}",
            "gitea://repos/{owner}/{repo}/releases{?draft,q}",
            "gitea://repos/{owner}/{repo}/labels",
            "gitea://users/{username}",
            "gitea://user",
            "gitea://orgs/{orgname}",
            "gitea://version",
            "gitea://token/scopes",
        ]
        for template in expected:
            assert template in uri_templates

    async def test_registers_all_custom_resources_with_server_info_md(
        self, mock_mcp: MagicMock, mock_gitea_client: AsyncMock
    ) -> None:
        """Test that 13 custom resources are registered when server_info_md is provided."""
        register_custom_resources(
            mock_mcp,
            mock_gitea_client,
            openapi_spec={"info": {"title": "test", "version": "1.0.0"}},
            server_info_md="# Server Information\n\n**Server Type**: Test\n**API Version**: 1.0\n",
        )
        assert mock_mcp.resource.call_count == 13

    async def test_custom_resources_include_server_info_uri(
        self, mock_mcp: MagicMock, mock_gitea_client: AsyncMock
    ) -> None:
        """Test that gitea://server/info is registered when server_info_md is provided."""
        register_custom_resources(
            mock_mcp,
            mock_gitea_client,
            server_info_md="# Server Information\n\n**Server Type**: Test\n**API Version**: 1.0\n",
        )
        uri_templates = [call[0][0] for call in mock_mcp.resource.call_args_list]
        assert "gitea://server/info" in uri_templates


class TestCustomResourceStringResponsePaths:
    """Tests for string response handling in custom resources.

    These hit the isinstance(data, str) early-return paths in each resource
    function. We call register_custom_resources with a mock FastMCP, capture
    the resource functions, then invoke them with a client returning text
    responses.
    """

    @pytest.fixture
    def mock_gitea_client_str(self) -> AsyncMock:
        """GiteaClient that returns string (text) responses."""
        client = AsyncMock()
        client.config.token = "test-token"
        return client

    @pytest.fixture
    def captured_resources(self, mock_gitea_client_str: AsyncMock) -> dict[str, Any]:
        """Register custom resources and capture the resulting functions keyed by URI.

        Uses mock_gitea_client_str so all resource functions close over it.
        Wraps captured functions to auto-extract content from ``ResourceResult``
        so tests can work with strings as before.

        The wrapper converts positional args to keyword args by extracting
        param names from the URI template (``{owner}``, ``{repo}``, etc.).
        This allows calling factory-generated handlers (which use ``**kwargs``)
        with the same positional-arg style as ``@_register`` handlers.
        """
        from fastmcp.resources import ResourceResult

        mcp = MagicMock(spec=FastMCP)
        registered: dict[str, object] = {}

        def resource_decorator(uri: str, **kwargs: Any) -> Callable:
            # Extract all param names from URI template for positional-to-keyword
            # conversion.  Matches ``{param}``, ``{param*}`` (greedy path),
            # ``{?param}`` (RFC 6570 optional query params), and RFC 6570
            # multi-param form ``{?a,b,c}``.  The ``?`` prefix is stripped,
            # comma-separated groups are split, and ``*`` suffixes removed.
            import re
            _param_names = [
                stripped
                for m in re.finditer(r"\{(\?*[\w?,*]+)\}", uri)
                for part in m.group(1).lstrip("?").rstrip("*").split(",")
                if (stripped := part.strip())
            ]

            def deco(func: Callable) -> Callable:
                async def wrapper(*args: object, **kwargs: object) -> str:
                    # Convert positional args to keyword if needed
                    if args:
                        for i, arg in enumerate(args):
                            if i < len(_param_names):
                                kwargs.setdefault(_param_names[i], arg)
                    result = await func(**kwargs)
                    if isinstance(result, ResourceResult):
                        return str(result.contents[0].content)
                    return str(result)
                registered[uri] = wrapper
                return func

            return deco

        mcp.resource = resource_decorator

        register_custom_resources(
            mcp, mock_gitea_client_str,
            version_str="1.0.0",
        )
        return registered

    @pytest.mark.asyncio
    async def test_get_repository_string_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """isinstance(data, str) returns string directly for repos."""
        func = captured_resources["gitea://repos/{owner}/{repo}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string response")
        result = await func("owner", "repo")
        assert result == "string response"

    @pytest.mark.asyncio
    async def test_get_readme_string_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """isinstance(data, str) returns string directly for readme."""
        func = captured_resources["gitea://repos/{owner}/{repo}/readme"]
        mock_gitea_client_str.request = AsyncMock(return_value="string readme")
        result = await func("owner", "repo")
        assert result == "string readme"

    @pytest.mark.asyncio
    async def test_get_readme_nondict_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Non-dict, non-string response returns str(response)."""
        func = captured_resources["gitea://repos/{owner}/{repo}/readme"]
        mock_gitea_client_str.request = AsyncMock(return_value=42)
        result = await func("owner", "repo")
        assert result == "42"

    @pytest.mark.asyncio
    async def test_get_readme_no_encoding(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Dict without 'base64' encoding returns content field directly."""
        func = captured_resources["gitea://repos/{owner}/{repo}/readme"]
        mock_gitea_client_str.request = AsyncMock(return_value={"content": "Hello World"})
        result = await func("owner", "repo")
        assert result == "Hello World"

    @pytest.mark.asyncio
    async def test_list_repo_issues_invalid_state(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Invalid state parameter raises ResourceError."""

        func = captured_resources["gitea://repos/{owner}/{repo}/issues{?state,type}"]
        with pytest.raises(ResourceError) as exc_info:
            await func("owner", "repo", state="invalid_state")
        error_data = exc_info.value.args[0]
        assert error_data["code"] == "VALIDATION_ERROR"
        assert "Invalid state parameter" in error_data["message"]
        assert error_data["resource_type"] == "issues"

    @pytest.mark.asyncio
    async def test_list_repo_issues_invalid_type(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Invalid type parameter raises ResourceError."""

        func = captured_resources["gitea://repos/{owner}/{repo}/issues{?state,type}"]
        with pytest.raises(ResourceError) as exc_info:
            await func("owner", "repo", type="invalid_type")
        error_data = exc_info.value.args[0]
        assert error_data["code"] == "VALIDATION_ERROR"
        assert "Invalid type parameter" in error_data["message"]

    @pytest.mark.asyncio
    async def test_list_repo_issues_string_response(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """isinstance(data, str) returns string directly for issues."""
        func = captured_resources["gitea://repos/{owner}/{repo}/issues{?state,type}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string issues")
        result = await func("owner", "repo")
        assert result == "string issues"

    @pytest.mark.asyncio
    async def test_list_repo_pulls_invalid_state(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Invalid state parameter for pulls raises ResourceError."""

        func = captured_resources["gitea://repos/{owner}/{repo}/pulls{?state}"]
        with pytest.raises(ResourceError) as exc_info:
            await func("owner", "repo", state="invalid_state")
        error_data = exc_info.value.args[0]
        assert error_data["code"] == "VALIDATION_ERROR"
        assert "Invalid state parameter" in error_data["message"]
        assert error_data["resource_type"] == "pulls"

    @pytest.mark.asyncio
    async def test_list_repo_pulls_string_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """isinstance(data, str) returns string directly for pulls."""
        func = captured_resources["gitea://repos/{owner}/{repo}/pulls{?state}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string pulls")
        result = await func("owner", "repo")
        assert result == "string pulls"

    @pytest.mark.asyncio
    async def test_get_file_string_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """isinstance(data, str) returns string directly for file."""
        func = captured_resources["gitea://repos/{owner}/{repo}/files/{path*}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string file")
        result = await func("owner", "repo", "some/path/file.py")
        assert result == "string file"

    @pytest.mark.asyncio
    async def test_get_file_nondict_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Non-dict, non-string file response returns str(response)."""
        func = captured_resources["gitea://repos/{owner}/{repo}/files/{path*}"]
        mock_gitea_client_str.request = AsyncMock(return_value=[1, 2, 3])
        result = await func("owner", "repo", "f")
        assert result == "[1, 2, 3]"

    @pytest.mark.asyncio
    async def test_get_file_encoding_base64(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """File with base64 encoding decodes content."""

        encoded = base64.b64encode(b"file content").decode()
        func = captured_resources["gitea://repos/{owner}/{repo}/files/{path*}"]
        mock_gitea_client_str.request = AsyncMock(
            return_value={"content": encoded, "encoding": "base64"}
        )
        result = await func("owner", "repo", "f.py")
        assert result == "file content"

    @pytest.mark.asyncio
    async def test_get_file_no_encoding(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """File with no encoding returns content field directly."""
        func = captured_resources["gitea://repos/{owner}/{repo}/files/{path*}"]
        mock_gitea_client_str.request = AsyncMock(return_value={"content": "plain text"})
        result = await func("owner", "repo", "f.py")
        assert result == "plain text"

    @pytest.mark.asyncio
    async def test_get_file_with_ref_param(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """File with ref parameter passes ref to the API."""
        func = captured_resources["gitea://repos/{owner}/{repo}/files/{path*}"]
        mock_gitea_client_str.request = AsyncMock(return_value={"content": "ref content"})
        result = await func("owner", "repo", "f.py", ref="main")
        assert result == "ref content"
        mock_gitea_client_str.request.assert_called_once()
        _, kwargs = mock_gitea_client_str.request.call_args
        assert kwargs.get("params") == {"ref": "main"}

    @pytest.mark.asyncio
    async def test_list_repo_releases_string_response(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """isinstance(data, str) returns string directly for releases."""
        func = captured_resources["gitea://repos/{owner}/{repo}/releases{?draft,q}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string releases")
        result = await func("owner", "repo")
        assert result == "string releases"

    @pytest.mark.asyncio
    async def test_list_repo_releases_empty(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Empty releases list returns raw JSON empty array."""
        func = captured_resources["gitea://repos/{owner}/{repo}/releases{?draft,q}"]
        mock_gitea_client_str.request = AsyncMock(return_value=[])
        result = await func("owner", "repo")
        assert result == "[]"

    @pytest.mark.asyncio
    async def test_get_user_string_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """isinstance(data, str) returns string directly for user."""
        func = captured_resources["gitea://users/{username}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string user")
        result = await func("username")
        assert result == "string user"

    @pytest.mark.asyncio
    async def test_get_current_user_string_response(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """isinstance(data, str) returns string directly for current user."""
        func = captured_resources["gitea://user"]
        mock_gitea_client_str.request = AsyncMock(return_value="string current user")
        result = await func()
        assert result == "string current user"

    @pytest.mark.asyncio
    async def test_get_current_user_dict_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Dict response from get_current_user returns formatted user."""
        func = captured_resources["gitea://user"]
        mock_gitea_client_str.request = AsyncMock(
            return_value={
                "login": "testuser",
                "full_name": "Test User",
                "email": "test@example.com",
                "html_url": "https://example.com/user/testuser",
            }
        )
        result = await func()
        assert "testuser" in result

    @pytest.mark.asyncio
    async def test_get_org_string_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """isinstance(data, str) returns string directly for org."""
        func = captured_resources["gitea://orgs/{orgname}"]
        mock_gitea_client_str.request = AsyncMock(return_value="string org")
        result = await func("orgname")
        assert result == "string org"

    @pytest.mark.asyncio
    async def test_get_version_returns_precomputed_string(self, captured_resources: dict[str, Any]) -> None:
        """Version returns the pre-computed string, no API call on read."""
        func = captured_resources["gitea://version"]
        result = await func()
        assert result == "1.0.0"

    @pytest.mark.asyncio
    async def test_token_scopes_returns_none_when_no_scopes(self, captured_resources: dict[str, Any]) -> None:
        """token/scopes returns null scopes when available_scopes is None."""
        func = captured_resources["gitea://token/scopes"]
        result = await func()
        data = json.loads(result)
        assert data["scopes"] is None

    @pytest.mark.asyncio
    async def test_token_scopes_returns_sorted_scopes(self, mock_gitea_client_str: AsyncMock) -> None:
        """token/scopes returns sorted scopes from pre-computed available_scopes."""
        from fastmcp.resources import ResourceResult

        mcp = MagicMock(spec=FastMCP)
        registered: dict[str, object] = {}

        def resource_decorator(uri: str, **kwargs: Any) -> Callable:
            def deco(func: Callable) -> Callable:
                async def wrapper(*args: object, **kwargs: object) -> str:
                    result = await func(*args, **kwargs)
                    if isinstance(result, ResourceResult):
                        return str(result.contents[0].content)
                    return str(result)
                registered[uri] = wrapper
                return func
            return deco

        mcp.resource = resource_decorator
        register_custom_resources(
            mcp, mock_gitea_client_str,
            available_scopes={"write:issue", "read:repository", "sudo"},
        )
        func_callable = registered["gitea://token/scopes"]
        assert callable(func_callable)
        result = await func_callable()
        data = json.loads(result)
        assert data["scopes"] == ["read:repository", "sudo", "write:issue"]

    @pytest.mark.asyncio
    async def test_server_info_returns_precomputed_markdown(self, mock_gitea_client_str: AsyncMock) -> None:
        """server/info returns pre-built markdown, no openapi_spec needed on read."""
        from fastmcp.resources import ResourceResult

        mcp = MagicMock(spec=FastMCP)
        registered: dict[str, object] = {}

        def resource_decorator(uri: str, **kwargs: Any) -> Callable:
            def deco(func: Callable) -> Callable:
                async def wrapper(*args: object, **kwargs: object) -> str:
                    result = await func(*args, **kwargs)
                    if isinstance(result, ResourceResult):
                        return str(result.contents[0].content)
                    return str(result)
                registered[uri] = wrapper
                return func
            return deco

        mcp.resource = resource_decorator
        expected_md = "# Server Information\n\n**Server Type**: Test\n**API Version**: 1.0\n"
        register_custom_resources(
            mcp, mock_gitea_client_str,
            server_info_md=expected_md,
        )
        func = registered["gitea://server/info"]
        assert callable(func)
        result = await func()
        assert result == expected_md

    @pytest.mark.asyncio
    async def test_list_repo_issues_with_state_param(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """Issues with state='open' passes state param to API."""

        func = captured_resources["gitea://repos/{owner}/{repo}/issues{?state,type}"]
        mock_gitea_client_str.request = AsyncMock(return_value=[])
        result = await func("owner", "repo", state="open")
        assert json.loads(result) == []
        mock_gitea_client_str.request.assert_called_once()
        _, kwargs = mock_gitea_client_str.request.call_args
        assert kwargs.get("params") == {"state": "open"}

    @pytest.mark.asyncio
    async def test_list_repo_issues_with_type_param(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """Issues with type='pulls' sends type param to API."""

        func = captured_resources["gitea://repos/{owner}/{repo}/issues{?state,type}"]
        mock_gitea_client_str.request = AsyncMock(return_value=[])
        result = await func("owner", "repo", type="pulls")
        assert json.loads(result) == []
        mock_gitea_client_str.request.assert_called_once()
        _, kwargs = mock_gitea_client_str.request.call_args
        # type is a query_param, sent to API
        assert kwargs.get("params") == {"type": "pulls"}

    @pytest.mark.asyncio
    async def test_list_repo_pulls_with_state_param(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """Pulls with state='closed' passes state param to API."""

        func = captured_resources["gitea://repos/{owner}/{repo}/pulls{?state}"]
        mock_gitea_client_str.request = AsyncMock(return_value=[])
        result = await func("owner", "repo", state="closed")
        assert json.loads(result) == []
        mock_gitea_client_str.request.assert_called_once()
        _, kwargs = mock_gitea_client_str.request.call_args
        assert kwargs.get("params") == {"state": "closed"}

    @pytest.mark.asyncio
    async def test_get_repository_dict_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Repository dict response returns raw JSON."""

        func = captured_resources["gitea://repos/{owner}/{repo}"]
        expected = {
            "full_name": "owner/repo",
            "description": "test",
            "owner": {"login": "owner"},
            "html_url": "https://example.com/owner/repo",
            "default_branch": "main",
        }
        mock_gitea_client_str.request = AsyncMock(return_value=expected)
        result = await func("owner", "repo")
        assert json.loads(result) == expected

    # ── labels resource ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_repo_labels_string_response(
        self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock
    ) -> None:
        """isinstance(data, str) returns string directly for labels."""
        func = captured_resources["gitea://repos/{owner}/{repo}/labels"]
        mock_gitea_client_str.request = AsyncMock(return_value="string labels")
        result = await func("owner", "repo")
        assert result == "string labels"

    @pytest.mark.asyncio
    async def test_list_repo_labels_dict_response(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Labels dict response returns raw JSON."""

        func = captured_resources["gitea://repos/{owner}/{repo}/labels"]
        expected = [
            {
                "id": 1,
                "name": "bug",
                "color": "ff0000",
                "description": "Bug report",
                "exclusive": False,
            },
            {
                "id": 2,
                "name": "Kind/Feature",
                "color": "00ff00",
                "description": "New feature",
                "exclusive": True,
            },
        ]
        mock_gitea_client_str.request = AsyncMock(return_value=expected)
        result = await func("owner", "repo")
        assert json.loads(result) == expected

    @pytest.mark.asyncio
    async def test_list_repo_labels_empty(self, captured_resources: dict[str, Any], mock_gitea_client_str: AsyncMock) -> None:
        """Empty labels list returns raw JSON empty array."""

        func = captured_resources["gitea://repos/{owner}/{repo}/labels"]
        mock_gitea_client_str.request = AsyncMock(return_value=[])
        result = await func("owner", "repo")
        assert json.loads(result) == []
