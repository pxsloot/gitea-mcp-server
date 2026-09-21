"""Tests for the display layer.

Covers:
    - the type-binding registry (``register_formatter(types=...)``)
    - the generic schema-anchored collection view (``format._generic_collection_view``)
    - the bespoke ``labels`` formatter (``tools/display.py``)
    - shape tolerance: collection (list) vs detail (dict) views
    - formatter edge cases
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from gitea_mcp_server.format import (
    _FORMATTERS,
    _TYPE_FORMATTERS,
    _generic_collection_view,
    build_server_info_markdown,
    format_as_markdown,
    get_formatter_for_type,
    register_formatter,
)
from gitea_mcp_server.models import ViewHints

if TYPE_CHECKING:
    from collections.abc import Generator

    from gitea_mcp_server.openapi_types import OpenAPISpec

from gitea_mcp_server.tools.display import (
    _format_labels_markdown,
)
from tests.helpers.spec_fixtures import make_openapi_spec


@pytest.fixture(autouse=True)
def _clean_formatters() -> Generator[None, None, None]:
    """Save and restore the global formatter registries around each test.

    Tests register ad-hoc formatters via ``@register_formatter`` which
    mutates the module-level ``_FORMATTERS`` and (with ``types=``)
    ``_TYPE_FORMATTERS`` dicts.  This fixture ensures each test starts with
    a clean slate and does not leak registrations to subsequent tests.
    """
    saved_formatters = dict(_FORMATTERS)
    saved_types = dict(_TYPE_FORMATTERS)
    yield
    _FORMATTERS.clear()
    _FORMATTERS.update(saved_formatters)
    _TYPE_FORMATTERS.clear()
    _TYPE_FORMATTERS.update(saved_types)


def _spec_with_type(
    type_name: str,
    properties: dict[str, Any],
) -> OpenAPISpec:
    """Build a spec with one component type and one operation returning it.

    The operation carries the converter-stamped ``x-response-type`` — the
    registration-time type-binding key.  Display-view hints are no longer
    stamped on operations; they travel in ``view_hints`` (#775), built in
    tests via :func:`_hints`.
    """
    operation: dict[str, Any] = {
        "operationId": f"list{type_name}s",
        "x-response-type": type_name,
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": f"#/components/schemas/{type_name}"},
                        }
                    }
                }
            }
        },
    }
    return make_openapi_spec(
        paths={f"/{type_name.lower()}s": {"get": operation}},
        components={"schemas": {type_name: {"type": "object", "properties": properties}}},
    )


def _hints(
    *,
    omit: list[str] | None = None,
    compact: dict[str, str | None] | None = None,
    flag: list[str] | None = None,
) -> ViewHints:
    """Build a ``ViewHints`` dict as registration would resolve it."""
    return ViewHints(
        omit=list(omit or []),
        compact=dict(compact or {}),
        flag=list(flag or []),
    )


def _render(
    data: Any,
    *,
    response_type: str | None,
    openapi_spec: OpenAPISpec | None,
    extra: dict[str, Any] | None = None,
    omit: list[str] | None = None,
    compact: dict[str, str | None] | None = None,
    flag: list[str] | None = None,
) -> str:
    """Call the generic view with the hints registration would pass.

    ``view_hints`` is required on ``_generic_collection_view`` so a wiring
    omission fails loudly; this helper is the test call site that supplies it.
    """
    hints = _hints(omit=omit, compact=compact, flag=flag) if (omit or compact or flag) else None
    return _generic_collection_view(
        data,
        response_type=response_type,
        openapi_spec=openapi_spec,
        view_hints=hints,
        extra=extra,
    )


class TestTypeBindingRegistry:
    """``register_formatter(types=...)`` populates the type index."""

    def test_types_populate_index(self) -> None:
        @register_formatter("thing", types=["Thing"])
        def _fmt(data: Any) -> str:
            return "thing!"

        assert _TYPE_FORMATTERS["Thing"] == "thing"
        assert get_formatter_for_type("Thing") is _fmt

    def test_multiple_types_bind_one_formatter(self) -> None:
        @register_formatter("who", types=["User", "Organization"])
        def _fmt(data: Any) -> str:
            return "who!"

        assert get_formatter_for_type("User") is _fmt
        assert get_formatter_for_type("Organization") is _fmt

    def test_unbound_type_returns_none(self) -> None:
        """Unregistered types keep the generic fallback (pipeline tier 3)."""
        assert get_formatter_for_type("Widget") is None

    def test_registered_name_without_types(self) -> None:
        """Plain ``register_formatter(name)`` binds nothing by type."""

        @register_formatter("hint_only")
        def _fmt(data: Any) -> str:
            return "x"

        assert "hint_only" in _FORMATTERS
        assert not any(v == "hint_only" for v in _TYPE_FORMATTERS.values())

    def test_shipped_domain_bindings(self) -> None:
        """Only the bespoke ``labels`` formatter is type-bound (#771).

        Every other type is rendered by the generic schema-anchored view, so
        there is no per-type formatter to drift from the schema.
        """
        assert _TYPE_FORMATTERS.get("Label") == "labels"
        for type_name in ("Issue", "PullRequest", "Repository", "User", "Organization", "Release"):
            assert get_formatter_for_type(type_name) is None

    def test_duplicate_type_binding_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Rebinding a type to a different formatter warns; last registration wins."""

        @register_formatter("first", types=["Dup"])
        def _first(data: Any) -> str:
            return "first"

        with caplog.at_level(logging.WARNING, logger="gitea_mcp_server.format"):

            @register_formatter("second", types=["Dup"])
            def _second(data: Any) -> str:
                return "second"

        assert "already bound" in caplog.text
        assert get_formatter_for_type("Dup") is _second


class TestGenericCollectionView:
    """The collection view derives from the schema, not a hand-written list."""

    def test_fields_come_from_schema_in_declaration_order(self) -> None:
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "count": {}, "active": {}},
        )
        data = [{"name": "a", "count": 2, "active": True}]
        result = _render(data, response_type="Widget", openapi_spec=spec)
        assert "Widgets - 1 items" in result
        assert "| Name | a |" in result
        assert "| Count | 2 |" in result
        assert "| Active | True |" in result

    def test_omit_hint_drops_fields(self) -> None:
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "url": {}, "internal_flag": {}},
        )
        data = [{"name": "a", "url": "http://x", "internal_flag": True}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            omit=["url", "internal_flag"],
        )
        assert "| Name | a |" in result
        assert "url" not in result.lower()
        assert "internal_flag" not in result.lower()

    def test_type_filter_retitles_issue_collection(self) -> None:
        """``extra.type == 'pulls'`` titles the issue collection Pull Requests.

        Regression for the review of PR #774: the generic view dropped the
        ``extra`` context, so ``?type=pulls`` mislabelled the list.
        """
        spec = _spec_with_type("Issue", {"number": {}, "title": {}, "state": {}})
        data = [{"number": 1, "title": "Bug", "state": "open"}]
        result = _render(data, response_type="Issue", openapi_spec=spec, extra={"type": "pulls"})
        assert "Pull Requests - 1 items" in result

    def test_issue_detail_title_uses_number_and_title(self) -> None:
        """An Issue detail read gets an ``Issue #N: title`` heading."""
        spec = _spec_with_type("Issue", {"number": {}, "title": {}})
        result = _render(
            {"number": 5, "title": "Bug", "body": "x"},
            response_type="Issue",
            openapi_spec=spec,
        )
        assert result.startswith("# Issue #5: Bug")

    def test_issue_detail_title_is_pull_request_when_pr(self) -> None:
        spec = _spec_with_type("Issue", {"number": {}, "title": {}, "pull_request": {}})
        result = _render(
            {"number": 3, "title": "Fix", "pull_request": {"merged": True}},
            response_type="Issue",
            openapi_spec=spec,
        )
        assert result.startswith("# Pull Request #3: Fix")

    def test_compact_hint_renders_identity(self) -> None:
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "owner": {"$ref": "#/components/schemas/User"}},
        )
        data = [{"name": "a", "owner": {"login": "dev2", "id": 1, "email": "x@y"}}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"owner": None},
        )
        assert "| Owner | dev2 |" in result
        assert "## Owner" not in result

    def test_compact_list_renders_joined_identities(self) -> None:
        spec = _spec_with_type(
            "Widget",
            {
                "name": {},
                "labels": {"type": "array", "items": {"$ref": "#/components/schemas/Label"}},
            },
        )
        data = [{"name": "a", "labels": [{"name": "bug"}, {"name": "feat"}]}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"labels": "name"},
        )
        assert "| Labels | bug, feat |" in result

    def test_compact_identity_prefers_login_then_username(self) -> None:
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "owner": {"$ref": "#/components/schemas/Organization"}},
        )
        data = [{"name": "a", "owner": {"username": "mcp-server", "name": "MCP"}}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"owner": None},
        )
        assert "| Owner | mcp-server |" in result

    def test_unknown_type_falls_back_to_generic(self) -> None:
        """No schema for the type → the plain generic renderer, no crash."""
        result = _render([{"name": "a"}], response_type="Unknown", openapi_spec=make_openapi_spec())
        assert "Unknowns - 1 items" in result
        assert "| Name | a |" in result

    def test_no_spec_falls_back_to_generic(self) -> None:
        result = _render([{"name": "a"}], response_type="Widget", openapi_spec=None)
        assert "Widgets - 1 items" in result

    def test_item_title_key_from_schema(self) -> None:
        spec = _spec_with_type("Widget", {"title": {}, "name": {}})
        data = [{"title": "The Title", "name": "n"}]
        result = _render(data, response_type="Widget", openapi_spec=spec)
        assert "# The Title" in result

    def test_empty_list(self) -> None:
        spec = _spec_with_type("Widget", {"name": {}})
        result = _render([], response_type="Widget", openapi_spec=spec)
        assert "Widgets" in result
        assert "_(empty)_" in result

    def test_pluralization(self) -> None:
        for type_name, expected in (
            ("Issue", "Issues"),
            ("Repository", "Repositories"),
            ("Box", "Boxes"),
            ("Class", "Classes"),
        ):
            spec = _spec_with_type(type_name, {"name": {}})
            result = _render([{"name": "a"}], response_type=type_name, openapi_spec=spec)
            assert f"{expected} - 1 items" in result

    def test_dict_result_renders_detail_not_collection(self) -> None:
        """A single-object read renders the full payload, never a collection."""
        spec = _spec_with_type("Repository", {"full_name": {}, "description": {}})
        result = _render(
            {"full_name": "o/r", "description": "d", "private": True},
            response_type="Repository",
            openapi_spec=spec,
        )
        assert result.startswith("# o/r")
        assert "Repositories -" not in result
        # Detail keeps every payload field, including one not in the schema.
        assert "| Private | True |" in result

    def test_dict_result_without_schema_uses_type_title(self) -> None:
        result = _render({"a": 1}, response_type="Widget", openapi_spec=make_openapi_spec())
        assert result.startswith("# Widget")

    def test_dict_result_no_type_title_fallback(self) -> None:
        result = _render({"a": 1}, response_type=None, openapi_spec=None)
        assert result.startswith("# Result")

    def test_identity_without_identity_key_renders_compact_summary(self) -> None:
        """A relation with no identity field renders a quote-free summary.

        Regression for the review of PR #774: ``str(dict)`` leaked single
        quotes and braces into agent-facing markdown.
        """
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "owner": {"$ref": "#/components/schemas/User"}},
        )
        data = [{"name": "a", "owner": {"weird": "shape"}}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"owner": None},
        )
        assert "weird=shape" in result
        assert "{'weird'" not in result

    def test_compact_identity_uses_identity_key(self) -> None:
        """A per-field identity key drives the compact value (``base`` → ref).

        Regression for the review of PR #774: ``base``/``head`` rendered as
        Python dict reprs because no identity key was named.
        """
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "branch": {"$ref": "#/components/schemas/BranchInfo"}},
        )
        data = [{"name": "a", "branch": {"label": "x", "ref": "main", "sha": "abc"}}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"branch": "ref"},
        )
        assert "| Branch | main |" in result
        assert "'label'" not in result

    def test_identity_renders_ref_marker_label(self) -> None:
        """A collapsed relation marker renders as its label, not a Python repr."""
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "owner": {"$ref": "#/components/schemas/User"}},
        )
        data = [{"name": "a", "owner": {"$ref": "User"}}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"owner": None},
        )
        assert "| Owner | $ref:User |" in result

    def test_identity_scalar_value(self) -> None:
        """A scalar in a compact position renders as its string form."""
        spec = _spec_with_type(
            "Widget",
            {"name": {}, "owner": {"$ref": "#/components/schemas/User"}},
        )
        data = [{"name": "a", "owner": 42}]
        result = _render(
            data,
            response_type="Widget",
            openapi_spec=spec,
            compact={"owner": None},
        )
        assert "| Owner | 42 |" in result

    def test_curated_hints_resolve_by_type(self) -> None:
        """``view_hints_for`` resolves the curated deficiency list by type.

        The registration layer hands this to ``tool.meta["view_hints"]`` /
        resource content meta; the render path never reads it off the spec.
        """
        from gitea_mcp_server.openapi_converter.display_hints import view_hints_for

        issue = view_hints_for("Issue")
        assert issue is not None
        assert "body" in issue["omit"]
        assert issue["compact"]["labels"] == "name"
        assert "pull_request" in issue["flag"]
        # Un-curated and unbound types carry no hints.
        assert view_hints_for("Widget") is None
        assert view_hints_for(None) is None

    def test_format_has_no_hint_index(self) -> None:
        """The render path carries hints as data — no spec index/mutation."""
        import gitea_mcp_server.format as format_module

        assert not hasattr(format_module, "_view_hints")
        assert not hasattr(format_module, "_hint_index")
        assert not hasattr(format_module, "_HINT_INDEX_KEY")

    def test_type_schema_no_spec_or_type(self) -> None:
        from gitea_mcp_server.format import _type_schema

        assert _type_schema(None, "Issue") is None
        assert _type_schema(make_openapi_spec(), None) is None

    def test_type_schema_missing_schemas(self) -> None:
        from gitea_mcp_server.format import _type_schema

        spec = make_openapi_spec(components={"schemas": "not-a-dict"})
        assert _type_schema(spec, "Issue") is None

    def test_type_schema_unknown_type(self) -> None:
        from gitea_mcp_server.format import _type_schema

        assert _type_schema(make_openapi_spec(), "Nope") is None

    def test_ref_target_non_dict_schema(self) -> None:
        """A non-dict property schema is not a relation."""
        from gitea_mcp_server.format import _ref_target

        assert _ref_target("not-a-dict", make_openapi_spec()) is None

    def test_ref_target_unresolvable_ref_keeps_name(self) -> None:
        """A ``$ref`` to a type absent from the spec still counts as a relation."""
        from gitea_mcp_server.format import _ref_target

        assert _ref_target({"$ref": "#/components/schemas/Ghost"}, make_openapi_spec()) == "Ghost"

    def test_ref_target_no_spec_returns_name(self) -> None:
        from gitea_mcp_server.format import _ref_target

        assert _ref_target({"$ref": "#/components/schemas/User"}, None) == "User"

    def test_collection_title_issues_filter(self) -> None:
        """``type=issues`` labels the collection Issues."""
        from gitea_mcp_server.format import _collection_title

        assert _collection_title("Issue", 2, {"type": "issues"}) == "Issues - 2 items"

    def test_pluralize_y_ending(self) -> None:
        from gitea_mcp_server.format import _pluralize

        assert _pluralize("Category") == "Categories"

    def test_pluralize_sibilant(self) -> None:
        from gitea_mcp_server.format import _pluralize

        assert _pluralize("Box") == "Boxes"

    def test_pluralize_default(self) -> None:
        from gitea_mcp_server.format import _pluralize

        assert _pluralize("Widget") == "Widgets"

    def test_pluralize_explicit_label(self) -> None:
        from gitea_mcp_server.format import _pluralize

        assert _pluralize("PullRequest") == "Pull Requests"

    def test_identity_ref_marker_label(self) -> None:
        """A collapsed relation marker renders as its label (line 288)."""
        from gitea_mcp_server.format import _identity

        assert _identity({"$ref": "User"}) == "$ref:User"

    def test_identity_scalar_fallback(self) -> None:
        """A non-dict, non-marker value renders as its string form (line 295)."""
        from gitea_mcp_server.format import _identity

        assert _identity(42) == "42"

    def test_fallback_title_for_list_without_schema(self) -> None:
        """A list with no type schema gets a collection title (line 368)."""
        result = _render([{"name": "a"}], response_type="Widget", openapi_spec=None)
        assert "Widgets - 1 items" in result

    def test_non_dict_properties_falls_back(self) -> None:
        """A schema whose ``properties`` is not a dict falls back (line 368)."""
        spec = make_openapi_spec(
            components={"schemas": {"Widget": {"type": "object", "properties": "nope"}}}
        )
        result = _render([{"name": "a"}], response_type="Widget", openapi_spec=spec)
        assert "Widgets - 1 items" in result

    def test_item_title_key_none_when_no_known_key(self) -> None:
        """A schema with no title-ish property yields no item title key (line 430)."""
        from gitea_mcp_server.format import _item_title_key

        assert _item_title_key({"count": {}, "size": {}}) is None

    def test_iso_datetime_auto_format(self) -> None:
        """An ISO datetime string is auto-formatted without a schema hint."""
        result = format_as_markdown({"when": "2024-01-01T00:00:00Z"})
        assert "2024-01-01" in result


