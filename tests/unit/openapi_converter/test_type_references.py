"""Unit tests for the type-reference analysis (openapi_converter.type_references).

Covers ``stamp_type_references`` — the pre-wrap pass that stamps
``x-resource-types`` on GET operations and ``x-modifies-type`` on write
operations for cache invalidation (issue #743).
"""

from typing import Any

from gitea_mcp_server.openapi_converter.type_references import (
    _collect_refs,
    _collect_transitive_refs,
    _primary_type,
    _resolve_ref,
    _success_schema,
    stamp_type_references,
)
from tests.helpers.spec_fixtures import make_openapi_spec


def _get_op(op_id: str, ref: str) -> dict:
    return {
        "operationId": op_id,
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
                },
            }
        },
    }


def _list_op(op_id: str, ref: str) -> dict:
    return {
        "operationId": op_id,
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": f"#/components/schemas/{ref}"},
                        }
                    }
                },
            }
        },
    }


def _write_op(op_id: str, ref: str, code: str = "201") -> dict:
    return {
        "operationId": op_id,
        "responses": {
            code: {
                "description": "ok",
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
                },
            }
        },
    }


def _empty_op(op_id: str) -> dict:
    return {"operationId": op_id, "responses": {"204": {"description": "no content"}}}


def _make_spec() -> Any:
    """Build a spec with GET + write operations and shared schemas."""
    return make_openapi_spec(
        paths={
            "/repos/{owner}/{repo}/issues": {
                "get": _list_op("issueList", "Issue"),
                "post": _write_op("issueCreate", "Issue"),
            },
            "/repos/{owner}/{repo}/labels": {
                "get": _list_op("labelList", "Label"),
                "post": _write_op("labelCreate", "Label"),
            },
            "/repos/{owner}/{repo}/labels/{id}": {
                "get": _get_op("labelGet", "Label"),
                "delete": _empty_op("labelDelete"),
            },
            "/repos/{owner}/{repo}/pulls/{index}/merge": {
                "post": _empty_op("pullMerge"),
            },
        },
        components={
            "schemas": {
                "Issue": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                    },
                },
                "Label": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
    )


class TestStampTypeReferences:
    """Tests for the main stamping pass."""

    def test_get_operation_gets_transitive_resource_types(self) -> None:
        """A GET operation reports its response type plus transitive refs."""
        spec = _make_spec()
        stamp_type_references(spec)
        types = spec["paths"]["/repos/{owner}/{repo}/issues"]["get"]["x-resource-types"]
        assert set(types) == {"Issue", "Label"}

    def test_write_operation_gets_modified_type(self) -> None:
        """A write operation reports the type its response returns."""
        spec = _make_spec()
        stamp_type_references(spec)
        assert spec["paths"]["/repos/{owner}/{repo}/labels"]["post"]["x-modifies-type"] == "Label"

    def test_empty_write_falls_back_to_get_sibling(self) -> None:
        """A 204 write falls back to the GET sibling's element type."""
        spec = _make_spec()
        stamp_type_references(spec)
        assert (
            spec["paths"]["/repos/{owner}/{repo}/labels/{id}"]["delete"]["x-modifies-type"]
            == "Label"
        )

    def test_empty_write_without_get_sibling_has_no_type(self) -> None:
        """A 204 write with no GET sibling gets no x-modifies-type."""
        spec = _make_spec()
        stamp_type_references(spec)
        assert (
            "x-modifies-type"
            not in spec["paths"]["/repos/{owner}/{repo}/pulls/{index}/merge"]["post"]
        )

    def test_non_dict_path_item_skipped(self) -> None:
        """Non-dict path items are skipped without error."""
        spec = make_openapi_spec(paths={"/weird": "not-a-dict"})
        stamp_type_references(spec)  # must not raise

    def test_non_dict_operation_skipped(self) -> None:
        """Non-dict operations are skipped without error."""
        spec = make_openapi_spec(paths={"/weird": {"get": "not-a-dict"}})
        stamp_type_references(spec)  # must not raise


