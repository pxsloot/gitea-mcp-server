"""Unit tests for parameter collision resolution.

Tests the spec-level renaming of body properties that collide with path
parameter names, and the runtime shim that fixes the ``parameter_map``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from gitea_mcp_server.openapi_converter.param_collision import (
    _collect_path_item_params,
    _collect_path_param_names,
    _get_body_schema,
    _merge_path_params,
    _rename_colliding_body_properties,
    _resolve_operation_collisions,
    resolve_param_collisions,
)
from gitea_mcp_server.server_setup.mcp_builder import (
    _apply_param_rename,
    _read_param_rename,
)
from tests.helpers.spec_fixtures import make_openapi_spec

# ===========================================================================
# Helpers
# ===========================================================================


def _make_operation(
    path_params: list[str] | None = None,
    body_props: dict[str, Any] | None = None,
    body_ref: str | None = None,
    method: str = "post",
) -> dict[str, Any]:
    """Build a minimal OpenAPI operation dict for testing.

    Args:
        path_params: List of path parameter names.
        body_props: Dict of body property name -> schema.
        body_ref: ``$ref`` string for the body schema (overrides body_props).
        method: HTTP method (default ``"post"``).

    Returns:
        An OpenAPI operation dict.
    """
    operation: dict[str, Any] = {
        "operationId": f"test_{method}",
    }

    # Add path parameters
    if path_params:
        operation["parameters"] = [
            {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
            for name in path_params
        ]

    # Add request body
    if body_ref:
        operation["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {"$ref": body_ref},
                },
            },
            "required": True,
        }
    elif body_props:
        required = [k for k, v in body_props.items() if v.get("_required")]
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {k: v for k, v in body_props.items() if not k.startswith("_")},
        }
        if required:
            schema["required"] = required
        operation["requestBody"] = {
            "content": {
                "application/json": {"schema": schema},
            },
            "required": True,
        }

    return operation


def _make_route_stub(
    path: str = "/test/{owner}/{repo}",
    method: str = "POST",
    parameter_map: dict[str, Any] | None = None,
) -> Any:
    """Build a minimal route stub with a ``parameter_map`` attribute.

    Args:
        path: The route path template.
        method: The HTTP method.
        parameter_map: The parameter map to attach.

    Returns:
        A simple object with ``path``, ``method``, and ``parameter_map`` attrs.
    """

    class _RouteStub:
        def __init__(self, path: str, method: str, parameter_map: dict[str, Any]) -> None:
            self.path = path
            self.method = method
            self.parameter_map = parameter_map

    return _RouteStub(
        path=path,
        method=method,
        parameter_map=parameter_map or {},
    )


# ===========================================================================
# Tests: _collect_path_param_names
# ===========================================================================


class TestCollectPathParamNames:
    """Tests for collecting path parameter names from an operation."""

    def test_empty_params(self) -> None:
        """No parameters returns empty set."""
        op = _make_operation()
        assert _collect_path_param_names(op) == set()

    def test_path_params_collected(self) -> None:
        """Path parameter names are collected."""
        op = _make_operation(path_params=["owner", "repo", "index"])
        assert _collect_path_param_names(op) == {"owner", "repo", "index"}

    def test_non_path_params_ignored(self) -> None:
        """Query and header parameters are ignored."""
        op = _make_operation(path_params=["owner"])
        op["parameters"].append({"name": "q", "in": "query", "schema": {"type": "string"}})
        op["parameters"].append({"name": "X-Custom", "in": "header", "schema": {"type": "string"}})
        assert _collect_path_param_names(op) == {"owner"}

    def test_missing_params_key(self) -> None:
        """Missing ``parameters`` key returns empty set."""
        op = _make_operation()
        op.pop("parameters", None)
        assert _collect_path_param_names(op) == set()

    def test_non_list_params(self) -> None:
        """Non-list ``parameters`` returns empty set."""
        op = _make_operation()
        op["parameters"] = "not_a_list"
        assert _collect_path_param_names(op) == set()


# ===========================================================================
# Tests: _get_body_schema
# ===========================================================================


class TestGetBodySchema:
    """Tests for extracting the request body schema."""

    def test_no_request_body(self) -> None:
        """No request body returns None."""
        op = _make_operation()
        spec = make_openapi_spec()
        assert _get_body_schema(op, spec) is None

    def test_inline_body_schema(self) -> None:
        """Inline body schema is returned directly."""
        op = _make_operation(body_props={"owner": {"type": "string"}})
        spec = make_openapi_spec()
        schema = _get_body_schema(op, spec)
        assert schema is not None
        assert "owner" in schema.get("properties", {})

    def test_ref_body_schema(self) -> None:
        """``$ref`` body schema is resolved and inlined."""
        spec = make_openapi_spec(
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
        )
        op = _make_operation(body_ref="#/components/schemas/IssueMeta")
        schema = _get_body_schema(op, spec)
        assert schema is not None
        props = schema.get("properties", {})
        assert "owner" in props
        assert "repo" in props
        assert "index" in props
        # Verify the $ref was inlined (replaced with the resolved schema)
        json_content = op["requestBody"]["content"]["application/json"]
        assert "$ref" not in json_content["schema"]

    def test_ref_not_found(self) -> None:
        """Unresolvable ``$ref`` returns the unresolved schema."""
        spec = make_openapi_spec()
        op = _make_operation(body_ref="#/components/schemas/NonExistent")
        schema = _get_body_schema(op, spec)
        # When the $ref can't be resolved, the function returns the
        # unresolved schema (the body still exists, just can't be resolved).
        assert schema is not None
        assert "$ref" in schema

    def test_missing_content(self) -> None:
        """Missing ``content`` key returns None."""
        op = _make_operation()
        op["requestBody"] = {"required": True}
        spec = make_openapi_spec()
        assert _get_body_schema(op, spec) is None

    def test_content_not_dict(self) -> None:
        """Non-dict ``content`` returns None (line 108)."""
        op = _make_operation()
        op["requestBody"] = {"content": "not_a_dict"}
        spec = make_openapi_spec()
        assert _get_body_schema(op, spec) is None

    def test_json_content_not_dict(self) -> None:
        """Non-dict ``application/json`` content returns None (line 112)."""
        op = _make_operation()
        op["requestBody"] = {"content": {"application/json": "not_a_dict"}}
        spec = make_openapi_spec()
        assert _get_body_schema(op, spec) is None


# ===========================================================================
# Tests: _rename_colliding_body_properties
# ===========================================================================


class TestRenameCollidingBodyProperties:
    """Tests for renaming colliding body properties."""

    def test_renames_colliding_properties(self) -> None:
        """Colliding properties are renamed with ``body_`` prefix."""
        schema = {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["owner", "title"],
        }
        rename_map = _rename_colliding_body_properties(schema, {"owner", "repo"})

        assert rename_map == {"body_owner": "owner", "body_repo": "repo"}
        props = schema["properties"]
        assert "body_owner" in props
        assert "body_repo" in props
        assert "owner" not in props
        assert "repo" not in props
        assert "title" in props  # Unchanged
        # Non-colliding required items keep their position; renamed items
        # are appended at the end (remove + append preserves existing order).
        assert schema["required"] == ["title", "body_owner"]

    def test_no_collisions(self) -> None:
        """No collisions returns empty map and no changes."""
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
        }
        rename_map = _rename_colliding_body_properties(schema, set())
        assert rename_map == {}
        assert "title" in schema["properties"]
        assert "body" in schema["properties"]

    def test_empty_properties(self) -> None:
        """Empty properties returns empty map."""
        schema = {"type": "object", "properties": {}}
        rename_map = _rename_colliding_body_properties(schema, {"owner"})
        assert rename_map == {}

    def test_non_dict_properties(self) -> None:
        """Non-dict properties returns empty map."""
        schema = {"type": "object", "properties": "not_a_dict"}
        rename_map = _rename_colliding_body_properties(schema, {"owner"})
        assert rename_map == {}

    def test_deterministic_ordering(self) -> None:
        """Renaming is deterministic (sorted)."""
        schema = {
            "type": "object",
            "properties": {
                "z_param": {"type": "string"},
                "a_param": {"type": "string"},
                "m_param": {"type": "string"},
            },
        }
        rename_map = _rename_colliding_body_properties(schema, {"z_param", "a_param", "m_param"})
        # Should be sorted: a_param, m_param, z_param
        keys = list(rename_map.keys())
        assert keys == ["body_a_param", "body_m_param", "body_z_param"]

    def test_required_not_list(self) -> None:
        """Non-list ``required`` is handled gracefully (line 156)."""
        schema = {
            "type": "object",
            "properties": {"owner": {"type": "string"}},
            "required": "not_a_list",
        }
        rename_map = _rename_colliding_body_properties(schema, {"owner"})
        assert rename_map == {"body_owner": "owner"}
        assert "body_owner" in schema["properties"]
        # Non-list required is replaced with empty list
        assert schema["required"] == []


# ===========================================================================
# Tests: _resolve_operation_collisions
# ===========================================================================


class TestResolveOperationCollisions:
    """Tests for resolving collisions on a single operation."""

    def test_empty_path_params_returns_none(self) -> None:
        """Empty path params returns None."""
        op = _make_operation(body_props={"owner": {"type": "string"}})
        spec = make_openapi_spec()
        assert _resolve_operation_collisions(op, set(), spec) is None

    def test_no_body_schema_returns_none(self) -> None:
        """No body schema returns None."""
        op = _make_operation()
        spec = make_openapi_spec()
        assert _resolve_operation_collisions(op, {"owner"}, spec) is None

    def test_body_props_not_dict_returns_none(self) -> None:
        """Non-dict body properties returns None (line 204)."""
        op = _make_operation()
        op["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": "not_a_dict"},
                },
            },
        }
        spec = make_openapi_spec()
        assert _resolve_operation_collisions(op, {"owner"}, spec) is None

    def test_no_collision_returns_none(self) -> None:
        """No collision between path params and body props returns None."""
        op = _make_operation(
            path_params=["owner", "repo"],
            body_props={"title": {"type": "string"}, "body": {"type": "string"}},
        )
        spec = make_openapi_spec()
        assert _resolve_operation_collisions(op, {"owner", "repo"}, spec) is None

    def test_resolves_collision(self) -> None:
        """Collision is resolved and rename map is returned."""
        op = _make_operation(
            path_params=["owner", "repo"],
            body_props={"owner": {"type": "string"}, "repo": {"type": "string"}},
        )
        spec = make_openapi_spec()
        result = _resolve_operation_collisions(op, {"owner", "repo"}, spec)
        assert result == {"body_owner": "owner", "body_repo": "repo"}
        assert op["x-param-rename"] == result


# ===========================================================================
# Tests: _collect_path_item_params
# ===========================================================================


class TestCollectPathItemParams:
    """Tests for collecting path-item-level parameters."""

    def test_empty_params(self) -> None:
        """No parameters returns empty list."""
        assert _collect_path_item_params({}) == []

    def test_collects_dict_items(self) -> None:
        """Dict items are collected."""
        path_item = {
            "parameters": [
                {"name": "owner", "in": "path"},
                {"name": "repo", "in": "path"},
            ],
        }
        result = _collect_path_item_params(path_item)
        assert len(result) == 2

    def test_skips_non_dict_items(self) -> None:
        """Non-dict items are skipped."""
        path_item = {
            "parameters": [
                {"name": "owner", "in": "path"},
                "not_a_dict",
                None,
                42,
            ],
        }
        result = _collect_path_item_params(path_item)
        assert len(result) == 1
        assert result[0]["name"] == "owner"

    def test_non_list_params(self) -> None:
        """Non-list parameters returns empty list."""
        path_item = {"parameters": "not_a_list"}
        assert _collect_path_item_params(path_item) == []


# ===========================================================================
# Tests: _merge_path_params
# ===========================================================================


class TestMergePathParams:
    """Tests for merging operation-level and path-level path params."""

    def test_merges_both_sources(self) -> None:
        """Params from both sources are merged."""
        result = _merge_path_params(
            {"owner", "repo"},
            [{"name": "index", "in": "path"}],
        )
        assert result == {"owner", "repo", "index"}

    def test_skips_non_path_params(self) -> None:
        """Non-path params are skipped (line 251)."""
        result = _merge_path_params(
            {"owner"},
            [
                {"name": "q", "in": "query"},
                {"name": "X-Custom", "in": "header"},
            ],
        )
        assert result == {"owner"}

    def test_skips_empty_names(self) -> None:
        """Empty names are skipped (lines 252-254)."""
        result = _merge_path_params(
            set(),
            [
                {"name": "", "in": "path"},
                {"name": "owner", "in": "path"},
            ],
        )
        assert result == {"owner"}


# ===========================================================================
# Tests: resolve_param_collisions (full spec-level)
# ===========================================================================


class TestResolveParamCollisions:
    """Tests for the full spec-level collision resolution."""

    def test_no_collisions_no_changes(self) -> None:
        """Spec with no collisions is unchanged."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/issues": {
                    "post": _make_operation(
                        path_params=["owner", "repo"],
                        body_props={"title": {"type": "string"}, "body": {"type": "string"}},
                    ),
                },
            },
        )
        original = deepcopy(cast("dict[str, Any]", spec))
        resolve_param_collisions(spec)
        assert cast("dict[str, Any]", spec) == original

    def test_resolves_collisions(self) -> None:
        """Colliding body properties are renamed."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/issues/{index}/blocks": {
                    "post": _make_operation(
                        path_params=["owner", "repo", "index"],
                        body_props={
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                    ),
                },
            },
        )
        resolve_param_collisions(spec)

        op = spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "body_owner" in props
        assert "body_repo" in props
        assert "body_index" in props
        assert "owner" not in props
        assert "repo" not in props
        assert "index" not in props

        # Verify x-param-rename is set
        op_dict = cast("dict[str, Any]", op)
        assert op_dict["x-param-rename"] == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }

    def test_resolves_collisions_with_ref(self) -> None:
        """Collisions with ``$ref`` body schemas are resolved."""
        spec = make_openapi_spec(
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
                    "post": _make_operation(
                        path_params=["owner", "repo", "index"],
                        body_ref="#/components/schemas/IssueMeta",
                    ),
                },
            },
        )
        resolve_param_collisions(spec)

        op = spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "body_owner" in props
        assert "body_repo" in props
        assert "body_index" in props
        assert "owner" not in props

        # Verify the shared component is NOT mutated
        issue_meta = spec["components"]["schemas"]["IssueMeta"]
        assert "owner" in issue_meta["properties"]
        assert "body_owner" not in issue_meta["properties"]

    def test_get_operations_ignored(self) -> None:
        """GET operations are not processed (no request body)."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "get": _make_operation(
                        path_params=["owner", "repo"],
                        method="get",
                    ),
                },
            },
        )
        original = deepcopy(cast("dict[str, Any]", spec))
        resolve_param_collisions(spec)
        assert cast("dict[str, Any]", spec) == original

    def test_multiple_affected_operations(self) -> None:
        """Multiple operations with collisions are all resolved."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/issues/{index}/blocks": {
                    "post": _make_operation(
                        path_params=["owner", "repo", "index"],
                        body_props={
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                        method="post",
                    ),
                },
                "/repos/{owner}/{repo}/issues/{index}/dependencies": {
                    "post": _make_operation(
                        path_params=["owner", "repo", "index"],
                        body_props={
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                        method="post",
                    ),
                },
            },
        )
        resolve_param_collisions(spec)

        for path_key in [
            "/repos/{owner}/{repo}/issues/{index}/blocks",
            "/repos/{owner}/{repo}/issues/{index}/dependencies",
        ]:
            op = spec["paths"][path_key]["post"]
            props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
            assert "body_owner" in props
            assert "body_repo" in props
            assert "body_index" in props
            assert cast("dict[str, Any]", op)["x-param-rename"] is not None

    def test_no_body_no_change(self) -> None:
        """Operations without request body are unchanged."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "post": {
                        "operationId": "test_post",
                        "parameters": [
                            {"name": "owner", "in": "path", "schema": {"type": "string"}},
                        ],
                    },
                },
            },
        )
        original = deepcopy(cast("dict[str, Any]", spec))
        resolve_param_collisions(spec)
        assert cast("dict[str, Any]", spec) == original

    def test_empty_paths(self) -> None:
        """Empty paths dict is handled gracefully."""
        spec = make_openapi_spec(paths={})
        resolve_param_collisions(spec)  # Should not raise

    def test_non_dict_path_item(self) -> None:
        """Non-dict path item is skipped (line 284)."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": "not_a_dict",
            },
        )
        resolve_param_collisions(spec)  # Should not raise


# ===========================================================================
# Tests: _read_param_rename and _apply_param_rename (runtime shim)
# ===========================================================================


class TestReadParamRename:
    """Tests for reading the ``x-param-rename`` from the spec."""

    def test_reads_rename_map(self) -> None:
        """``x-param-rename`` is read correctly."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner"},
                    },
                },
            },
        )
        result = _read_param_rename(spec, "/test/{owner}", "POST")
        assert result == {"body_owner": "owner"}

    def test_no_rename_map(self) -> None:
        """No ``x-param-rename`` returns None."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {"operationId": "test"},
                },
            },
        )
        assert _read_param_rename(spec, "/test/{owner}", "POST") is None

    def test_wrong_method(self) -> None:
        """Wrong method returns None."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner"},
                    },
                },
            },
        )
        assert _read_param_rename(spec, "/test/{owner}", "GET") is None

    def test_nonexistent_path(self) -> None:
        """Nonexistent path returns None."""
        spec = make_openapi_spec()
        assert _read_param_rename(spec, "/nonexistent", "POST") is None


