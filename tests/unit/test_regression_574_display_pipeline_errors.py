"""Regression tests for issue #574: display pipeline mid-failure error recovery.

The display layer must handle unexpected data shapes gracefully instead of
crashing.  The pipeline-level try/except lives in the single result pipeline
(``tools/result_pipeline.py`` — see ``TestErrorRecovery`` there); these tests
lock the renderer-side guards that make the recovery path reachable.

Since #771 the collection view is generic (``format._generic_collection_view``)
and the only bespoke formatter is ``labels``; the guards below cover both.
"""

from gitea_mcp_server.format import _generic_collection_view, format_as_markdown
from gitea_mcp_server.tools.display import _format_labels_markdown
from tests.helpers.spec_fixtures import make_openapi_spec


class TestGenericCollectionViewGuard:
    """Guard: the generic view handles unexpected data shapes."""

    def test_non_dict_items_no_crash(self) -> None:
        """Non-dict items produce output, not AttributeError."""
        spec = make_openapi_spec(
            paths={
                "/widgets": {
                    "get": {
                        "x-response-type": "Widget",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/Widget"},
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            },
            components={"schemas": {"Widget": {"type": "object", "properties": {"name": {}}}}},
        )
        result = _generic_collection_view(
            ["string item", "another string"], response_type="Widget", openapi_spec=spec
        )
        assert result.strip() != ""

    def test_empty_list_no_crash(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/widgets": {
                    "get": {
                        "x-response-type": "Widget",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/Widget"},
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            },
            components={"schemas": {"Widget": {"type": "object", "properties": {"name": {}}}}},
        )
        result = _generic_collection_view([], response_type="Widget", openapi_spec=spec)
        assert result.strip() != ""
        assert "_(empty)_" in result


class TestFormatLabelsMarkdownGuard:
    """Guard: _format_labels_markdown handles non-dict items."""

    def test_non_dict_items_no_crash(self) -> None:
        """String items render compactly, not AttributeError."""
        data = ["bug", "feature"]
        result = _format_labels_markdown(
            data,
            extra={"owner": "test", "repo": "test"},
        )
        assert result.strip() != ""
        assert "Labels for test/test" in result
        assert "- bug" in result
        assert "- feature" in result

    def test_string_items_render_verbatim(self) -> None:
        """Plain string items render verbatim, not crash."""
        data = ["$ref:Label[2]"]
        result = _format_labels_markdown(
            data,
            extra={"owner": "o", "repo": "r"},
        )
        assert "Labels for o/r" in result
        assert "$ref:Label[2]" in result

    def test_mixed_items_full_branch_guard(self) -> None:
        """Mixed string+dict items hit the full-branch non-dict guard, no crash."""
        data = ["$ref:Label", {"id": 1, "name": "bug", "color": "ff0000"}]
        result = _format_labels_markdown(
            data,
            extra={"owner": "o", "repo": "r"},
        )
        assert "Labels for o/r" in result
        assert "- $ref:Label" in result
        assert "bug" in result


class TestFormatAsMarkdownEdgeCases:
    """Edge cases for format_as_markdown with unexpected data shapes."""

    def test_none_data(self) -> None:
        """None data produces 'N/A'."""
        result = format_as_markdown(None)
        assert result == "N/A"

    def test_none_data_with_title(self) -> None:
        """None data with title still shows title and N/A."""
        result = format_as_markdown(None, title="Test Title")
        assert "# Test Title" in result
        assert "N/A" in result

    def test_bool_input(self) -> None:
        """Boolean input produces string representation."""
        result = format_as_markdown(True)
        assert result == "True"

    def test_list_with_mixed_types(self) -> None:
        """Mixed-type list items render without crash."""
        result = format_as_markdown([1, "two", None, {"key": "val"}])
        # None falls back to "N/A" in _format_simple_value
        assert "N/A" in result or "two" in result
