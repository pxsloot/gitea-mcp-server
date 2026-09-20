"""Unit tests for gitea_mcp_server/marker.py.

The canonical agent-facing ``$ref`` marker contract (#763):

- ``ref_marker`` — build the marker (normalising a raw pointer/path)
- ``is_ref_marker`` — structurally strict detection
- ``ref_marker_label`` — render the marker for markdown/scalar output
- ``RefMarker`` — the typed marker shape

Cross-producer and cross-surface agreement also lives here.
"""

from __future__ import annotations

from typing import Any

from fastmcp.tools.base import Tool

from gitea_mcp_server.marker import (
    RefMarker,
    is_ref_marker,
    ref_marker,
    ref_marker_label,
)
from gitea_mcp_server.tools.result_pipeline import ExecutionResult
from tests.helpers.mcp_results import parse_json_content


class TestRefMarker:
    """The canonical agent-facing ``$ref`` marker contract (#763)."""

    def test_object_marker_is_single_key(self) -> None:
        assert ref_marker("User") == {"$ref": "User"}

    def test_list_marker_carries_count(self) -> None:
        assert ref_marker("Label", 2) == {"$ref": "Label", "count": 2}

    def test_zero_count_is_kept(self) -> None:
        """A collapsed empty list still carries ``count: 0`` (not omitted)."""
        assert ref_marker("Label", 0) == {"$ref": "Label", "count": 0}

    def test_typed_marker_shape(self) -> None:
        """``RefMarker`` declares ``$ref`` required and ``count`` optional."""
        assert RefMarker.__required_keys__ == frozenset({"$ref"})
        assert RefMarker.__optional_keys__ == frozenset({"count"})

    def test_ref_marker_normalizes_raw_reference(self) -> None:
        """A raw pointer/path is reduced to its bare type name by ``ref_marker``.

        Callers pass the reference they have (a JSON pointer or a path); the
        ``rsplit`` lives here, not at every call site.
        """
        assert ref_marker("#/components/schemas/User") == {"$ref": "User"}
        assert ref_marker("a/b") == {"$ref": "b"}
        assert ref_marker("#c") == {"$ref": "c"}
        assert ref_marker("#/components/schemas/Label", 2) == {
            "$ref": "Label",
            "count": 2,
        }

    def test_ref_marker_and_is_ref_marker_are_inverses(self) -> None:
        """Every marker ``ref_marker`` builds is recognised by ``is_ref_marker``."""
        for raw in ("User", "#/components/schemas/User", "a/b", "#c"):
            assert is_ref_marker(ref_marker(raw)) is True

    def test_is_ref_marker_accepts_both_forms(self) -> None:
        assert is_ref_marker({"$ref": "User"}) is True
        assert is_ref_marker({"$ref": "Label", "count": 2}) is True
        assert is_ref_marker({"$ref": "Label", "count": 0}) is True

    def test_is_ref_marker_rejects_other_shapes(self) -> None:
        assert is_ref_marker("$ref:User") is False  # the retired string form
        assert is_ref_marker({"$ref": 1}) is False  # type name must be a string
        assert is_ref_marker({}) is False
        assert is_ref_marker(None) is False
        assert is_ref_marker([{"$ref": "User"}]) is False
        # Extra keys beyond count are not the marker shape.
        assert is_ref_marker({"$ref": "Label", "count": 2, "extra": 1}) is False

    def test_is_ref_marker_count_must_be_int(self) -> None:
        assert is_ref_marker({"$ref": "Label", "count": "2"}) is False
        # ``bool`` is an ``int`` subclass — a boolean count is not a count.
        assert is_ref_marker({"$ref": "Label", "count": True}) is False

    def test_is_ref_marker_rejects_json_schema_pointers(self) -> None:
        """A schema ``$ref`` pointer is not a marker (#763).

        ``ref_marker`` normalises a pointer to a bare name, so a *raw* pointer
        (not produced by ``ref_marker``) must not be mistaken for a marker by
        the display path.
        """
        assert is_ref_marker({"$ref": "#/components/schemas/User"}) is False
        assert is_ref_marker({"$ref": "components/schemas/User"}) is False
        assert is_ref_marker({"$ref": ""}) is False

    def test_label_renders_object_marker(self) -> None:
        assert ref_marker_label(ref_marker("User")) == "$ref:User"

    def test_label_renders_list_marker_with_count(self) -> None:
        assert ref_marker_label(ref_marker("Label", 2)) == "$ref:Label[2]"

    def test_label_renders_zero_count_list_marker(self) -> None:
        assert ref_marker_label(ref_marker("Label", 0)) == "$ref:Label[0]"


