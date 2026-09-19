"""Tests for gitea_mcp_server/format.py.

Covers all functions in __all__:
- _snake_to_title, _format_datetime, _format_scalar, _format_simple_value
- _resolve_anyof_schema, format_as_markdown, _format_parameter_table, _format_type
- call_markdown_formatter, _accepted_kwargs (signature-aware formatter dispatch)
- MarkdownFormatter (the canonical formatter contract alias)
"""

from typing import Any

from gitea_mcp_server.format import (
    MarkdownFormatter,
    _accepted_kwargs,
    _extract_type_name,
    _format_datetime,
    _format_parameter_table,
    _format_scalar,
    _format_simple_value,
    _format_type,
    _resolve_anyof_schema,
    _snake_to_title,
    call_markdown_formatter,
    collapse_data,
    format_as_markdown,
)
from gitea_mcp_server.models import (
    ToolSchemaResult,  # noqa: TC001 — used as runtime annotation in test helpers
)
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.result_pipeline import ExecutionResult, render
from tests.helpers.mcp_results import assert_dual_channel, parse_json_content
from tests.helpers.spec_fixtures import make_openapi_spec


class TestSnakeToTitle:
    def test_simple_snake_case(self) -> None:
        assert _snake_to_title("hello_world") == "Hello World"

    def test_single_word(self) -> None:
        assert _snake_to_title("hello") == "Hello"

    def test_camelcase_boundary(self) -> None:
        assert _snake_to_title("helloWorld") == "Hello World"

    def test_multiple_underscores(self) -> None:
        assert _snake_to_title("get_user_by_id") == "Get User By Id"

    def test_mixed_case_with_underscores(self) -> None:
        result = _snake_to_title("issue_list_labels")
        assert result == "Issue List Labels"

    def test_empty_string(self) -> None:
        assert _snake_to_title("") == ""

    def test_already_title_cased(self) -> None:
        assert _snake_to_title("Created") == "Created"

    def test_with_numbers(self) -> None:
        result = _snake_to_title("repo_2fa")
        assert result == "Repo 2Fa"

    def test_space_before_uppercase(self) -> None:
        """Names with embedded space before uppercase converts to lowercase."""
        result = _snake_to_title("get URL")
        assert result == "Get Url"


class TestFormatDatetime:
    def test_valid_iso_datetime(self) -> None:
        result = _format_datetime("2024-01-15T10:30:00Z")
        assert result == "2024-01-15 10:30:00 UTC"

    def test_none_input(self) -> None:
        assert _format_datetime(None) == "N/A"

    def test_empty_string(self) -> None:
        assert _format_datetime("") == "N/A"

    def test_invalid_string_passthrough(self) -> None:
        assert _format_datetime("not-a-date") == "not-a-date"

    def test_timezone_aware_iso(self) -> None:
        result = _format_datetime("2024-06-15T14:30:00+00:00")
        assert result == "2024-06-15 14:30:00 UTC"

    def test_epoch_zero(self) -> None:
        result = _format_datetime("1970-01-01T00:00:00Z")
        assert result == "1970-01-01 00:00:00 UTC"


class TestFormatScalar:
    def test_none_returns_na(self) -> None:
        assert _format_scalar(None) == "N/A"

    def test_boolean_true(self) -> None:
        assert _format_scalar(True) == "True"

    def test_boolean_false(self) -> None:
        assert _format_scalar(False) == "False"

    def test_integer(self) -> None:
        assert _format_scalar(42) == "42"

    def test_float(self) -> None:
        assert _format_scalar(3.14) == "3.14"

    def test_zero_float(self) -> None:
        assert _format_scalar(0.0) == "0.0"

    def test_string_passthrough(self) -> None:
        assert _format_scalar("hello") == "hello"

    def test_non_string_no_schema(self) -> None:
        assert _format_scalar(["a"]) == "['a']"

    def test_datetime_format_with_schema(self) -> None:
        schema = {"format": "date-time"}
        result = _format_scalar("2024-01-01T00:00:00Z", schema)
        assert result == "2024-01-01 00:00:00 UTC"

    def test_string_with_schema_no_date_format(self) -> None:
        schema = {"format": "email"}
        result = _format_scalar("user@example.com", schema)
        assert result == "user@example.com"

    def test_int_with_schema(self) -> None:
        schema = {"format": "int64"}
        assert _format_scalar(123, schema) == "123"


class TestFormatSimpleValue:
    def test_none_returns_na(self) -> None:
        assert _format_simple_value(None) == "N/A"

    def test_list_of_strings(self) -> None:
        assert _format_simple_value(["a", "b", "c"]) == "a, b, c"

    def test_list_of_mixed_types(self) -> None:
        assert _format_simple_value([1, "two", True]) == "1, two, True"

    def test_empty_list(self) -> None:
        assert _format_simple_value([]) == ""

    def test_dict(self) -> None:
        result = _format_simple_value({"key": "val"})
        assert '"key": "val"' in result

    def test_nested_dict(self) -> None:
        result = _format_simple_value({"a": {"b": "c"}})
        assert '"a"' in result

    def test_string(self) -> None:
        assert _format_simple_value("plain text") == "plain text"

    def test_integer(self) -> None:
        assert _format_simple_value(42) == "42"

    def test_boolean(self) -> None:
        assert _format_simple_value(True) == "True"


class TestExtractTypeName:
    """Tests for _extract_type_name — extracts type name from $ref in schemas."""

    def test_direct_ref(self) -> None:
        """Direct $ref on the schema itself."""
        schema = {"$ref": "#/components/schemas/Repository"}
        assert _extract_type_name(schema) == "Repository"

    def test_ref_in_anyof(self) -> None:
        """$ref nested inside anyOf."""
        schema = {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]}
        assert _extract_type_name(schema) == "User"

    def test_ref_in_oneof(self) -> None:
        """$ref nested inside oneOf."""
        schema = {"oneOf": [{"$ref": "#/components/schemas/Label"}, {"type": "string"}]}
        assert _extract_type_name(schema) == "Label"

    def test_no_ref_returns_none(self) -> None:
        """Schema without $ref returns None."""
        schema = {"type": "string"}
        assert _extract_type_name(schema) is None

    def test_none_schema_returns_none(self) -> None:
        """None input returns None without error."""
        assert _extract_type_name(None) is None

    def test_anyof_without_ref_returns_none(self) -> None:
        """anyOf with no $ref options returns None."""
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        assert _extract_type_name(schema) is None

    def test_list_of_non_dict_anyof(self) -> None:
        """anyOf containing non-dict options is handled gracefully."""
        schema = {"anyOf": ["string", "integer"]}
        assert _extract_type_name(schema) is None

    def test_ref_in_allof(self) -> None:
        """$ref nested inside allOf (common OpenAPI pattern)."""
        schema = {"allOf": [{"$ref": "#/components/schemas/User"}]}
        assert _extract_type_name(schema) == "User"

    def test_allof_combined_anyof(self) -> None:
        """allOf with anyOf ref — first found wins."""
        schema = {"allOf": [{"$ref": "#/components/schemas/Repository"}, {"type": "object"}]}
        assert _extract_type_name(schema) == "Repository"