class TestApplyParamRename:
    """Tests for applying the param rename to ``parameter_map``."""

    def test_fixes_parameter_map(self) -> None:
        """``parameter_map`` is fixed for renamed body properties."""
        parameter_map = {
            "owner": {"location": "path", "openapi_name": "owner"},
            "repo": {"location": "path", "openapi_name": "repo"},
            "body_owner": {"location": "body", "openapi_name": "body_owner"},
            "body_repo": {"location": "body", "openapi_name": "body_repo"},
        }
        spec = make_openapi_spec(
            paths={
                "/test/{owner}/{repo}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner", "body_repo": "repo"},
                    },
                },
            },
        )
        route = _make_route_stub(
            path="/test/{owner}/{repo}",
            method="POST",
            parameter_map=parameter_map,
        )
        _apply_param_rename(route, spec)

        assert route.parameter_map["body_owner"]["openapi_name"] == "owner"
        assert route.parameter_map["body_repo"]["openapi_name"] == "repo"
        # Path params unchanged
        assert route.parameter_map["owner"]["openapi_name"] == "owner"
        assert route.parameter_map["repo"]["openapi_name"] == "repo"

    def test_no_rename_map_no_change(self) -> None:
        """No rename map leaves parameter_map unchanged."""
        parameter_map = {
            "owner": {"location": "path", "openapi_name": "owner"},
        }
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {"operationId": "test"},
                },
            },
        )
        route = _make_route_stub(
            path="/test/{owner}",
            method="POST",
            parameter_map=parameter_map,
        )
        original = deepcopy(parameter_map)
        _apply_param_rename(route, spec)
        assert route.parameter_map == original

    def test_no_parameter_map_no_error(self) -> None:
        """Missing parameter_map is handled gracefully."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner"},
                    },
                },
            },
        )
        route = _make_route_stub(
            path="/test/{owner}",
            method="POST",
        )
        object.__setattr__(route, "parameter_map", None)
        _apply_param_rename(route, spec)  # Should not raise

    def test_non_body_params_not_affected(self) -> None:
        """Only body-location params are affected by the rename."""
        parameter_map = {
            "owner": {"location": "path", "openapi_name": "owner"},
            "body_owner": {"location": "query", "openapi_name": "body_owner"},
        }
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner"},
                    },
                },
            },
        )
        route = _make_route_stub(
            path="/test/{owner}",
            method="POST",
            parameter_map=parameter_map,
        )
        _apply_param_rename(route, spec)
        # Query param should NOT be renamed (only body params are renamed)
        assert route.parameter_map["body_owner"]["openapi_name"] == "body_owner"