class TestSuccessSchema:
    """Tests for _success_schema."""

    def test_returns_schema_with_ref_intact(self) -> None:
        """The raw response schema keeps its $ref (pre-wrap)."""
        spec = _make_spec()
        schema = _success_schema(spec, "/repos/{owner}/{repo}/labels", "GET")
        assert schema == {"type": "array", "items": {"$ref": "#/components/schemas/Label"}}

    def test_missing_path_returns_none(self) -> None:
        spec = _make_spec()
        assert _success_schema(spec, "/does/not/exist", "GET") is None

    def test_non_dict_path_item_returns_none(self) -> None:
        spec = make_openapi_spec(paths={"/weird": "not-a-dict"})
        assert _success_schema(spec, "/weird", "GET") is None

    def test_non_dict_operation_returns_none(self) -> None:
        spec = make_openapi_spec(paths={"/weird": {"get": "not-a-dict"}})
        assert _success_schema(spec, "/weird", "GET") is None

    def test_non_dict_response_returns_none(self) -> None:
        spec = make_openapi_spec(paths={"/weird": {"get": {"responses": {"200": "not-a-dict"}}}})
        assert _success_schema(spec, "/weird", "GET") is None

    def test_non_dict_responses_returns_none(self) -> None:
        spec = make_openapi_spec(paths={"/weird": {"get": {"responses": "not-a-dict"}}})
        assert _success_schema(spec, "/weird", "GET") is None

    def test_non_dict_json_content_returns_none(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/weird": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {"application/json": "not-a-dict"},
                            }
                        }
                    }
                }
            }
        )
        assert _success_schema(spec, "/weird", "GET") is None

    def test_unresolvable_response_ref_returns_none(self) -> None:
        """A response-level $ref that cannot be resolved returns None."""
        spec = make_openapi_spec(
            paths={
                "/weird": {
                    "get": {"responses": {"200": {"$ref": "#/components/responses/missing"}}}
                }
            }
        )
        assert _success_schema(spec, "/weird", "GET") is None

    def test_non_dict_content_returns_none(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/weird": {"get": {"responses": {"200": {"description": "ok", "content": "nope"}}}}
            }
        )
        assert _success_schema(spec, "/weird", "GET") is None

    def test_no_json_content_returns_none(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/weird": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {"text/plain": {"schema": {"type": "string"}}},
                            }
                        }
                    }
                }
            }
        )
        assert _success_schema(spec, "/weird", "GET") is None


class TestPrimaryType:
    """Tests for _primary_type."""

    def test_object_ref(self) -> None:
        assert _primary_type({"$ref": "#/components/schemas/Label"}) == "Label"

    def test_array_ref(self) -> None:
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Label"}}
        assert _primary_type(schema) == "Label"

    def test_array_without_ref_items(self) -> None:
        schema = {"type": "array", "items": {"type": "string"}}
        assert _primary_type(schema) is None

    def test_inline_object_no_ref(self) -> None:
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert _primary_type(schema) is None

    def test_none_schema(self) -> None:
        assert _primary_type(None) is None


class TestCollectRefs:
    """Tests for the ref collectors."""

    def test_collect_refs_direct_only(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "user": {"$ref": "#/components/schemas/User"},
                "labels": {"type": "array", "items": {"$ref": "#/components/schemas/Label"}},
            },
        }
        assert _collect_refs(schema) == {"User", "Label"}

    def test_collect_refs_non_dict(self) -> None:
        assert _collect_refs("not-a-dict") == set()

    def test_collect_transitive_refs_follows_refs(self) -> None:
        spec = _make_spec()
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}
        assert _collect_transitive_refs(schema, spec) == {"Issue", "Label"}

    def test_collect_transitive_refs_non_dict(self) -> None:
        spec = _make_spec()
        assert _collect_transitive_refs("not-a-dict", spec) == set()

    def test_collect_transitive_refs_cycle_guarded(self) -> None:
        """Cyclic schema references terminate."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {"next": {"$ref": "#/components/schemas/Node"}},
                    }
                }
            }
        )
        schema = {"$ref": "#/components/schemas/Node"}
        assert _collect_transitive_refs(schema, spec) == {"Node"}


class TestResolveRef:
    """Tests for _resolve_ref."""

    def test_resolves_valid_ref(self) -> None:
        spec = _make_spec()
        resolved = _resolve_ref(spec, "#/components/schemas/Label")
        assert isinstance(resolved, dict)
        assert resolved["type"] == "object"

    def test_missing_segment_returns_none(self) -> None:
        spec = _make_spec()
        assert _resolve_ref(spec, "#/components/schemas/Missing") is None

    def test_non_dict_result_returns_none(self) -> None:
        spec = make_openapi_spec(components={"schemas": {"X": "not-a-dict"}})
        assert _resolve_ref(spec, "#/components/schemas/X") is None