class TestCallMarkdownFormatter:
    """Tests for call_markdown_formatter — signature-aware formatter dispatch.

    The display pipeline pre-collapses the data, so formatters declare only
    the keyword params they use (``extra``); the dispatch helper inspects
    each signature and passes exactly the accepted kwargs.  ``detail`` is
    deliberately not part of the contract — collapsed items are detected by
    shape, not by the detail flag.
    """

    def test_formatter_with_no_kwargs(self) -> None:
        """Formatter declaring no keyword params receives only data."""

        def _fmt(data: Any) -> str:
            return f"data={data}"

        result = call_markdown_formatter(_fmt, "x", extra={"a": 1})
        assert result == "data=x"

    def test_formatter_with_extra(self) -> None:
        """Formatter declaring ``extra`` receives it."""

        def _fmt(data: Any, *, extra: dict[str, Any] | None = None) -> str:
            return f"data={data} extra={extra}"

        result = call_markdown_formatter(_fmt, "x", extra={"ctx": "c"})
        assert result == "data=x extra={'ctx': 'c'}"

    def test_extra_defaults_to_none_when_omitted(self) -> None:
        """``extra`` defaults to None when the caller omits it."""

        def _fmt(data: Any, *, extra: dict[str, Any] | None = None) -> str:
            return f"extra={extra}"

        assert call_markdown_formatter(_fmt, "x") == "extra=None"

    def test_positional_only_param_not_treated_as_kwarg(self) -> None:
        """Only keyword-only params are dispatched; positional params are not."""

        def _fmt(data: Any, /, *, extra: dict[str, Any] | None = None) -> str:
            return f"{data}:{extra}"

        result = call_markdown_formatter(_fmt, "x", extra={"ctx": "c"})
        assert result == "x:{'ctx': 'c'}"

    def test_partial_fallback_signature(self) -> None:
        """A ``functools.partial`` binding schema dispatches correctly.

        Mirrors the pipeline's fallback
        ``functools.partial(format_as_markdown, schema=schema)``.  Note that
        ``inspect.signature`` on a partial keeps the bound ``schema`` as a
        keyword-only param — but since ``extra`` is not accepted, only
        ``data`` is passed.
        """
        import functools

        def _fmt(data: Any, schema: dict[str, Any] | None = None) -> str:
            return f"{data} via {schema}"

        partial = functools.partial(_fmt, schema={"type": "object"})
        assert call_markdown_formatter(partial, "x") == "x via {'type': 'object'}"

    def test_accepted_kwargs_returns_keyword_only_names(self) -> None:
        """``_accepted_kwargs`` returns exactly the keyword-only param names."""

        def _fmt(data: Any, *, extra: dict[str, Any] | None = None) -> str:
            return ""

        assert _accepted_kwargs(_fmt) == frozenset({"extra"})


class TestMarkdownFormatterContract:
    """The ``MarkdownFormatter`` alias is the canonical formatter contract."""

    def test_alias_is_callable_returning_str(self) -> None:
        """``MarkdownFormatter`` is ``Callable[..., str]`` — the contract type."""
        import typing
        from collections.abc import Callable as _Callable

        assert typing.get_origin(MarkdownFormatter) is _Callable
        assert typing.get_args(MarkdownFormatter) == (Ellipsis, str)

    def test_alias_is_exported(self) -> None:
        """``MarkdownFormatter`` is part of ``format.__all__`` (public contract)."""
        import gitea_mcp_server.format as _format_module

        assert "MarkdownFormatter" in _format_module.__all__


def _issue_spec() -> OpenAPISpec:
    """A spec with Issue (scalars + nested $ref user + $ref-list labels) + User/Label."""
    return make_openapi_spec(
        components={
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "login": {"type": "string"}},
                },
                "Label": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                },
                "Issue": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "user": {"$ref": "#/components/schemas/User"},
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                    },
                },
            }
        }
    )


