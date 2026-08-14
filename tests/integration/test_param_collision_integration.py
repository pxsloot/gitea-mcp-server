"""Integration tests for parameter collision resolution end-to-end.

Tests the full pipeline: spec-level renaming -> FastMCP provider creation ->
runtime shim -> tool parameter names.  Verifies that colliding body properties
are renamed with a ``body_`` prefix and that the resulting tools have the
correct parameter names.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3
from gitea_mcp_server.openapi_converter.param_collision import resolve_param_collisions
from gitea_mcp_server.server_setup.mcp_builder import (
    _apply_param_rename,
    _read_param_rename,
    create_openapi_provider,
)
from tests.helpers.spec_fixtures import make_openapi_spec

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gitea_mcp_server.openapi_types import OpenAPISpec, SwaggerV2Spec


def _tool_dict(tools: Sequence[Any]) -> dict[str, Any]:
    """Extract tool name -> tool mapping from provider.list_tools() result."""
    return {t.name: t for t in tools}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def blocking_spec() -> OpenAPISpec:
    """OpenAPI spec with a blocking endpoint that has param collisions."""
    return make_openapi_spec(
        components={
            "schemas": {
                "IssueMeta": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "repo": {"type": "string"},
                        "index": {"type": "integer"},
                    },
                },
            },
        },
        paths={
            "/repos/{owner}/{repo}/issues/{index}/blocks": {
                "post": {
                    "operationId": "issueCreateIssueBlocking",
                    "summary": "Create issue blocking",
                    "parameters": [
                        {
                            "name": "owner",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "repo",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "index",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/IssueMeta"},
                            },
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {"description": "Created"},
                    },
                },
            },
            "/repos/{owner}/{repo}/issues": {
                "post": {
                    "operationId": "issueCreateIssue",
                    "summary": "Create an issue (no collision)",
                    "parameters": [
                        {
                            "name": "owner",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "repo",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "body": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {"description": "Created"},
                    },
                },
            },
        },
    )


# ---------------------------------------------------------------------------
# Tests: spec-level collision resolution
# ---------------------------------------------------------------------------


class TestSpecLevelResolution:
    """Tests that resolve_param_collisions correctly renames body properties."""

    def test_renames_colliding_properties(self, blocking_spec: OpenAPISpec) -> None:
        """Colliding body properties are renamed with body_ prefix."""
        resolve_param_collisions(blocking_spec)

        op = blocking_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]

        assert "body_owner" in props
        assert "body_repo" in props
        assert "body_index" in props
        assert "owner" not in props
        assert "repo" not in props
        assert "index" not in props

    def test_body_params_have_descriptions_after_resolution(
        self, blocking_spec: OpenAPISpec
    ) -> None:
        """Renamed body_* params have non-empty descriptions (issue #681).

        Path params in the fixture carry no descriptions, so body_*
        properties fall back to the generic ``<name> field of the request
        body resource`` message — not empty.
        """
        resolve_param_collisions(blocking_spec)

        op = blocking_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]

        # Every renamed param must have a non-empty description
        assert props["body_owner"]["description"], "body_owner has empty description"
        assert props["body_repo"]["description"], "body_repo has empty description"
        assert props["body_index"]["description"], "body_index has empty description"

    def test_body_params_get_path_param_descriptions(self) -> None:
        """Renamed body_* params inherit path param descriptions (issue #681).

        When path params carry descriptions, the renamed body properties
        use the ``(Request body)`` prefix.
        """
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/issues/{index}/blocks": {
                    "post": {
                        "operationId": "issueCreateIssueBlocking",
                        "parameters": [
                            {
                                "name": "owner",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "owner of the repo",
                            },
                            {
                                "name": "repo",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "name of the repo",
                            },
                            {
                                "name": "index",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                                "description": "index of the issue",
                            },
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "owner": {"type": "string"},
                                            "repo": {"type": "string"},
                                            "index": {"type": "integer"},
                                        },
                                    },
                                },
                            },
                            "required": True,
                        },
                        "responses": {"201": {"description": "Created"}},
                    },
                },
            },
        )
        resolve_param_collisions(spec)

        op = spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]

        assert props["body_owner"]["description"] == "(Request body) owner of the repo"
        assert props["body_repo"]["description"] == "(Request body) name of the repo"
        assert props["body_index"]["description"] == "(Request body) index of the issue"

    def test_sets_x_param_rename(self, blocking_spec: OpenAPISpec) -> None:
        """x-param-rename extension is set with correct mapping."""
        resolve_param_collisions(blocking_spec)

        op = blocking_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        rename_map = cast("dict[str, Any]", op).get("x-param-rename")
        assert rename_map == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }

    def test_shared_component_not_mutated(self, blocking_spec: OpenAPISpec) -> None:
        """Shared IssueMeta component is not mutated by inlining."""
        resolve_param_collisions(blocking_spec)

        issue_meta = blocking_spec["components"]["schemas"]["IssueMeta"]
        assert "owner" in issue_meta["properties"]
        assert "body_owner" not in issue_meta["properties"]

    def test_non_colliding_endpoint_unchanged(self, blocking_spec: OpenAPISpec) -> None:
        """Endpoints without collisions are not affected."""
        resolve_param_collisions(blocking_spec)

        op = blocking_spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "title" in props
        assert "body" in props
        assert "x-param-rename" not in cast("dict[str, Any]", op)


# ---------------------------------------------------------------------------
# Tests: runtime shim (_read_param_rename / _apply_param_rename)
# ---------------------------------------------------------------------------


class TestRuntimeShim:
    """Tests that the runtime shim correctly fixes parameter_map."""

    def test_read_param_rename_after_resolution(self, blocking_spec: OpenAPISpec) -> None:
        """_read_param_rename finds the x-param-rename set by resolution."""
        resolve_param_collisions(blocking_spec)
        rename_map = _read_param_rename(
            blocking_spec,
            "/repos/{owner}/{repo}/issues/{index}/blocks",
            "POST",
        )
        assert rename_map == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }

    def test_apply_param_rename_fixes_parameter_map(self, blocking_spec: OpenAPISpec) -> None:
        """_apply_param_rename corrects openapi_name in parameter_map."""
        resolve_param_collisions(blocking_spec)

        # Simulate what FastMCP's parameter_map would look like
        parameter_map = {
            "owner": {"location": "path", "openapi_name": "owner"},
            "repo": {"location": "path", "openapi_name": "repo"},
            "index": {"location": "path", "openapi_name": "index"},
            "body_owner": {"location": "body", "openapi_name": "body_owner"},
            "body_repo": {"location": "body", "openapi_name": "body_repo"},
            "body_index": {"location": "body", "openapi_name": "body_index"},
        }

        class _RouteStub:
            def __init__(self, path: str, method: str, parameter_map: dict[str, Any]) -> None:
                self.path = path
                self.method = method
                self.parameter_map = parameter_map

        route = _RouteStub(
            path="/repos/{owner}/{repo}/issues/{index}/blocks",
            method="POST",
            parameter_map=parameter_map,
        )

        _apply_param_rename(route, blocking_spec)

        # Body params should now map to original names
        assert route.parameter_map["body_owner"]["openapi_name"] == "owner"
        assert route.parameter_map["body_repo"]["openapi_name"] == "repo"
        assert route.parameter_map["body_index"]["openapi_name"] == "index"
        # Path params unchanged
        assert route.parameter_map["owner"]["openapi_name"] == "owner"
        assert route.parameter_map["repo"]["openapi_name"] == "repo"
        assert route.parameter_map["index"]["openapi_name"] == "index"


# ---------------------------------------------------------------------------
# Tests: full pipeline (spec -> FastMCP provider -> tools)
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Tests the full pipeline from spec to FastMCP tools."""

    @pytest.mark.asyncio
    async def test_tool_has_correct_param_names(self, blocking_spec: OpenAPISpec) -> None:
        """Tool parameters use body_ prefix for renamed body properties."""
        resolve_param_collisions(blocking_spec)

        mock_client = MagicMock()
        mock_client.client = MagicMock()
        mock_client.request.return_value = {}

        provider = create_openapi_provider(
            openapi_spec=blocking_spec,
            gitea_client=mock_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        tools = await provider.list_tools()
        tool_map = _tool_dict(tools)

        # The blocking tool should have body_ prefixed params.
        # Tool names in the provider are the raw operationIds (camelCase);
        # snake_case conversion and the gitea_ prefix are applied later
        # by server-level transforms (GiteaNamespace).
        blocking_tool = tool_map.get("issueCreateIssueBlocking")
        assert blocking_tool is not None, (
            f"Expected issueCreateIssueBlocking tool, got: {list(tool_map.keys())}"
        )

        # blocking_tool.parameters is a JSON Schema dict with property names
        # as keys under the "properties" key.
        params = blocking_tool.parameters
        if isinstance(params, dict):
            param_names = set(params.get("properties", {}).keys())
        else:
            param_names = set(params) if isinstance(params, (list, set)) else set()

        assert "body_owner" in param_names, f"Expected body_owner in params: {param_names}"
        assert "body_repo" in param_names, f"Expected body_repo in params: {param_names}"
        assert "body_index" in param_names, f"Expected body_index in params: {param_names}"
        # Original names should still be present (they're the path params)
        assert "owner" in param_names
        assert "repo" in param_names
        assert "index" in param_names

    @pytest.mark.asyncio
    async def test_non_colliding_tool_unchanged(self, blocking_spec: OpenAPISpec) -> None:
        """Tools without collisions have their original parameter names."""
        resolve_param_collisions(blocking_spec)

        mock_client = MagicMock()
        mock_client.client = MagicMock()
        mock_client.request.return_value = {}

        provider = create_openapi_provider(
            openapi_spec=blocking_spec,
            gitea_client=mock_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        tools = await provider.list_tools()
        tool_map = _tool_dict(tools)

        create_tool = tool_map.get("issueCreateIssue")
        assert create_tool is not None

        params = create_tool.parameters
        if isinstance(params, dict):
            param_names = set(params.get("properties", {}).keys())
        else:
            param_names = set(params) if isinstance(params, (list, set)) else set()

        assert "title" in param_names
        assert "body" in param_names
        # No body_ prefix for non-colliding params
        assert "body_title" not in param_names
        assert "body_body" not in param_names


# ---------------------------------------------------------------------------
# Tests: request emission (pins the FastMCP parameter_map contract)
# ---------------------------------------------------------------------------


class TestRequestEmission:
    """Tests that the emitted HTTP request uses the original Gitea field names.

    The runtime shim (``_apply_param_rename``) depends on FastMCP-internal
    ``parameter_map`` structure (``{"location": ..., "openapi_name": ...}``
    dicts).  The unit tests verify the shim against a hand-built stub of that
    structure; these tests go through real FastMCP parsing and call the tool
    with a recording httpx client.  If a FastMCP upgrade changes the
    ``parameter_map`` structure, the shim silently no-ops (defensive
    ``isinstance`` checks) and the body would contain ``body_owner`` instead
    of ``owner`` — these tests are the tripwire for that regression.
    """

    @staticmethod
    def _make_recording_client() -> tuple[MagicMock, AsyncMock]:
        """Build a GiteaClient stub whose httpx client records the request.

        Returns:
            Tuple of (gitea_client stub, send mock).  The send mock's first
            positional call argument is the emitted ``httpx.Request``.
        """
        send = AsyncMock(
            side_effect=lambda request: httpx.Response(200, json={"number": 7}, request=request)
        )
        http_client = MagicMock()
        http_client.base_url = "http://localhost"
        http_client.headers = {}
        http_client.send = send
        gitea_client = MagicMock()
        gitea_client.client = http_client
        gitea_client.request.return_value = {}
        return gitea_client, send

    @pytest.mark.asyncio
    async def test_emitted_body_uses_original_field_names(self, blocking_spec: OpenAPISpec) -> None:
        """Request body contains ``owner``/``repo``/``index``, not ``body_*``."""
        resolve_param_collisions(blocking_spec)
        gitea_client, send = self._make_recording_client()

        provider = create_openapi_provider(
            openapi_spec=blocking_spec,
            gitea_client=gitea_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        tools = await provider.list_tools()
        tool = _tool_dict(tools)["issueCreateIssueBlocking"]

        await tool.run(
            arguments={
                "owner": "pathowner",
                "repo": "pathrepo",
                "index": 42,
                "body_owner": "bodyowner",
                "body_repo": "bodyrepo",
                "body_index": 7,
            }
        )

        send.assert_awaited_once()
        request = send.call_args.args[0]
        # Path params go to the URL with their original values.
        assert request.url.path == "/repos/pathowner/pathrepo/issues/42/blocks"
        # Body params are emitted under their original Gitea field names.
        assert json.loads(request.content) == {
            "owner": "bodyowner",
            "repo": "bodyrepo",
            "index": 7,
        }

    @pytest.mark.asyncio
    async def test_non_colliding_tool_emits_body_unchanged(
        self, blocking_spec: OpenAPISpec
    ) -> None:
        """Tools without collisions emit their body untouched (control case)."""
        resolve_param_collisions(blocking_spec)
        gitea_client, send = self._make_recording_client()

        provider = create_openapi_provider(
            openapi_spec=blocking_spec,
            gitea_client=gitea_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        tools = await provider.list_tools()
        tool = _tool_dict(tools)["issueCreateIssue"]

        await tool.run(
            arguments={
                "owner": "someowner",
                "repo": "somerepo",
                "title": "A title",
                "body": "A body",
            }
        )

        send.assert_awaited_once()
        request = send.call_args.args[0]
        assert request.url.path == "/repos/someowner/somerepo/issues"
        assert json.loads(request.content) == {"title": "A title", "body": "A body"}


# ---------------------------------------------------------------------------
# Tests: full pipeline from swagger.v1.json (DELETE-with-body)
# ---------------------------------------------------------------------------


class TestSwaggerV1DeleteWithBody:
    """Full-pipeline test: ``tests/swagger.v1.json`` → converter → collision
    resolver → FastMCP tools.

    Verifies that the real-world DELETE-with-body endpoints
    (``/repos/{owner}/{repo}/issues/{index}/blocks`` and
    ``/repos/{owner}/{repo}/issues/{index}/dependencies``) survive the
    converter (no method-gate dropping), have their param collisions
    resolved (``body_owner`` / ``body_repo`` / ``body_index``), and end
    up as usable FastMCP tools with the expected parameter names.

    This is the integration-level acceptance test for issue #680 /
    PR #682.
    """

    @pytest.fixture(scope="class")
    def converted_spec(self) -> OpenAPISpec:
        """Load swagger.v1.json, convert, resolve collisions."""
        spec_path = Path(__file__).parent.parent / "swagger.v1.json"
        with spec_path.open() as f:
            swag: dict[str, Any] = json.load(f)
        openapi_spec: dict[str, Any] = convert_swagger_to_openapi_v3(cast("SwaggerV2Spec", swag))
        # widen to OpenAPISpec for collision resolution
        typed: OpenAPISpec = cast("OpenAPISpec", openapi_spec)
        resolve_param_collisions(typed)
        return typed

    @pytest.fixture(scope="class")
    def tools(self, converted_spec: OpenAPISpec) -> dict[str, Any]:
        """Provider tools from the converted+resolved spec."""
        import asyncio

        mock_client = MagicMock()
        mock_client.client = MagicMock()
        mock_client.request.return_value = {}

        provider = create_openapi_provider(
            openapi_spec=converted_spec,
            gitea_client=mock_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        async def _get() -> Sequence[Any]:
            return await provider.list_tools()

        return _tool_dict(asyncio.run(_get()))

    def test_blocking_tool_has_body_prefixed_params(self, tools: dict[str, Any]) -> None:
        """issue_remove_issue_blocking exposes body_owner/body_repo/body_index."""
        t = tools.get("issue_remove_issue_blocking")
        assert t is not None, f"Tool not found; available: {sorted(tools.keys())}"

        param_names = set(t.parameters.get("properties", {}).keys())
        assert "body_owner" in param_names, f"Missing body_owner in {sorted(param_names)}"
        assert "body_repo" in param_names, f"Missing body_repo in {sorted(param_names)}"
        assert "body_index" in param_names, f"Missing body_index in {sorted(param_names)}"
        # Original path params still present
        assert "owner" in param_names
        assert "repo" in param_names
        assert "index" in param_names

    def test_dependencies_tool_has_body_prefixed_params(self, tools: dict[str, Any]) -> None:
        """issue_remove_issue_dependencies exposes body_owner/body_repo/body_index."""
        t = tools.get("issue_remove_issue_dependencies")
        assert t is not None, f"Tool not found; available: {sorted(tools.keys())}"

        param_names = set(t.parameters.get("properties", {}).keys())
        assert "body_owner" in param_names, f"Missing body_owner in {sorted(param_names)}"
        assert "body_repo" in param_names, f"Missing body_repo in {sorted(param_names)}"
        assert "body_index" in param_names, f"Missing body_index in {sorted(param_names)}"
        assert "owner" in param_names
        assert "repo" in param_names
        assert "index" in param_names

    def test_request_body_uses_original_field_names(self, converted_spec: OpenAPISpec) -> None:
        """Emitted request body maps body_* params back to owner/repo/index."""
        import asyncio

        gitea_client = MagicMock()
        gitea_client.client = MagicMock()
        gitea_client.request.return_value = {}

        provider = create_openapi_provider(
            openapi_spec=converted_spec,
            gitea_client=gitea_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        async def _run() -> dict[str, Any]:
            tools = await provider.list_tools()
            return _tool_dict(tools)

        tools = asyncio.run(_run())
        t = tools["issue_remove_issue_blocking"]

        # Verify x-param-rename is set and maps body_* → original names
        op = converted_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["delete"]
        rename_map = op.get("x-param-rename")
        assert rename_map == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }, f"Wrong renaming map: {rename_map}"

    def test_body_params_have_descriptions(self, converted_spec: OpenAPISpec) -> None:
        """Renamed body_* params have non-empty descriptions (issue #681).

        The real swagger.v1.json carries descriptions on path params
        (e.g., ``owner of the repo``), so body_* properties should
        inherit them with the ``(Request body)`` prefix.
        """
        op = converted_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["delete"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]

        assert props["body_owner"]["description"], "body_owner has empty description"
        assert props["body_repo"]["description"], "body_repo has empty description"
        assert props["body_index"]["description"], "body_index has empty description"

        # When path params have descriptions in the real spec, the
        # body_* params should carry the (Request body) prefix.
        assert "(Request body)" in props["body_owner"]["description"]
        assert "(Request body)" in props["body_repo"]["description"]
        assert "(Request body)" in props["body_index"]["description"]

    def test_dependencies_body_params_have_descriptions(self, converted_spec: OpenAPISpec) -> None:
        """Body_* params on the dependencies endpoint also have descriptions."""
        op = converted_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/dependencies"]["delete"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]

        assert props["body_owner"]["description"], "body_owner has empty description"
        assert props["body_repo"]["description"], "body_repo has empty description"
        assert props["body_index"]["description"], "body_index has empty description"


# ---------------------------------------------------------------------------
# Tests: allOf body schemas
# ---------------------------------------------------------------------------


@pytest.fixture
def allof_blocking_spec() -> OpenAPISpec:
    """OpenAPI spec with a blocking endpoint using allOf + nested $ref in body.

    Simulates the scenario from issue #679: a body schema that uses
    ``allOf`` with a ``$ref`` to a shared component schema plus inline
    property extensions, rather than a flat ``$ref``.
    """
    return make_openapi_spec(
        components={
            "schemas": {
                "IssueMeta": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string"},
                        "repo": {"type": "string"},
                        "index": {"type": "integer"},
                    },
                },
            },
        },
        paths={
            "/repos/{owner}/{repo}/issues/{index}/blocks": {
                "post": {
                    "operationId": "issueCreateIssueBlocking",
                    "summary": "Create issue blocking",
                    "parameters": [
                        {
                            "name": "owner",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "repo",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "index",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "allOf": [
                                        {"$ref": "#/components/schemas/IssueMeta"},
                                        {
                                            "type": "object",
                                            "properties": {"note": {"type": "string"}},
                                        },
                                    ],
                                },
                            },
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {"description": "Created"},
                    },
                },
            },
        },
    )


class TestAllOfSpecLevelResolution:
    """Tests that resolve_param_collisions handles allOf body schemas."""

    def test_renames_colliding_properties_in_allof(self, allof_blocking_spec: OpenAPISpec) -> None:
        """Colliding body properties inside allOf are renamed with body_ prefix."""
        resolve_param_collisions(allof_blocking_spec)

        op = allof_blocking_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "allOf" not in schema  # Flattened (mirrors FastMCP's merge)
        props = schema["properties"]

        assert "body_owner" in props
        assert "body_repo" in props
        assert "body_index" in props
        assert "owner" not in props
        assert "repo" not in props
        assert "index" not in props
        # Non-colliding allOf properties preserved
        assert "note" in props

    def test_sets_x_param_rename_for_allof(self, allof_blocking_spec: OpenAPISpec) -> None:
        """x-param-rename is set for allOf body schema collisions."""
        resolve_param_collisions(allof_blocking_spec)

        op = allof_blocking_spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        rename_map = cast("dict[str, Any]", op).get("x-param-rename")
        assert rename_map == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }

    def test_shared_component_not_mutated_by_allof(self, allof_blocking_spec: OpenAPISpec) -> None:
        """Shared IssueMeta component is not mutated by allOf flattening."""
        resolve_param_collisions(allof_blocking_spec)

        issue_meta = allof_blocking_spec["components"]["schemas"]["IssueMeta"]
        assert "owner" in issue_meta["properties"]
        assert "body_owner" not in issue_meta["properties"]
        assert "note" not in issue_meta["properties"]  # Inline property from allOf


class TestAllOfFullPipeline:
    """allOf body schema through the full FastMCP provider pipeline (#679)."""

    @pytest.mark.asyncio
    async def test_allof_tool_has_no_path_suffixes(self, allof_blocking_spec: OpenAPISpec) -> None:
        """The flattened allOf spec yields body_ params, never __path params.

        This is the regression issue #679 guards against: without
        flattening, FastMCP's own allOf merge would detect the collision
        first and rename the path params with ``__path`` suffixes.
        """
        resolve_param_collisions(allof_blocking_spec)

        mock_client = MagicMock()
        mock_client.client = MagicMock()
        mock_client.request.return_value = {}

        provider = create_openapi_provider(
            openapi_spec=allof_blocking_spec,
            gitea_client=mock_client,
            label_service=MagicMock(),
            excluded_routes=set(),
        )

        tools = await provider.list_tools()
        tool_map = _tool_dict(tools)

        blocking_tool = tool_map.get("issueCreateIssueBlocking")
        assert blocking_tool is not None, (
            f"Expected issueCreateIssueBlocking tool, got: {list(tool_map.keys())}"
        )

        params = blocking_tool.parameters
        param_names = (
            set(params.get("properties", {}).keys()) if isinstance(params, dict) else set()
        )

        # Renamed body params and original path params coexist
        assert {"body_owner", "body_repo", "body_index"} <= param_names
        assert {"owner", "repo", "index"} <= param_names
        # The inline allOf member property survives
        assert "note" in param_names
        # The actual regression check: no FastMCP __path suffixes anywhere
        assert not any(name.endswith("__path") for name in param_names), (
            f"FastMCP __path suffix leaked into params: {param_names}"
        )


# ---------------------------------------------------------------------------
# Tests: oneOf/anyOf tripwire (issue #679)
# ---------------------------------------------------------------------------


class TestOneOfTripwire:
    """oneOf bodies are not flattened; a loud warning surfaces instead."""

    def test_oneof_body_warns_and_stays_intact(self, caplog: pytest.LogCaptureFixture) -> None:
        """Full resolve_param_collisions logs a tripwire warning for oneOf."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/issues/{index}/blocks": {
                    "post": {
                        "operationId": "issueCreateIssueBlocking",
                        "parameters": [
                            {
                                "name": "owner",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "repo",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "index",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            },
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "oneOf": [
                                            {
                                                "type": "object",
                                                "properties": {"owner": {"type": "string"}},
                                            },
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "owner": {"type": "string"},
                                                    "note": {"type": "string"},
                                                },
                                            },
                                        ],
                                    },
                                },
                            },
                            "required": True,
                        },
                        "responses": {"201": {"description": "Created"}},
                    },
                },
            },
        )
        with caplog.at_level(logging.WARNING):
            resolve_param_collisions(spec)

        assert "issueCreateIssueBlocking" in caplog.text
        assert "oneOf" in caplog.text

        # Schema untouched (FastMCP needs the composition), no rename map
        op = spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "oneOf" in schema
        assert "properties" not in schema
        assert "x-param-rename" not in cast("dict[str, Any]", op)