class TestFormatLabelsMarkdown:
    """The bespoke labels formatter (the one non-generic view)."""

    def test_empty_data_labels(self) -> None:
        result = _format_labels_markdown([], extra={"owner": "org", "repo": "repo"})
        assert "No labels configured for this repository" in result

    def test_empty_data_labels_no_extra(self) -> None:
        result = _format_labels_markdown([])
        assert "?/?" in result

    def test_string_items_render_verbatim(self) -> None:
        """Non-dict items render verbatim (defensive shape guard)."""
        result = _format_labels_markdown(
            ["bug", "feature"],
            extra={"owner": "o", "repo": "r"},
        )
        assert "- bug" in result
        assert "- feature" in result

    def test_label_dict_detail_view(self) -> None:
        label = {
            "id": 1,
            "name": "Kind/Bug",
            "color": "ee0701",
            "description": "desc",
            "exclusive": True,
            "is_archived": True,
        }
        result = _format_labels_markdown(label, extra={"owner": "o", "repo": "r"})
        assert result.startswith("# Label: Kind/Bug (#1) *(archived)* for o/r")
        assert "scope: `Kind`" in result
        assert "**Exclusive**: Yes" in result
        assert "Accepted Format" not in result
        assert "**Total**" not in result

    def test_label_dict_without_extra(self) -> None:
        result = _format_labels_markdown({"id": 2, "name": "bug"})
        assert result.startswith("# Label: bug (#2)")
        assert " for " not in result.splitlines()[0]

    def test_label_list_org_scope(self) -> None:
        """Org-scoped label tools pass ``org`` — heading shows it (#766)."""
        result = _format_labels_markdown(
            [{"id": 1, "name": "bug", "color": "red"}], extra={"org": "mcp-server"}
        )
        assert "# Labels for mcp-server" in result
        assert "?/?" not in result

    def test_label_detail_org_scope(self) -> None:
        result = _format_labels_markdown({"id": 1, "name": "bug"}, extra={"org": "mcp-server"})
        assert result.startswith("# Label: bug (#1) for mcp-server")

    def test_label_scope_prefers_owner_over_org(self) -> None:
        result = _format_labels_markdown(
            [{"id": 1, "name": "bug"}],
            extra={"owner": "o", "repo": "r", "org": "ignored"},
        )
        assert "# Labels for o/r" in result

    def test_labels_format_contains_hints_and_scope(self) -> None:
        labels = [
            {
                "id": 1,
                "name": "bug",
                "color": "ff0000",
                "description": "Bug reports",
                "exclusive": False,
            },
            {
                "id": 5,
                "name": "Kind/Feature",
                "color": "00ff00",
                "description": "New features",
                "exclusive": True,
            },
        ]
        result = _format_labels_markdown(labels, extra={"owner": "test-owner", "repo": "test-repo"})
        assert "# Labels for test-owner/test-repo" in result
        assert "Accepted Format" in result
        assert "Names" in result
        assert "IDs" in result
        assert "bug" in result
        assert "Kind/Feature" in result
        assert "(scope: " in result
        assert "`#ff0000`" in result

    def test_labels_format_concise_receives_item_dicts(self) -> None:
        """Under detail=concise items are summarized dicts (#759)."""
        concise_labels = [
            {"id": 1, "name": "Kind/Bug", "color": "ee0701", "description": "Bugs"},
            {"id": 2, "name": "Priority/High", "color": "e64a19", "description": ""},
        ]
        result = _format_labels_markdown(
            concise_labels,
            extra={"owner": "test-owner", "repo": "test-repo"},
        )
        assert "# Labels for test-owner/test-repo" in result
        assert "**Total**: 2 labels" in result
        assert "### Kind/Bug (#1)" in result
        assert "**Color**:" in result

    def test_labels_format_defensive_on_string_items(self) -> None:
        result = _format_labels_markdown(
            ["$ref:Label", "$ref:Label"],
            extra={"owner": "test-owner", "repo": "test-repo"},
        )
        assert "**Total**: 2 labels" in result
        assert "- $ref:Label" in result
        assert "**Color**:" not in result


class TestBuildServerInfoMarkdown:
    def test_build_server_info_markdown(self) -> None:
        spec = make_openapi_spec(
            info={
                "title": "Gitea API",
                "version": "1.21.0",
                "description": "Gitea API description.",
            }
        )
        result = build_server_info_markdown(spec)

        assert "**Server Type**: Gitea API" in result
        assert "**API Version**: 1.21.0" in result
        assert "## Description" in result
        assert "Gitea API description." in result

    def test_build_server_info_markdown_no_description(self) -> None:
        spec = make_openapi_spec(info={"title": "Gitea API", "version": "1.21.0"})
        result = build_server_info_markdown(spec)

        assert "**Server Type**: Gitea API" in result
        assert "## Description" not in result

    def test_build_server_info_markdown_missing_info(self) -> None:
        result = build_server_info_markdown({})

        assert "**Server Type**: Unknown" in result
        assert "**API Version**: Unknown" in result