class TestCollapseData:
    """Tests for collapse_data — walk data+schema, collapse $ref objects at depth>=1.

    This function shapes data for any formatter (json or markdown).
    """

    def test_full_detail_returns_unchanged(self) -> None:
        """detail='full' returns data unchanged regardless of schema."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"owner": {"$ref": "#/components/schemas/User"}}}
        result = collapse_data(data, schema, _depth=0, detail="full")
        assert result is data

    def test_depth_0_no_collapse(self) -> None:
        """At depth=0, the top-level dict is not collapsed, but
        $ref-backed properties at depth>=1 ARE collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"owner": {"$ref": "#/components/schemas/User"}}}
        result = collapse_data(data, schema, _depth=0, detail="concise")
        # Top-level dict stays as dict (not marker-replaced)
        assert isinstance(result, dict)
        assert "owner" in result
        # BUT the nested $ref property at depth 1 IS replaced by the marker
        assert result["owner"] == {"$ref": "User"}

    def test_depth_1_dict_with_ref_collapses(self) -> None:
        """At depth>=1, a dict with a $ref schema becomes the marker."""
        data = {"user": {"id": 1, "login": "user1"}}
        schema = {"type": "object", "properties": {"user": {"$ref": "#/components/schemas/User"}}}
        result = collapse_data(data, schema, _depth=0, detail="concise")
        assert result["user"] == {"$ref": "User"}

    def test_depth_1_list_with_ref_collapses(self) -> None:
        """At depth>=1, a list with $ref items becomes a marker with a count."""
        data = {"labels": [{"id": 1, "name": "bug"}, {"id": 2, "name": "feature"}]}
        schema = {
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"$ref": "#/components/schemas/Label"}},
            },
        }
        result = collapse_data(data, schema, _depth=0, detail="concise")
        assert result["labels"] == {"$ref": "Label", "count": 2}

    def test_inline_schema_not_collapsed(self) -> None:
        """Inline schemas (no $ref) are NOT collapsed — they remain as nested dicts."""
        data = {"config": {"host": "localhost", "port": 8080}}
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
                },
            },
        }
        result = collapse_data(data, schema, _depth=0, detail="concise")
        assert isinstance(result["config"], dict)
        assert result["config"]["host"] == "localhost"

    def test_allof_ref_collapses(self) -> None:
        """allOf with $ref is resolved and collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {"owner": {"allOf": [{"$ref": "#/components/schemas/User"}]}},
        }
        result = collapse_data(data, schema, _depth=0, detail="concise")
        assert result["owner"] == {"$ref": "User"}

    def test_anyof_ref_collapses(self) -> None:
        """anyOf with $ref is resolved and collapsed."""
        data = {"owner": {"id": 1, "login": "user1"}}
        schema = {
            "type": "object",
            "properties": {
                "owner": {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]},
            },
        }
        result = collapse_data(data, schema, _depth=0, detail="concise")
        assert result["owner"] == {"$ref": "User"}

    def test_none_no_collapse(self) -> None:
        """schema=None means no collapse occurs (data passed through)."""
        data = {"owner": {"id": 1, "login": "user1"}}
        result = collapse_data(data, None, _depth=0, detail="concise")
        assert result is data

    def test_nested_mixed(self) -> None:
        """Mixed $ref and inline schemas: only $ref properties collapse."""
        data = {
            "meta": {
                "owner": {"id": 1, "login": "user1"},
                "description": "a repo",
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {
                        "owner": {"$ref": "#/components/schemas/User"},
                        "description": {"type": "string"},
                    },
                },
            },
        }
        result = collapse_data(data, schema, _depth=0, detail="concise")
        meta = result["meta"]
        assert meta["owner"] == {"$ref": "User"}
        assert meta["description"] == "a repo"

    def test_list_at_depth_0_no_collapse(self) -> None:
        """Without a spec, top-level list items keep the whole-item marker fallback.

        Pre-#759 behavior is the documented fallback: the collapse can only
        summarize a root-list item when it can resolve the item ``$ref``,
        which needs the OpenAPI spec.  Callers that pass no spec (synthetic
        tools, unit callers) get one marker per root item.
        """
        data = [{"id": 1, "login": "user1"}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/User"}}
        result = collapse_data(data, schema, _depth=0, detail="concise")
        # List stays as list (not marker-replaced wholesale)
        assert isinstance(result, list)
        assert len(result) == 1
        # But nested items at depth>=1 ARE replaced by the marker (no spec → fallback)
        assert result[0] == {"$ref": "User"}

    def test_root_list_with_spec_summarizes_items(self) -> None:
        """#759: with a spec, concise summarizes root-list items.

        Scalars stay intact; nested ``$ref``-backed fields become the marker
        ``{"$ref": "TypeName"}``; nested lists become
        ``{"$ref": "TypeName", "count": N}``.  This is the S1-lite contract.
        """
        spec = _issue_spec()
        data = [
            {
                "number": 1,
                "title": "Bug",
                "body": "long text...",
                "user": {"id": 1, "login": "dev2"},
                "labels": [{"id": 1, "name": "Kind/Bug"}],
            },
        ]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        item = result[0]
        assert isinstance(item, dict)
        assert item["number"] == 1
        assert item["title"] == "Bug"
        assert item["body"] == "long text..."  # scalar intact
        assert item["user"] == {"$ref": "User"}  # nested ref becomes marker
        assert item["labels"] == {"$ref": "Label", "count": 1}  # nested list marker

    def test_root_list_no_truncation(self) -> None:
        """Scalars pass through verbatim — the collapse never truncates (#759 AC)."""
        long_body = "x" * 10_000
        spec = _issue_spec()
        data = [{"number": 1, "title": "t", "body": long_body, "user": {"id": 1, "login": "u"}}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result[0]["body"] == long_body

    def test_root_list_alias_chain_resolved(self) -> None:
        """A $ref item whose target is itself a bare $ref is chased one hop at a time."""
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Alias": {"$ref": "#/components/schemas/Real"},
                    "Real": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "who": {"$ref": "#/components/schemas/User"},
                        },
                    },
                    "User": {"type": "object", "properties": {"login": {"type": "string"}}},
                }
            }
        )
        data = [{"id": 7, "who": {"login": "u"}}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Alias"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result[0] == {"id": 7, "who": {"$ref": "User"}}

    def test_root_list_allof_alias_chased(self) -> None:
        """A combinator-wrapped alias target is chased, not mislabeled.

        The chase uses the same ``$ref`` notion as the collapse walker
        (``_extract_ref``), so an ``allOf``-wrapped reference resolves to the
        concrete schema and the item is *summarized*.  Before this, the
        resolver stopped at the wrapper and the walker's own allOf check
        label-replaced the item — a silent degrade to pre-#759 behavior.
        """
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Alias": {"allOf": [{"$ref": "#/components/schemas/Real"}]},
                    "Real": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "who": {"$ref": "#/components/schemas/User"},
                        },
                    },
                    "User": {"type": "object", "properties": {"login": {"type": "string"}}},
                }
            }
        )
        data = [{"id": 7, "who": {"login": "u"}}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Alias"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result[0] == {"id": 7, "who": {"$ref": "User"}}

    def test_root_list_allof_cycle_falls_back(self) -> None:
        """A cycle routed through a combinator hits the hop cap → fallback label."""
        spec = make_openapi_spec(
            components={"schemas": {"Loop": {"allOf": [{"$ref": "#/components/schemas/Loop"}]}}}
        )
        data = [{"a": 1}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Loop"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result == [{"$ref": "Loop"}]

    def test_root_list_allof_merge_passes_extension_through(self) -> None:
        """A genuine allOf merge is chased to its first $ref member; the other
        members' keys carry no property schema and pass through verbatim.

        Documents the deliberate trade-off: the summary may be slightly
        fatter (uncollapsed extension values) but never emptier than the
        whole-item label the old code produced for this shape.
        """
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "Base": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "who": {"$ref": "#/components/schemas/User"},
                        },
                    },
                    "Merged": {
                        "allOf": [
                            {"$ref": "#/components/schemas/Base"},
                            {
                                "type": "object",
                                "properties": {"extra": {"$ref": "#/components/schemas/User"}},
                            },
                        ]
                    },
                    "User": {"type": "object", "properties": {"login": {"type": "string"}}},
                }
            }
        )
        data = [{"id": 7, "who": {"login": "u"}, "extra": {"login": "x"}}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Merged"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result[0] == {
            "id": 7,
            "who": {"$ref": "User"},  # Base's nested ref becomes marker
            "extra": {"login": "x"},  # extension key: no schema → verbatim
        }

    def test_root_list_ref_cycle_falls_back(self) -> None:
        """A self-referential alias hits the hop cap → whole-item label fallback."""
        spec = make_openapi_spec(
            components={"schemas": {"Loop": {"$ref": "#/components/schemas/Loop"}}}
        )
        data = [{"a": 1}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Loop"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result == [{"$ref": "Loop"}]

    def test_root_list_missing_ref_falls_back(self) -> None:
        """A pointer missing from the spec → whole-item marker fallback."""
        spec = _issue_spec()
        data = [{"a": 1}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Nope"}}
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result == [{"$ref": "Nope"}]

    def test_root_list_anyof_ref_with_spec_summarizes(self) -> None:
        """anyOf-wrapped item refs resolve via the spec too."""
        spec = _issue_spec()
        schema = {
            "type": "array",
            "items": {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]},
        }
        data = [{"id": 1, "login": "x"}]
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        # User has no nested $ref fields — the summary is the full scalar dict
        assert result == [{"id": 1, "login": "x"}]

    def test_root_list_inline_items_ignore_spec(self) -> None:
        """No $ref at the item root → the spec is not consulted; behavior unchanged."""
        spec = _issue_spec()
        schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"host": {"type": "string"}}},
        }
        data = [{"host": "a"}]
        result = collapse_data(data, schema, _depth=0, detail="concise", openapi_spec=spec)
        assert result == [{"host": "a"}]

    def test_detail_full_ignores_spec(self) -> None:
        """detail='full' returns unchanged even when a spec is supplied."""
        spec = _issue_spec()
        data = [{"user": {"id": 1}}]
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}
        result = collapse_data(data, schema, _depth=0, detail="full", openapi_spec=spec)
        assert result is data

    def test_non_dict_prop_schema_guarded(self) -> None:
        """When a property schema is not a dict, set to None instead of crashing."""
        data = {"labels": [{"id": 1, "name": "bug"}]}
        # Deliberately pass a non-dict value for a property schema
        schema = {
            "type": "object",
            "properties": {"labels": "not_a_dict"},
        }
        result = collapse_data(data, schema, _depth=0, detail="concise")
        # The non-dict prop_schema is treated as None — data passes through unchanged
        assert isinstance(result, dict)
        assert "labels" in result


