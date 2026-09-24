"""Unit tests for label parameter schema augmentation (``tools/labels.py``)."""

from unittest.mock import MagicMock

from gitea_mcp_server.tools.labels import update_labels_schema


class TestUpdateLabelsSchema:
    """``update_labels_schema`` widens the labels item type to string + integer."""

    def test_updates_integer_type_to_union(self) -> None:
        """Schema with integer items.type should become [string, integer]."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "integer"},
                }
            }
        }

        update_labels_schema(tool)

        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]

    def test_updates_string_type_to_union(self) -> None:
        """Schema with string items.type should become [string, integer]."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            }
        }

        update_labels_schema(tool)

        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]

    def test_preserves_existing_union(self) -> None:
        """Schema already with union type should not be modified."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": ["string", "integer"]},
                }
            }
        }

        update_labels_schema(tool)

        labels_schema = tool.parameters["properties"]["labels"]
        assert labels_schema["items"]["type"] == ["string", "integer"]

    def test_skips_non_array_labels(self) -> None:
        """If labels is not array type, schema should not be modified."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "labels": {"type": "string"},
            }
        }

        update_labels_schema(tool)

        # Should remain unchanged
        assert tool.parameters["properties"]["labels"]["type"] == "string"

    def test_skips_no_labels_property(self) -> None:
        """Tool without labels property should not be modified."""
        tool = MagicMock()
        tool.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
            }
        }

        update_labels_schema(tool)

        # Should remain unchanged
        assert "labels" not in tool.parameters["properties"]

    def test_skips_no_parameters(self) -> None:
        """Tool without parameters attribute should not crash."""
        tool = MagicMock()
        # No parameters attribute
        del tool.parameters

        # Should not raise
        update_labels_schema(tool)

    def test_skips_empty_parameters(self) -> None:
        """Tool with None parameters should not crash."""
        tool = MagicMock()
        tool.parameters = None

        # Should not raise
        update_labels_schema(tool)
