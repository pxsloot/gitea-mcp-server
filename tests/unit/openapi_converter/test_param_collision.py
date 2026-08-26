"""Unit tests for parameter collision resolution.

Tests the spec-level renaming of body properties that collide with path
parameter names, and the runtime shim that fixes the ``parameter_map``.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest

from gitea_mcp_server.openapi_converter.param_collision import (
    _collect_path_item_params,
    _collect_path_param_descriptions,
    _collect_path_param_names,
    _flatten_body_schema,
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
# Tests: _flatten_body_schema
# ===========================================================================


class TestFlattenBodySchema:
    """Tests for allOf/$ref flattening and oneOf/anyOf passthrough (issue #679)."""

    # --- No composition ---

    def test_flat_schema_passes_through(self) -> None:
        """A flat schema with no composition returns the same object."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result is schema  # Same object returned
        assert result["properties"] == {"title": {"type": "string"}}

    # --- $ref resolution ---

    def test_top_level_ref_resolved_and_inlined(self) -> None:
        """A top-level $ref is resolved; the result is a deep copy."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "IssueMeta": {
                        "type": "object",
                        "properties": {"owner": {"type": "string"}},
                    },
                },
            },
        )
        schema: dict[str, Any] = {"$ref": "#/components/schemas/IssueMeta"}
        result = _flatten_body_schema(schema, spec)
        assert result is not schema
        assert "$ref" not in result
        assert result["properties"] == {"owner": {"type": "string"}}
        # Shared component untouched
        assert spec["components"]["schemas"]["IssueMeta"]["properties"] == {
            "owner": {"type": "string"},
        }

    def test_ref_chain_resolved_recursively(self) -> None:
        """A $ref whose target has an allOf [$ref B] resolves both levels."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Base": {
                        "type": "object",
                        "properties": {"owner": {"type": "string"}},
                    },
                    "Extended": {
                        "allOf": [{"$ref": "#/components/schemas/Base"}],
                        "properties": {"note": {"type": "string"}},
                    },
                },
            },
        )
        schema: dict[str, Any] = {"$ref": "#/components/schemas/Extended"}
        result = _flatten_body_schema(schema, spec)
        assert result["properties"] == {
            "owner": {"type": "string"},
            "note": {"type": "string"},
        }
        assert "allOf" not in result

    def test_ref_cycle_terminates(self) -> None:
        """A self-referential $ref (A allOf [$ref A]) terminates gracefully."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Cyclic": {
                        "allOf": [
                            {"$ref": "#/components/schemas/Cyclic"},
                            {"type": "object", "properties": {"owner": {"type": "string"}}},
                        ],
                    },
                },
            },
        )
        schema: dict[str, Any] = {"$ref": "#/components/schemas/Cyclic"}
        result = _flatten_body_schema(schema, spec)  # Must not hang
        # The cyclic member contributes nothing; the inline member is merged
        assert result["properties"] == {"owner": {"type": "string"}}
        assert "allOf" not in result

    def test_unresolvable_ref_returned_unchanged(self) -> None:
        """An unresolvable $ref returns the schema as-is."""
        schema: dict[str, Any] = {"$ref": "#/components/schemas/Nonexistent"}
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result is schema
        assert "$ref" in result

    def test_sibling_keys_override_ref(self) -> None:
        """Keys next to $ref (allowed since OpenAPI 3.1) override resolved keys."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Base": {
                        "type": "object",
                        "description": "component description",
                        "properties": {"owner": {"type": "string"}},
                    },
                },
            },
        )
        schema: dict[str, Any] = {
            "$ref": "#/components/schemas/Base",
            "description": "operation-level description",
        }
        result = _flatten_body_schema(schema, spec)
        assert result["description"] == "operation-level description"
        assert result["properties"] == {"owner": {"type": "string"}}

    # --- allOf ---

    def test_allof_merges_inline_properties(self) -> None:
        """allOf members' properties merge into the schema in place."""
        schema: dict[str, Any] = {
            "allOf": [
                {"type": "object", "properties": {"owner": {"type": "string"}}},
                {"type": "object", "properties": {"repo": {"type": "string"}}},
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result is schema  # Mutated in place
        assert "allOf" not in result  # allOf removed (mirrors FastMCP's merge)
        assert result["properties"] == {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
        }

    def test_allof_resolves_nested_ref(self) -> None:
        """allOf with a $ref member resolves and merges its properties."""
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
        schema: dict[str, Any] = {
            "allOf": [
                {"$ref": "#/components/schemas/IssueMeta"},
                {"type": "object", "properties": {"note": {"type": "string"}}},
            ],
        }
        result = _flatten_body_schema(schema, spec)
        assert result["properties"] == {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "index": {"type": "integer"},
            "note": {"type": "string"},
        }

    def test_allof_preserves_required(self) -> None:
        """required lists from all members are merged and de-duplicated."""
        schema: dict[str, Any] = {
            "allOf": [
                {
                    "type": "object",
                    "properties": {"owner": {"type": "string"}},
                    "required": ["owner"],
                },
                {
                    "type": "object",
                    "properties": {"repo": {"type": "string"}},
                    "required": ["repo", "owner"],
                },
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result["required"] == ["owner", "repo"]

    def test_allof_merges_top_level_required(self) -> None:
        """A pre-existing top-level required list unions with member lists."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
            "allOf": [
                {
                    "type": "object",
                    "properties": {"owner": {"type": "string"}},
                    "required": ["owner", "note"],
                },
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result["required"] == ["owner", "note"]

    def test_allof_deep_copies_ref_item(self) -> None:
        """allOf with $ref does not mutate the shared component schema."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "IssueMeta": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                        },
                    },
                },
            },
        )
        schema: dict[str, Any] = {
            "allOf": [
                {"$ref": "#/components/schemas/IssueMeta"},
                {"type": "object", "properties": {"note": {"type": "string"}}},
            ],
        }
        _flatten_body_schema(schema, spec)

        # The original component must be unmodified
        original = spec["components"]["schemas"]["IssueMeta"]
        assert original["properties"] == {"owner": {"type": "string"}}
        assert "note" not in original["properties"]

    def test_allof_single_item_merged(self) -> None:
        """allOf with a single $ref member resolves and merges its properties."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "IssueMeta": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                        },
                    },
                },
            },
        )
        schema: dict[str, Any] = {
            "allOf": [
                {"$ref": "#/components/schemas/IssueMeta"},
            ],
        }
        result = _flatten_body_schema(schema, spec)
        assert result["properties"] == {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
        }

    def test_top_level_properties_win_over_allof(self) -> None:
        """Pre-existing top-level properties union with — and win over — allOf members."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"owner": {"type": "integer"}},
            "allOf": [
                {
                    "type": "object",
                    "properties": {"owner": {"type": "string"}, "note": {"type": "string"}},
                },
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result["properties"]["owner"] == {"type": "integer"}  # top-level wins
        assert result["properties"]["note"] == {"type": "string"}  # union
        assert "allOf" not in result

    def test_allof_preserves_other_keywords(self) -> None:
        """Top-level keywords (description, title) survive flattening."""
        schema: dict[str, Any] = {
            "description": "body description",
            "title": "BlockingInput",
            "allOf": [
                {"type": "object", "properties": {"owner": {"type": "string"}}},
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result["description"] == "body description"
        assert result["title"] == "BlockingInput"

    def test_nested_allof_inside_allof(self) -> None:
        """An allOf member with its own allOf is flattened recursively."""
        schema: dict[str, Any] = {
            "allOf": [
                {
                    "allOf": [
                        {"type": "object", "properties": {"owner": {"type": "string"}}},
                        {"type": "object", "properties": {"repo": {"type": "string"}}},
                    ],
                },
                {"type": "object", "properties": {"note": {"type": "string"}}},
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result["properties"] == {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "note": {"type": "string"},
        }

    # --- oneOf / anyOf: not flattened (FastMCP parity, see #679) ---

    def test_oneof_passes_through_unmodified(self) -> None:
        """oneOf is NOT flattened — FastMCP does not explode it into params."""
        schema: dict[str, Any] = {
            "oneOf": [
                {"type": "object", "properties": {"owner": {"type": "string"}}},
                {
                    "type": "object",
                    "properties": {"owner": {"type": "string"}, "url": {"type": "string"}},
                },
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result is schema
        assert "oneOf" in result
        assert "properties" not in result

    def test_anyof_passes_through_unmodified(self) -> None:
        """anyOf is NOT flattened — same FastMCP parity reasoning as oneOf."""
        schema: dict[str, Any] = {
            "anyOf": [
                {"type": "object", "properties": {"owner": {"type": "string"}}},
                {"type": "null"},
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result is schema
        assert "anyOf" in result
        assert "properties" not in result

    # --- Edge cases ---

    def test_empty_allof_returns_unchanged(self) -> None:
        """Empty allOf list returns the original schema unchanged."""
        schema: dict[str, Any] = {"allOf": []}
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result is schema

    def test_non_dict_allof_items_skipped(self) -> None:
        """Non-dict items inside allOf are skipped."""
        schema: dict[str, Any] = {
            "allOf": [
                {"type": "object", "properties": {"owner": {"type": "string"}}},
                "not_a_dict",
            ],
        }
        spec = make_openapi_spec()
        result = _flatten_body_schema(schema, spec)
        assert result["properties"] == {"owner": {"type": "string"}}

    def test_broken_ref_in_allof_skipped(self) -> None:
        """Unresolvable $ref inside allOf is skipped gracefully."""
        spec = make_openapi_spec()
        schema: dict[str, Any] = {
            "allOf": [
                {"$ref": "#/components/schemas/Nonexistent"},
                {"type": "object", "properties": {"note": {"type": "string"}}},
            ],
        }
        result = _flatten_body_schema(schema, spec)
        # Only the inline member's properties are merged
        assert result["properties"] == {"note": {"type": "string"}}


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

    # ------------------------------------------------------------------
    # Description injection
    # ------------------------------------------------------------------

    def test_injects_description_from_path_param(self) -> None:
        """Empty description is filled from path param description."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "title": {"type": "string"},
            },
        }
        _rename_colliding_body_properties(
            schema,
            {"owner", "repo"},
            path_param_descriptions={
                "owner": "owner of the repo",
                "repo": "name of the repo",
            },
        )

        body_owner = cast("dict[str, Any]", schema["properties"]["body_owner"])
        assert body_owner["description"] == "(Request body) owner of the repo"
        body_repo = cast("dict[str, Any]", schema["properties"]["body_repo"])
        assert body_repo["description"] == "(Request body) name of the repo"

    def test_fallback_when_path_param_has_no_description(self) -> None:
        """Fallback to generic description when path param has none."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
            },
        }
        _rename_colliding_body_properties(
            schema,
            {"owner"},
            path_param_descriptions={},  # Empty descriptions dict
        )

        body_owner = cast("dict[str, Any]", schema["properties"]["body_owner"])
        assert body_owner["description"] == ("owner field of the request body resource")

    def test_preserves_existing_description(self) -> None:
        """Non-empty existing description is not overwritten."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "the issue owner"},
            },
        }
        _rename_colliding_body_properties(
            schema,
            {"owner"},
            path_param_descriptions={
                "owner": "owner of the repo",
            },
        )

        body_owner = cast("dict[str, Any]", schema["properties"]["body_owner"])
        assert body_owner["description"] == "the issue owner"

    def test_path_param_descriptions_none_skips_injection(self) -> None:
        """``path_param_descriptions=None`` skips description injection entirely."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "owner": {"type": "string"},
            },
        }
        _rename_colliding_body_properties(
            schema,
            {"owner"},
            path_param_descriptions=None,
        )

        body_owner = cast("dict[str, Any]", schema["properties"]["body_owner"])
        assert "description" not in body_owner


# ===========================================================================
# Tests: _collect_path_param_descriptions
# ===========================================================================


class TestCollectPathParamDescriptions:
    """Tests for collecting path param descriptions from both levels."""

    def test_collects_from_operation_level(self) -> None:
        """Descriptions from operation-level params are collected."""
        op = _make_operation(path_params=["owner", "repo"])
        # Override params to include descriptions
        op["parameters"][0]["description"] = "owner of the repo"
        op["parameters"][1]["description"] = "name of the repo"

        result = _collect_path_param_descriptions(op, [])
        assert result == {"owner": "owner of the repo", "repo": "name of the repo"}

    def test_collects_from_path_item_level(self) -> None:
        """Descriptions from path-item-level params are collected."""
        op = _make_operation()
        path_item_params: list[dict[str, Any]] = [
            {"name": "owner", "in": "path", "description": "owner of the repo"},
            {"name": "repo", "in": "path", "description": "name of the repo"},
        ]
        result = _collect_path_param_descriptions(op, path_item_params)
        assert result == {
            "owner": "owner of the repo",
            "repo": "name of the repo",
        }

    def test_operation_level_overrides_path_item(self) -> None:
        """Operation-level description takes precedence."""
        op = _make_operation(path_params=["owner"])
        op["parameters"][0]["description"] = "from operation"

        path_item_params: list[dict[str, Any]] = [
            {"name": "owner", "in": "path", "description": "from path item"},
        ]
        result = _collect_path_param_descriptions(op, path_item_params)
        assert result == {"owner": "from operation"}

    def test_skips_empty_descriptions(self) -> None:
        """Params with empty descriptions are skipped."""
        op = _make_operation(path_params=["owner", "repo"])
        op["parameters"][0]["description"] = ""  # Empty
        op["parameters"][1]["description"] = "name of the repo"

        result = _collect_path_param_descriptions(op, [])
        assert result == {"repo": "name of the repo"}

    def test_skips_non_path_params(self) -> None:
        """Query and header params are skipped."""
        op = {
            "operationId": "test",
            "parameters": [
                {"name": "q", "in": "query", "description": "search query"},
                {"name": "X-Custom", "in": "header", "description": "custom header"},
            ],
        }
        result = _collect_path_param_descriptions(op, [])
        assert result == {}

    def test_skips_non_dict_params(self) -> None:
        """Non-dict parameters are skipped."""
        op = {
            "operationId": "test",
            "parameters": [
                {"name": "owner", "in": "path", "description": "owner of the repo"},
                "not_a_dict",
            ],
        }
        result = _collect_path_param_descriptions(op, [])
        assert result == {"owner": "owner of the repo"}

    def test_non_list_parameters(self) -> None:
        """Non-list ``parameters`` returns empty dict."""
        op = {"operationId": "test", "parameters": "not_a_list"}
        result = _collect_path_param_descriptions(op, [])
        assert result == {}


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

    def test_injects_descriptions_via_path_item_params(self) -> None:
        """Renamed body properties get descriptions from path param descriptions."""
        op = _make_operation(
            path_params=["owner", "repo"],
            body_props={"owner": {"type": "string"}, "repo": {"type": "string"}},
        )
        # Add descriptions to operation-level path params
        for p in op["parameters"]:
            if p["name"] == "owner":
                p["description"] = "owner of the repo"
            elif p["name"] == "repo":
                p["description"] = "name of the repo"

        spec = make_openapi_spec()
        result = _resolve_operation_collisions(
            op,
            {"owner", "repo"},
            spec,
            path_item_params=[],
        )
        assert result == {"body_owner": "owner", "body_repo": "repo"}

        # Verify descriptions were injected
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        body_props = body_schema["properties"]
        assert body_props["body_owner"]["description"] == "(Request body) owner of the repo"
        assert body_props["body_repo"]["description"] == "(Request body) name of the repo"

    def test_no_path_item_params_skips_injection(self) -> None:
        """When path_item_params is not passed, no description injection occurs."""
        op = _make_operation(
            path_params=["owner", "repo"],
            body_props={"owner": {"type": "string"}, "repo": {"type": "string"}},
        )
        spec = make_openapi_spec()
        result = _resolve_operation_collisions(op, {"owner", "repo"}, spec)
        assert result == {"body_owner": "owner", "body_repo": "repo"}

        # Verify no descriptions were injected
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        body_props = body_schema["properties"]
        assert "description" not in body_props["body_owner"]
        assert "description" not in body_props["body_repo"]

    # --- allOf body schemas ---

    def test_resolves_collisions_with_allof_and_nested_ref(self) -> None:
        """Collisions in allOf with a nested $ref are resolved."""
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
        op: dict[str, Any] = {
            "operationId": "test_allof_collision",
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
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
        }
        result = _resolve_operation_collisions(op, {"owner", "repo"}, spec)
        assert result == {"body_owner": "owner", "body_repo": "repo"}
        assert op["x-param-rename"] == result

        # Verify the inlined+flattened schema has the renamed properties
        body_schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "allOf" not in body_schema  # Flattened (mirrors FastMCP's merge)
        props = body_schema["properties"]
        assert "body_owner" in props
        assert "body_repo" in props
        assert "owner" not in props
        assert "repo" not in props
        # index should NOT be renamed (not a path param in this test)
        assert "index" in props
        # note should be preserved
        assert "note" in props

        # Verify shared component is NOT mutated
        original = spec["components"]["schemas"]["IssueMeta"]
        assert "owner" in original["properties"]
        assert "body_owner" not in original["properties"]

    def test_allof_with_partial_collision(self) -> None:
        """allOf where only some allOf properties collide with path params."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Base": {
                        "type": "object",
                        "properties": {
                            "owner": {"type": "string"},
                            "title": {"type": "string"},
                        },
                    },
                },
            },
        )
        op: dict[str, Any] = {
            "operationId": "test_partial_collision",
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "allOf": [
                                {"$ref": "#/components/schemas/Base"},
                                {
                                    "type": "object",
                                    "properties": {"body": {"type": "string"}},
                                },
                            ],
                        },
                    },
                },
                "required": True,
            },
        }
        result = _resolve_operation_collisions(op, {"owner", "repo"}, spec)
        # Only "owner" collides (not "repo", "title", "body")
        assert result == {"body_owner": "owner"}
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "body_owner" in props
        assert "title" in props  # Not a collision
        assert "body" in props  # Not a collision

    def test_allof_merges_with_top_level_properties(self) -> None:
        """A body schema with both top-level properties and allOf unions them."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Base": {
                        "type": "object",
                        "properties": {"repo": {"type": "string"}},
                    },
                },
            },
        )
        op: dict[str, Any] = {
            "operationId": "test_mixed_collision",
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
                {"name": "repo", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"owner": {"type": "string"}},
                            "allOf": [
                                {"$ref": "#/components/schemas/Base"},
                            ],
                        },
                    },
                },
                "required": True,
            },
        }
        result = _resolve_operation_collisions(op, {"owner", "repo"}, spec)
        assert result == {"body_owner": "owner", "body_repo": "repo"}
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "allOf" not in schema
        props = schema["properties"]
        assert "body_owner" in props
        assert "body_repo" in props

    # --- oneOf/anyOf body schemas: tripwire warning (issue #679) ---

    def test_oneof_body_warns_and_is_not_renamed(self, caplog: pytest.LogCaptureFixture) -> None:
        """oneOf bodies are not flattened; a tripwire warning is logged.

        FastMCP does not explode oneOf into parameters, so there is no
        property-level collision to resolve.  The body schema must stay
        intact for FastMCP, and the warning makes the uncovered shape
        visible instead of silently degrading.
        """
        spec = make_openapi_spec()
        op: dict[str, Any] = {
            "operationId": "test_oneof_tripwire",
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {
                                        "owner": {"type": "string"},
                                        "url": {"type": "string"},
                                    },
                                },
                                {
                                    "type": "object",
                                    "properties": {
                                        "owner": {"type": "string"},
                                        "content": {"type": "string"},
                                    },
                                },
                            ],
                        },
                    },
                },
                "required": True,
            },
        }
        with caplog.at_level(logging.WARNING):
            result = _resolve_operation_collisions(op, {"owner"}, spec)

        assert result is None  # No rename: no visible properties to collide
        assert "test_oneof_tripwire" in caplog.text
        assert "oneOf" in caplog.text
        # The oneOf schema is preserved for FastMCP
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "oneOf" in schema
        assert "properties" not in schema

    def test_anyof_body_warns_and_is_not_renamed(self, caplog: pytest.LogCaptureFixture) -> None:
        """anyOf bodies trigger the same tripwire warning as oneOf."""
        spec = make_openapi_spec()
        op: dict[str, Any] = {
            "operationId": "test_anyof_tripwire",
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "anyOf": [
                                {"type": "object", "properties": {"owner": {"type": "string"}}},
                                {"type": "null"},
                            ],
                        },
                    },
                },
                "required": True,
            },
        }
        with caplog.at_level(logging.WARNING):
            result = _resolve_operation_collisions(op, {"owner"}, spec)

        assert result is None
        assert "test_anyof_tripwire" in caplog.text
        assert "anyOf" in caplog.text
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "anyOf" in schema

    def test_nested_composition_in_allof_dropped_without_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A oneOf nested inside an allOf member is dropped without a warning.

        The tripwire inspects the flattened *top-level* schema only.  A
        ``oneOf``/``anyOf`` buried in an ``allOf`` member is invisible to
        ``_merge_allof_members`` — and to FastMCP itself, which merges only
        members carrying their own ``properties`` — so the tool shape is
        parity by construction and warning about it would be noise.  This
        test pins that contract so a future change cannot silently turn the
        nested case into a warning (or vice versa).
        """
        spec = make_openapi_spec()
        op: dict[str, Any] = {
            "operationId": "test_nested_oneof_in_allof",
            "parameters": [
                {"name": "owner", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "allOf": [
                                {
                                    "oneOf": [
                                        {
                                            "type": "object",
                                            "properties": {"owner": {"type": "string"}},
                                        },
                                        {
                                            "type": "object",
                                            "properties": {"note": {"type": "string"}},
                                        },
                                    ],
                                },
                                {"type": "object", "properties": {"note": {"type": "string"}}},
                            ],
                        },
                    },
                },
                "required": True,
            },
        }
        with caplog.at_level(logging.WARNING):
            result = _resolve_operation_collisions(op, {"owner"}, spec)

        assert result is None  # Nested oneOf member dropped; no properties to collide
        assert "oneOf" not in caplog.text  # No tripwire warning for the nested case
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        assert "allOf" not in schema  # Flattened (mirrors FastMCP's merge)
        assert schema["properties"] == {"note": {"type": "string"}}


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

    def test_delete_operation_with_body_resolved(self) -> None:
        """DELETE operations with request bodies are resolved.

        Gitea's swagger declares bodies on some DELETE endpoints (e.g.
        ``DELETE /repos/{owner}/{repo}/issues/{index}/blocks`` takes an
        ``IssueMeta`` body).  The converter currently drops DELETE bodies,
        so this case is synthetic today — resolution must still cover it so
        it keeps working when the converter starts emitting DELETE request
        bodies.
        """
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/issues/{index}/blocks": {
                    "delete": _make_operation(
                        path_params=["owner", "repo", "index"],
                        body_props={
                            "owner": {"type": "string"},
                            "repo": {"type": "string"},
                            "index": {"type": "integer"},
                        },
                        method="delete",
                    ),
                },
            },
        )
        resolve_param_collisions(spec)

        op = spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["delete"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "body_owner" in props
        assert "body_repo" in props
        assert "body_index" in props
        assert cast("dict[str, Any]", op)["x-param-rename"] == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }

    def test_resolution_is_method_agnostic(self) -> None:
        """Any operation with a request body is resolved, regardless of method.

        Resolution is behavior-driven: the presence of a request body is
        the gate, not the HTTP method.  A GET with a request body is
        unusual (and Gitea has none), but if a spec ever contains one,
        FastMCP would generate ``__path`` suffixes for it — so the resolver
        must not skip it.
        """
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}": {
                    "get": _make_operation(
                        path_params=["owner", "repo"],
                        body_props={"owner": {"type": "string"}},
                        method="get",
                    ),
                },
            },
        )
        resolve_param_collisions(spec)

        op = spec["paths"]["/repos/{owner}/{repo}"]["get"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert "body_owner" in props
        assert cast("dict[str, Any]", op)["x-param-rename"] == {"body_owner": "owner"}

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

    def test_never_raises(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exceptions inside collision resolution are caught and logged.

        The ``resolve_param_collisions`` docstring declares a
        "guaranteed not to raise" contract.  This test validates it
        by forcing an exception deep inside the scan loop and asserting
        that the function returns normally (no propagation) and logs
        at error level.
        """
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": _make_operation(
                        path_params=["owner"],
                        body_props={"owner": {"type": "string"}},
                    ),
                },
            },
        )
        monkeypatch.setattr(
            "gitea_mcp_server.openapi_converter.param_collision._resolve_operation_collisions",
            MagicMock(side_effect=RuntimeError("simulated internal failure")),
        )
        with caplog.at_level(logging.ERROR):
            resolve_param_collisions(spec)  # Must not raise

        assert "Failed to resolve parameter name collisions" in caplog.text

    # --- allOf body schemas (full pipeline) ---

    def test_allof_body_schema_with_nested_ref(self) -> None:
        """Full resolve_param_collisions handles allOf with nested $ref.

        Simulates the scenario from issue #679: a body schema using
        ``allOf`` with a ``$ref`` to a shared component plus inline
        extensions.
        """
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
                        "responses": {"201": {"description": "Created"}},
                    },
                },
            },
        )
        resolve_param_collisions(spec)

        op = spec["paths"]["/repos/{owner}/{repo}/issues/{index}/blocks"]["post"]
        props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]

        # All three colliding properties are renamed
        assert "body_owner" in props
        assert "body_repo" in props
        assert "body_index" in props
        assert "owner" not in props
        assert "repo" not in props
        assert "index" not in props
        # Non-colliding properties preserved
        assert "note" in props

        # x-param-rename is set
        assert cast("dict[str, Any]", op)["x-param-rename"] == {
            "body_owner": "owner",
            "body_repo": "repo",
            "body_index": "index",
        }

        # Shared component is NOT mutated
        original = spec["components"]["schemas"]["IssueMeta"]
        assert "owner" in original["properties"]
        assert "body_owner" not in original["properties"]


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
        """Query/header/cookie params are renamed; path params are deferred (#734)."""
        parameter_map = {
            "owner": {"location": "path", "openapi_name": "owner"},
            "body_owner": {"location": "query", "openapi_name": "body_owner"},
            "do": {"location": "header", "openapi_name": "do"},
        }
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {
                            "body_owner": "owner",
                            "do": "Do",
                        },
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
        # Query and header params ARE renamed (openapi_name -> original wire name).
        assert route.parameter_map["body_owner"]["openapi_name"] == "owner"
        assert route.parameter_map["do"]["openapi_name"] == "Do"
        # Path params are deferred to issue #734 — not renamed.
        assert route.parameter_map["owner"]["openapi_name"] == "owner"