class TestResolveAnyOfSchema:
    def test_none_returns_none(self) -> None:
        assert _resolve_anyof_schema(None) is None

    def test_anyof_with_object_returns_first_object(self) -> None:
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"
        assert "id" in result["properties"]

    def test_anyof_only_scalars_returns_original(self) -> None:
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_oneof_with_object_returns_first_object(self) -> None:
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "object", "properties": {"name": {"type": "string"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"

    def test_anyof_object_no_properties_skipped(self) -> None:
        schema = {
            "anyOf": [
                {"type": "object"},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert "id" in result["properties"]

    def test_no_anyof_or_oneof(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_empty_list(self) -> None:
        schema: dict[str, Any] = {"anyOf": []}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_only_non_dict_items(self) -> None:
        schema = {"anyOf": ["string", 42]}
        result = _resolve_anyof_schema(schema)
        assert result is schema

    def test_anyof_object_with_null_properties(self) -> None:
        schema = {
            "anyOf": [
                {"type": "object", "properties": None},
                {"type": "object", "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == "object"
        assert "id" in result["properties"]

    def test_anyof_object_with_type_list(self) -> None:
        """Type-as-list form (``type: [\"object\", \"null\"]``) is handled correctly."""
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": ["object", "null"], "properties": {"id": {"type": "integer"}}},
            ]
        }
        result = _resolve_anyof_schema(schema)
        assert result is not None
        assert result["type"] == ["object", "null"]
        assert "id" in result["properties"]


class TestFormatAsMarkdown:
    def test_none_input(self) -> None:
        result = format_as_markdown(None)
        assert result == "N/A"

    def test_none_input_with_title(self) -> None:
        result = format_as_markdown(None, title="Test")
        assert "Test" in result
        assert "N/A" in result

    def test_scalar_value(self) -> None:
        result = format_as_markdown("hello")
        assert result == "hello"

    def test_integer_scalar(self) -> None:
        result = format_as_markdown(42)
        assert result == "42"

    def test_empty_list(self) -> None:
        result = format_as_markdown([])
        assert "_(empty)_" in result

    def test_list_of_scalars_with_schema(self) -> None:
        schema = {"type": "array", "items": {"type": "string"}}
        result = format_as_markdown(["a", "b", "c"], schema)
        assert "a, b, c" in result

    def test_list_of_scalars_no_schema(self) -> None:
        result = format_as_markdown(["a", "b"])
        assert "- a" in result
        assert "- b" in result

    def test_list_of_dicts(self) -> None:
        data = [{"name": "Alice"}, {"name": "Bob"}]
        result = format_as_markdown(data)
        assert "Alice" in result
        assert "Bob" in result
        assert "| Name |" in result

    def test_list_of_dicts_with_schema(self) -> None:
        data = [{"id": 1, "name": "Foo"}]
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                },
            },
        }
        result = format_as_markdown(data, schema)
        assert "Foo" in result
        assert "1" in result or "1" in result

    def test_empty_dict(self) -> None:
        result = format_as_markdown({})
        assert "*Empty*" in result

    def test_dict_with_properties_renders_table(self) -> None:
        data = {"name": "test", "count": 3}
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
            },
        }
        result = format_as_markdown(data, schema)
        assert "| Property | Value |" in result
        assert "Name" in result or "name" in result
        assert "test" in result
        assert "Count" in result or "count" in result
        assert "3" in result

    def test_dict_with_nested_dict_section(self) -> None:
        data = {"profile": {"age": 30}}
        schema = {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "properties": {"age": {"type": "integer"}},
                }
            },
        }
        result = format_as_markdown(data, schema)
        assert "**Profile:**" in result or "Profile" in result

    def test_dict_with_nested_list(self) -> None:
        data = {"items": [{"x": 1}, {"x": 2}]}
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"x": {"type": "integer"}}},
                }
            },
        }
        result = format_as_markdown(data, schema)
        assert "Items" in result or "items" in result

    def test_title_at_top_level(self) -> None:
        result = format_as_markdown("hello", title="MyTitle")
        assert "MyTitle" in result
        assert "hello" in result

    def test_title_at_top_level_for_scalar(self) -> None:
        result = format_as_markdown(42, title="The Answer")
        assert "The Answer" in result
        assert "42" in result

    def test_title_with_dict(self) -> None:
        result = format_as_markdown({"key": "val"}, title="MyTitle")
        assert "# MyTitle" in result
        assert "| Key | val |" in result
        assert "val" in result

    def test_title_with_list(self) -> None:
        result = format_as_markdown(["a", "b"], title="MyTitle")
        assert "# MyTitle" in result
        assert "a" in result
        assert "b" in result

    def test_allof_merged_schema(self) -> None:
        data = {"title": "Issue", "body": "Text"}
        schema = {
            "type": "object",
            "allOf": [
                {"properties": {"title": {"type": "string"}}},
                {"properties": {"body": {"type": "string"}}},
            ],
        }
        result = format_as_markdown(data, schema)
        assert "Title" in result
        assert "Body" in result

    def test_allof_without_properties(self) -> None:
        data = {"a": 1}
        schema = {"type": "object", "allOf": [{"type": "object"}]}
        result = format_as_markdown(data, schema)
        assert "a" in result or "A" in result

    def test_properties_without_schema_flat(self) -> None:
        data = {"key": "val"}
        result = format_as_markdown(data)
        assert "|" in result

    def test_datetime_property_formatted(self) -> None:
        data = {"created_at": "2024-01-01T12:00:00Z"}
        schema = {
            "type": "object",
            "properties": {"created_at": {"type": "string", "format": "date-time"}},
        }
        result = format_as_markdown(data, schema)
        assert "2024-01-01" in result
        assert "12:00:00" in result

    def test_anyof_resolved_in_properties(self) -> None:
        data = {"owner": {"login": "user"}}
        schema = {
            "type": "object",
            "properties": {
                "owner": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "object", "properties": {"login": {"type": "string"}}},
                    ]
                }
            },
        }
        result = format_as_markdown(data, schema)
        assert "user" in result

    def test_nested_section_with_depth(self) -> None:
        """Nested section at depth>0 uses indent-bold format."""
        data = {
            "config": {
                "database": {
                    "host": "localhost",
                    "port": 5432,
                }
            }
        }
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "database": {
                            "type": "object",
                            "properties": {
                                "host": {"type": "string"},
                                "port": {"type": "integer"},
                            },
                        }
                    },
                }
            },
        }
        result = format_as_markdown(data, schema)
        # Should contain the bold label format at depth > 0
        assert "Host" in result or "Port" in result or "database" in result

    def test_dict_concise_collapses_nested_at_depth(self) -> None:
        """The formatter renders already-collapsed data — it does not collapse.

        Collapsing is the display pipeline's job (``collapse_data``); the
        formatter receives already-collapsed data (nested ``$ref``-backed
        objects are the canonical ``{"$ref": "TypeName"}`` marker) and renders
        them as-is.
        """
        # Outer wrapper pushes 'owner' and 'repo' to _depth=1
        data = {
            "details": {
                "owner": {"id": 1, "login": "user1"},
                "repo": {"id": 10, "name": "my-repo"},
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "details": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "allOf": [{"$ref": "#/components/schemas/User"}],
                        },
                        "repo": {
                            "anyOf": [
                                {"$ref": "#/components/schemas/Repository"},
                                {"type": "null"},
                            ],
                        },
                    },
                },
            },
        }
        collapsed = collapse_data(data, schema, _depth=0, detail="concise")
        result = format_as_markdown(collapsed, schema)
        # The collapsed $ref labels render in the markdown
        assert "$ref:User" in result
        assert "$ref:Repository" in result
        # Original values are gone — collapsed before the formatter ran
        assert "user1" not in result
        assert "my-repo" not in result

    def test_dict_concise_collapses_list_at_depth(self) -> None:
        """The formatter renders already-collapsed lists — it does not collapse."""
        data = {
            "nested": {
                "labels": [{"id": 1, "name": "bug"}, {"id": 2, "name": "feature"}],
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                    },
                },
            },
        }
        collapsed = collapse_data(data, schema, _depth=0, detail="concise")
        result = format_as_markdown(collapsed, schema)
        assert "$ref:Label[2]" in result
        assert "bug" not in result
        assert "feature" not in result

    def test_dict_concise_top_level_stays_expanded(self) -> None:
        """The formatter renders un-collapsed data as-is (no collapse at any depth).

        Collapsing is the pipeline's job; the formatter renders whatever data
        it receives.  Un-collapsed nested objects render as sections.
        """
        data = {
            "user": {"id": 1, "login": "testuser"},
        }
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "login": {"type": "string"}},
                },
            },
        }
        result = format_as_markdown(data, schema)
        # Nested objects render as sections, not collapsed
        assert "testuser" in result

    def test_property_schema_not_a_dict_skipped(self) -> None:
        """Property schema that is not a dict is skipped gracefully."""
        data = {
            "name": "test",
            "ref": "abc123",
        }
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "ref": "$ref: #/components/schemas/Ref",
            },
        }
        # If ref prop schema is not a dict (it's a string), it should be skipped
        result = format_as_markdown(data, schema)
        assert "Name" in result

    def test_non_dict_non_list_input(self) -> None:
        assert format_as_markdown(True) == "True"

    # ── field_filter and item_title_key hooks ──────────────────────────────────────

    def test_field_filter_on_dict_selects_subset(self) -> None:
        """field_filter shows only the specified properties."""
        data = {"id": 1, "name": "Alice", "email": "alice@test.com", "role": "admin"}
        result = format_as_markdown(data, field_filter={"id": {}, "name": {}})
        assert "| Id | 1 |" in result
        assert "| Name | Alice |" in result
        assert "Email" not in result
        assert "Role" not in result

    def test_field_filter_on_list_of_dicts(self) -> None:
        """field_filter applies to each item in a list of dicts."""
        data = [
            {"id": 1, "name": "Foo", "extra": "x"},
            {"id": 2, "name": "Bar", "extra": "y"},
        ]
        result = format_as_markdown(data, field_filter={"id": {}, "name": {}})
        for row in ("Foo", "Bar", "1", "2"):
            assert row in result
        assert "extra" not in result.lower()
        assert "Extra" not in result

    def test_field_filter_skips_missing_keys_gracefully(self) -> None:
        """field_filter entries not in data are silently skipped."""
        data = {"name": "Alice"}
        result = format_as_markdown(data, field_filter={"name": {}, "nonexistent": {}})
        assert "| Name | Alice |" in result
        assert "nonexistent" not in result.lower()

    def test_item_title_key_customizes_list_headings(self) -> None:
        """item_title_key uses the specified field value as the item heading."""
        data = [{"number": 42, "title": "Bug fix"}, {"number": 43, "title": "Feature"}]
        result = format_as_markdown(data, item_title_key="title")
        assert "# Bug fix" in result
        assert "# Feature" in result
        assert "| Number | 42 |" in result
        assert "| Number | 43 |" in result

    def test_item_title_key_falls_back_to_item_n_when_missing(self) -> None:
        """When item_title_key field is missing, falls back to 'Item N'."""
        data = [{"id": 1, "name": "Alice"}]
        result = format_as_markdown(data, item_title_key="nonexistent")
        assert "# Item 1" in result
        assert "| Id | 1 |" in result

    def test_field_filter_and_item_title_key_together(self) -> None:
        """Both hooks can be used together."""
        data = [{"number": 1, "title": "Bug", "body": "Details"}]
        result = format_as_markdown(
            data,
            field_filter={"number": {}, "title": {}},
            item_title_key="title",
        )
        assert "# Bug" in result
        assert "| Number | 1 |" in result
        assert "| Title | Bug |" in result
        assert "Body" not in result
        assert "body" not in result

    # ── Consistency: tool and resource should produce same structure ──────────────

    def test_format_produces_nested_sub_tables_for_nested_objects(self) -> None:
        """Nested dicts render as bold sub-sections with sub-tables (not dot-path)."""
        data = {"user": {"id": 12, "login": "dev2"}, "labels": [{"name": "Cleanup"}]}
        result = format_as_markdown(data)
        # User appears as a nested sub-section, not as dot-path keys
        assert "**User:**" in result or "## User" in result
        # Labels appears as a nested section
        assert "**Labels:**" in result or "## Labels" in result
        # Dot-path keys should NOT appear
        assert "user.id" not in result
        assert "labels.Name" not in result

    # ── Field-level render hints: compact_ref, badge ──────────────────────────────

    def test_compact_ref_renders_dict_as_flat_row(self) -> None:
        """compact_ref renders a nested dict as a flat table row using template."""
        data = {"base": {"owner": "org", "repo": "myrepo", "branch": "main"}}
        field_filter = {
            "base": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        # base appears as a flat table row, not a nested sub-section
        assert "| Base | org/myrepo:main |" in result
        assert "## Base" not in result

    def test_compact_ref_at_full_detail(self) -> None:
        """compact_ref renders a flat row regardless of detail level."""
        data = {
            "name": "PR-42",
            "head": {"owner": "fork", "repo": "fork-repo", "branch": "feature-x"},
        }
        field_filter = {
            "name": {},
            "head": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Head | fork/fork-repo:feature-x |" in result
        assert "## Head" not in result

    def test_compact_ref_at_concise_detail(self) -> None:
        """compact_ref renders a flat row regardless of detail level."""
        data = {
            "name": "PR-42",
            "head": {"owner": "fork", "repo": "fork-repo", "branch": "feature-x"},
        }
        field_filter = {
            "name": {},
            "head": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Head | fork/fork-repo:feature-x |" in result

    def test_compact_ref_fallback_on_missing_template_key(self) -> None:
        """When template.format() fails, compact_ref falls back to str()."""
        data = {"base": {"label": "main"}}
        field_filter = {
            "base": {"render": "compact_ref", "template": "{owner}/{repo}:{branch}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        # Should not crash; falls back to str representation
        assert "| Base | {'label': 'main'}" in result or "| Base |" in result
        assert "## Base" not in result

    def test_badge_yes_for_truthy(self) -> None:
        """badge renders truthy values as 'Yes'."""
        data = {"active": True, "name": "test"}
        field_filter = {"active": {"render": "badge"}, "name": {}}
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Active | Yes |" in result

    def test_badge_no_for_falsy(self) -> None:
        """badge renders falsy values as 'No'."""
        data = {"active": False, "pull_request": None}
        field_filter = {"active": {"render": "badge"}, "pull_request": {"render": "badge"}}
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Active | No |" in result
        assert "| Pull Request | No |" in result

    def test_badge_with_dict_value(self) -> None:
        """badge with a dict (present) renders as 'Yes'."""
        data = {"pull_request": {"url": "https://example.com/pr/1"}}
        field_filter = {"pull_request": {"render": "badge"}}
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Pull Request | Yes |" in result
        # Should NOT expand the nested dict
        assert "url" not in result.lower()
        assert "Url" not in result

    def test_expand_default_renders_nested_as_section(self) -> None:
        """Default render='expand' preserves existing nested-section behavior."""
        data: dict[str, Any] = {"user": {"id": 1, "login": "dev"}}
        field_filter: dict[str, Any] = {"user": {}}
        result = format_as_markdown(data, field_filter=field_filter)
        assert "## User" in result or "**User:**" in result
        assert "dev" in result

    # ── List compact_ref ──────────────────────────────────────────────────────────

    def test_compact_ref_on_list_renders_comma_separated(self) -> None:
        """compact_ref on a list renders comma-separated template values."""
        data = {"labels": [{"name": "bug"}, {"name": "enhancement"}]}
        field_filter = {
            "labels": {"render": "compact_ref", "template": "{name}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Labels | bug, enhancement |" in result
        assert "## Labels" not in result

    def test_compact_ref_on_list_single_item(self) -> None:
        """compact_ref on a single-element list works correctly."""
        data = {"labels": [{"name": "bug"}]}
        field_filter = {
            "labels": {"render": "compact_ref", "template": "{name}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Labels | bug |" in result

    def test_compact_ref_on_list_empty(self) -> None:
        """compact_ref on an empty list renders empty string."""
        data: dict[str, Any] = {"labels": []}
        field_filter: dict[str, Any] = {
            "labels": {"render": "compact_ref", "template": "{name}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Labels |  |" in result

    def test_compact_ref_on_list_fallback_on_missing_key(self) -> None:
        """When template is missing a key, compact_ref list falls back to str()."""
        data = {"items": [{"id": 1}, {"id": 2}]}
        field_filter = {
            "items": {"render": "compact_ref", "template": "{name}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        # Should not crash; falls back to str representation
        assert "| Items |" in result

    def test_compact_ref_on_list_non_dict_items(self) -> None:
        """compact_ref on a list of scalars renders each as str."""
        data = {"tags": ["alpha", "beta"]}
        field_filter = {
            "tags": {"render": "compact_ref", "template": "{name}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Tags | alpha, beta |" in result

    def test_ref_marker_precedes_render_hints(self) -> None:
        """A $ref marker renders as its label even on a compact_ref field.

        Marker detection must precede render hints: a ``compact_ref`` template
        (e.g. ``{login}``/``{ref}``) has no matching key on a marker and would
        otherwise leak a Python repr into the table (#763).
        """
        data = {
            "base": {"$ref": "FakeRef"},
        }
        field_filter = {
            "base": {"render": "compact_ref", "template": "{ref}"},
        }
        result = format_as_markdown(data, field_filter=field_filter)
        assert "| Base | $ref:FakeRef |" in result
        assert "{'$ref'" not in result

    def test_dollar_ref_flattened_on_expand_path(self) -> None:
        """$ref flattening still works on the default expand path."""
        data = {
            "user": {"$ref": "User"},
        }
        result = format_as_markdown(data)
        # Without explicit render hints, the $ref dict is flattened
        assert "$ref:User" in result


class TestFormatType:
    """Tests for _format_type — type enrichment with enum/array info."""

    def test_plain_type_unchanged(self) -> None:
        """No enum, no array items — returns basic type."""
        assert _format_type({"type": "string"}) == "string"
        assert _format_type({"type": "integer"}) == "integer"
        assert _format_type({"type": "boolean"}) == "boolean"

    def test_fallback_when_no_type(self) -> None:
        """No type key — returns 'any'."""
        assert _format_type({}) == "any"

    def test_enum_appends_values(self) -> None:
        """Enum values appear as type [val1, val2, ...]."""
        prop = {"type": "string", "enum": ["merge", "rebase", "squash"]}
        assert _format_type(prop) == "string [merge, rebase, squash]"

    def test_enum_with_integer_values(self) -> None:
        """Non-string enum values are stringified."""
        prop = {"type": "integer", "enum": [1, 2, 3]}
        assert _format_type(prop) == "integer [1, 2, 3]"

    def test_array_with_items_properties(self) -> None:
        """Array with items.properties shows array of {key1, key2}."""
        prop = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        }
        assert _format_type(prop) == "array of {operation, path, content}"

    def test_array_without_items(self) -> None:
        """Array without items schema — unchanged."""
        assert _format_type({"type": "array"}) == "array"

    def test_array_with_items_no_properties(self) -> None:
        """Array items with no properties — unchanged."""
        prop = {"type": "array", "items": {"type": "string"}}
        assert _format_type(prop) == "array"

    def test_enum_takes_priority_over_array(self) -> None:
        """When both enum and array are present, enum wins."""
        prop = {
            "type": "array",
            "enum": ["create", "update", "delete"],
            "items": {"type": "string"},
        }
        assert _format_type(prop) == "array [create, update, delete]"

    def test_type_list_extracts_non_null_type(self) -> None:
        """``type: ["array", "null"]`` should display as ``"array"``."""
        prop = {"type": ["array", "null"]}
        assert _format_type(prop) == "array"

    def test_type_list_with_items_properties(self) -> None:
        """``type: ["array", "null"]`` with items.properties shows array of {...}."""
        prop = {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "path": {"type": "string"},
                },
            },
        }
        assert _format_type(prop) == "array of {operation, path}"

    def test_type_list_with_enum(self) -> None:
        """``type: ["string", "null"]`` with enum appends enum values."""
        prop = {"type": ["string", "null"], "enum": ["open", "closed"]}
        assert _format_type(prop) == "string [open, closed]"

    def test_type_list_all_null(self) -> None:
        """``type: ["null"]`` returns ``"null"`` — the only type present."""
        assert _format_type({"type": ["null"]}) == "null"


class TestFormatParameterTable:
    """Tests for _format_parameter_table — the markdown parameter table."""

    def test_plain_params(self) -> None:
        """Basic string/integer params render without enrichment."""
        props = {
            "owner": {"type": "string", "description": "owner of the repo"},
            "index": {"type": "integer", "description": "issue index"},
        }
        result = _format_parameter_table(props, ["owner", "index"])
        assert "| owner | string | yes | owner of the repo |" in result
        assert "| index | integer | yes | issue index |" in result
        assert "## Parameters" in result

    def test_enum_param(self) -> None:
        """Enum param shows values in type column."""
        props = {
            "Do": {
                "type": "string",
                "enum": ["merge", "rebase", "squash"],
            },
        }
        result = _format_parameter_table(props, ["Do"])
        assert "| Do | string [merge, rebase, squash] | yes |  |" in result

    def test_array_param(self) -> None:
        """Array param with items.properties shows item keys."""
        props = {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string"},
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
                "description": "list of file operations",
            },
        }
        result = _format_parameter_table(props, ["files"])
        assert (
            "| files | array of {operation, path, content} | yes | list of file operations |"
            in result
        )

    def test_optional_param(self) -> None:
        """Non-required param gets 'no' in Required column."""
        props = {
            "message": {"type": "string", "description": "commit message"},
        }
        result = _format_parameter_table(props, [])
        assert "| message | string | no | commit message |" in result

    def test_description_escapes_pipe(self) -> None:
        """Pipe characters in description are escaped."""
        props = {
            "owner": {"type": "string", "description": "owner|repo"},
        }
        result = _format_parameter_table(props, ["owner"])
        assert r"| owner | string | yes | owner\|repo |" in result

    def test_invalid_prop_skipped(self) -> None:
        """Non-dict properties are skipped without error."""
        props = {"bad": "not a dict"}
        result = _format_parameter_table(props, [])
        assert "bad" not in result
        assert "## Parameters" in result

    def test_empty_properties(self) -> None:
        """Empty properties produces header with no data rows."""
        result = _format_parameter_table({}, [])
        assert "## Parameters" in result
        assert "Parameter | Type | Required | Description" in result
        # No data row below the separator
        header_end = result.index("|-----------")
        rest = result[header_end:]
        # Only blank line after separator, no `| owner |` etc.
        assert rest.strip() == "|-----------|------|----------|-------------|"


# ============================================================================
# ============================================================================
# Dual-channel contract (issue #718)
# ============================================================================


class TestDualChannelContract:
    """Contract tests for the dual-channel result shape.

    The MCP spec makes ``content`` the guaranteed channel of a tool result;
    ``structured_content`` is an optional mirror that duplicates it.  These
    tests assert that contract via ``assert_dual_channel``.  The single
    result pipeline is the single writer of both channels, so every
    path holds the contract.
    """

    def test_paginated_json_envelope_in_text(self) -> None:
        """Paginated format=json must carry the envelope beside result in the text."""
        items = [{"id": i} for i in range(25)]
        result = render(
            ExecutionResult(data=items, total_count=25, shape="list", paginated=True),
            fmt="json",
            page=1,
            limit=10,
        )
        assert_dual_channel(result, fmt="json")

    def test_markdown_dual_channel(self) -> None:
        """Markdown output satisfies the contract: content present, result in structured."""
        result = render(
            ExecutionResult(
                data=[{"id": 1, "name": "test"}],
                total_count=1,
                shape="list",
                paginated=True,
            ),
            fmt="markdown",
            page=1,
            limit=10,
        )
        sc = assert_dual_channel(result, fmt="markdown")
        assert sc["total_count"] == 1

    def test_empty_page_dual_channel(self) -> None:
        """Empty/out-of-range pages satisfy the contract: content present, envelope in structured."""
        result = render(
            ExecutionResult(
                data=[],
                total_count=0,
                shape="empty",
                paginated=True,
                message="No results found for 'x'.",
            ),
            fmt="markdown",
            page=1,
            limit=10,
        )
        sc = assert_dual_channel(result, fmt="markdown")
        assert sc["result"] == []
        assert sc["has_more"] is False
        assert sc["total_count"] == 0

    def test_empty_page_json_shape(self) -> None:
        """Empty page format=json carries result/message/envelope as JSON text.

        The empty-json shape is ``{"result": [], "message": "...",
        "has_more": false, "next_offset": null, "total_count": N}`` — the
        text is JSON, mirroring structured_content.
        """
        result = render(
            ExecutionResult(
                data=[],
                total_count=0,
                shape="empty",
                paginated=True,
                message="No results found for 'x'.",
            ),
            fmt="json",
            page=1,
            limit=10,
        )
        assert_dual_channel(result, fmt="json")
        parsed = parse_json_content(result)
        assert parsed == {
            "result": [],
            "message": "No results found for 'x'.",
            "has_more": False,
            "next_offset": None,
            "total_count": 0,
        }


class TestFormatDateTime:
    """Tests for _format_datetime."""

    def test_formats_iso_datetime(self) -> None:
        """Test ISO datetime string is formatted correctly."""
        dt = "2024-01-15T10:30:00Z"
        result = _format_datetime(dt)
        assert result == "2024-01-15 10:30:00 UTC"

    def test_handles_none(self) -> None:
        """Test None returns N/A."""
        assert _format_datetime(None) == "N/A"

    def test_handles_empty_string(self) -> None:
        """Test empty string returns N/A."""
        assert _format_datetime("") == "N/A"

    def test_handles_invalid_format(self) -> None:
        """Test invalid format returns original string."""
        assert _format_datetime("not a date") == "not a date"


class TestFormatListAsMarkdownRef:
    """Tests for _format_list_as_markdown with $ref-flattened data."""

    def test_ref_list_renders_bulleted_refs(self) -> None:
        """List of {"$ref": "Type"} dicts renders as bulleted $ref:Type items."""
        from gitea_mcp_server.format import _format_list_as_markdown

        data = [{"$ref": "User"}, {"$ref": "Repo"}]
        result = _format_list_as_markdown(data)
        assert "$ref:User" in result
        assert "$ref:Repo" in result
        assert "- $ref:User" in result
        assert "- $ref:Repo" in result


class TestFormatDictAsMarkdownEmptyFieldFilter:
    """Tests for _format_dict_as_markdown with empty field_filter."""

    def test_empty_field_filter_falls_back_to_flat_table(self) -> None:
        """When field_filter is empty but data exists, renders flat table."""
        from gitea_mcp_server.format import _format_dict_as_markdown

        data = {"a": 1, "b": 2}
        result = _format_dict_as_markdown(data, field_filter={})
        assert "| Property | Value |" in result
        assert "| a | 1 |" in result
        assert "| b | 2 |" in result


class TestFormatToolInfoMarkdown:
    """Tests for format_tool_info_markdown."""

    def test_output_schema_section_included(self) -> None:
        """When output_schema is present, 'Output Schema' section is rendered."""
        from gitea_mcp_server.format import format_tool_info_markdown

        schema: ToolSchemaResult = {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {"properties": {"x": {"type": "string"}}, "required": []},
            "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
        }
        result = format_tool_info_markdown(schema)
        assert "## Output Schema" in result
        assert "type" in result
        assert "object" in result
