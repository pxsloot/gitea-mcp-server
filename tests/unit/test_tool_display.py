"""Tests for tool_display module (format_tool_result wrapper)."""

from gitea_mcp_server.tools.tool_display import format_tool_result


class TestFormatToolResult:
    """Tests for format_tool_result -- thin delegator to apply_format."""

    def test_raw_format_passthrough(self):
        """'raw' format returns structured content."""
        result = format_tool_result({"key": "value"}, "raw")
        assert result.structured_content == {"result": {"key": "value"}}
        # raw format may include empty or json text content; verify structure
        assert isinstance(result.structured_content, dict)

    def test_json_format(self):
        """'json' format produces indented JSON in text content."""
        result = format_tool_result({"key": "value"}, "json")
        assert result.structured_content == {"result": {"key": "value"}}
        assert result.content is not None
        text = result.content[0].text
        assert '"key"' in text
        assert '"value"' in text

    def test_markdown_format_default(self):
        """'markdown' format produces markdown text."""
        data = {"name": "test", "count": 42}
        result = format_tool_result(data, "markdown")
        assert result.structured_content == {"result": data}
        assert result.content is not None
        text = result.content[0].text
        assert "test" in text
        assert "42" in text

    def test_concise_detail_collapses_schema(self):
        """detail=concise with schema collapses nested objects."""
        data = {"id": 1, "user": {"login": "alice", "id": 99}}
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "user": {"$ref": "#/components/schemas/User"},
            },
        }
        result = format_tool_result(data, "json", detail="concise", schema=schema)
        text = result.content[0].text
        # Nested user should be collapsed to $ref
        assert "$ref:User" in text

    def test_concise_detail_no_schema_no_collapse(self):
        """detail=concise without schema leaves data intact."""
        data = {"id": 1, "nested": {"key": "val"}}
        result = format_tool_result(data, "json", detail="concise", schema=None)
        text = result.content[0].text
        assert '"key"' in text
        assert '"val"' in text

    def test_list_data_formats_correctly(self):
        """List data is wrapped in result and formatted."""
        data = [{"id": 1}, {"id": 2}]
        result = format_tool_result(data, "json")
        assert result.structured_content == {"result": data}

    def test_returns_tool_result_type(self):
        """Return value is a ToolResult instance."""
        from fastmcp.tools.base import ToolResult

        result = format_tool_result({"a": 1}, "raw")
        assert isinstance(result, ToolResult)

    def test_empty_dict(self):
        """Empty dict formats without error."""
        result = format_tool_result({}, "markdown")
        assert result.structured_content == {"result": {}}

    def test_none_data(self):
        """None data passes through."""
        result = format_tool_result(None, "raw")
        assert result.structured_content == {"result": None}