class TestRefMarkerContract:
    """The one agent-facing ``$ref`` marker shape (#763).

    Two producers emit the marker — the concise collapse (``collapse_data``)
    and the compact example generator (``schema_to_compact_example``) — and an
    agent reads it on two surfaces (``tool_info.output_example`` and
    ``format=json, detail=concise`` output).  These tests pin the shape as
    literally the same value across producers and surfaces.
    """

    _SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {"owner": {"$ref": "#/components/schemas/User"}},
    }
    _LIST_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/Label"},
            }
        },
    }

    def test_producers_emit_the_same_marker(self) -> None:
        from gitea_mcp_server.format import collapse_data
        from gitea_mcp_server.tools.examples import schema_to_compact_example

        # Object position: both producers emit {"$ref": "User"}.
        from_example = schema_to_compact_example({"$ref": "#/components/schemas/User"})
        collapsed = collapse_data(
            {"owner": {"id": 1, "login": "u"}},
            self._SCHEMA,
            detail="concise",
        )
        assert from_example == {"$ref": "User"}
        assert collapsed["owner"] == from_example
        assert is_ref_marker(collapsed["owner"])

        # Collapsed-list position: both producers emit one marker with count,
        # not an array wrapping a marker (#763 — the example is not an
        # exception to unification).
        example_list = schema_to_compact_example(self._LIST_SCHEMA)
        collapsed_list = collapse_data(
            {"labels": [{"id": 1, "name": "bug"}]},
            self._LIST_SCHEMA,
            detail="concise",
        )
        assert example_list["labels"] == {"$ref": "Label", "count": 1}
        assert collapsed_list["labels"] == example_list["labels"]
        assert is_ref_marker(collapsed_list["labels"])

    def test_producers_agree_on_root_ref_to_named_array(self) -> None:
        """Both producers resolve a root ``$ref`` payload before summarizing.

        The collapse and the example generator must not disagree about a
        referenced payload type's shape (#763).
        """
        from gitea_mcp_server.format import collapse_data
        from gitea_mcp_server.tools.examples import schema_to_compact_example
        from tests.helpers.spec_fixtures import make_openapi_spec

        spec = make_openapi_spec(
            components={
                "schemas": {
                    "User": {"type": "object", "properties": {"login": {"type": "string"}}},
                    "Repo": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "owner": {"$ref": "#/components/schemas/User"},
                        },
                    },
                    "RepoList": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Repo"},
                    },
                }
            }
        )
        schema = {"$ref": "#/components/schemas/RepoList"}
        data = [{"name": "r", "owner": {"login": "u"}}]

        collapsed = collapse_data(data, schema, detail="concise", openapi_spec=spec)
        example = schema_to_compact_example(schema, openapi_spec=spec)

        assert collapsed == [{"name": "r", "owner": {"$ref": "User"}}]
        assert example[0]["owner"] == {"$ref": "User"}

    def test_tool_info_and_concise_json_agree(self) -> None:
        """The marker an agent reads in tool_info is the one in concise JSON."""
        from gitea_mcp_server.format import format_tool_info_markdown
        from gitea_mcp_server.tools.examples import serialize_tool_schema
        from gitea_mcp_server.tools.result_pipeline import render

        tool = Tool(
            name="test_tool",
            description="A test tool.",
            parameters={"properties": {}},
            output_schema={"type": "object", "properties": {"result": self._SCHEMA}},
            meta={"output_schema_raw": self._SCHEMA},
        )
        info_markdown = format_tool_info_markdown(serialize_tool_schema(tool))
        assert '"$ref": "User"' in info_markdown

        rendered = render(
            ExecutionResult(data={"owner": {"id": 1, "login": "u"}}, shape="object"),
            fmt="json",
            detail="concise",
            schema=self._SCHEMA,
        )
        assert parse_json_content(rendered)["result"]["owner"] == {"$ref": "User"}
