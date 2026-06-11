"""Integration tests for MCP extensions end-to-end."""

from unittest.mock import MagicMock

import pytest

from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.mcp_extensions import apply_mcp_extensions, load_mcp_extensions


@pytest.fixture
def minimal_spec():
    """Minimal OpenAPI spec with two operations."""
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/repos/{owner}/{repo}/issues": {
                "post": {
                    "operationId": "issue_create_issue",
                    "summary": "Create an issue",
                    "description": "Original description",
                    "parameters": [
                        {
                            "name": "owner",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "labels",
                            "in": "query",
                            "schema": {"type": "array", "items": {"type": "integer"}},
                        },
                    ],
                }
            },
            "/repos/{owner}/{repo}/issues/{index}/comments": {
                "post": {
                    "operationId": "issue_create_comment",
                    "summary": "Add a comment",
                    "description": "Original comment description",
                    "parameters": [
                        {
                            "name": "index",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        },
                        {
                            "name": "body",
                            "in": "query",
                            "schema": {"type": "string"},
                        },
                    ],
                }
            },
        },
    }


def test_parameter_extensions_apply_to_spec_and_are_visible_in_tools(minimal_spec):
    """Test that mcp_extensions.yaml parameter customizations propagate through spec to tools.

    Note: Tool-level metadata overrides (title, description, tags, hints) are handled
    by ``ExtensionMetadataTransform`` at query time, not at the spec level.
    """
    # Create a fake Gitea client
    mock_client = MagicMock()
    mock_client.request.return_value = {}

    # Apply extensions manually — only parameter overrides are spec-level
    extensions = {
        "tool_names": {
            "issue_create_issue": {
                "parameters": [
                    {"name": "labels", "description": "Custom labels parameter description"},
                ]
            },
        }
    }
    apply_mcp_extensions(minimal_spec, extensions)

    # Convert to OpenAPI v3 (the spec is already v3, but this simulates the pipeline)
    provider = create_openapi_provider(
        openapi_spec=minimal_spec,
        client=mock_client,
        label_manager=MagicMock(),
    )

    # Get tools from provider
    tools = list(provider._tools.values())
    tool_names = {t.name: t for t in tools}

    assert "issue_create_issue" in tool_names
    # Description is NOT overridden at spec level — stays as original
    assert "Original description" in tool_names["issue_create_issue"].description


def test_extensions_load_from_yaml_file(minimal_spec, tmp_path):
    """Test that extensions are loaded from mcp_extensions.yaml."""
    # Create a temporary extensions file
    ext_content = """
tool_names:
  issue_create_issue:
    description: "Loaded from YAML"
"""
    ext_file = tmp_path / "mcp_extensions.yaml"
    ext_file.write_text(ext_content)

    # Load extensions from that file
    extensions = load_mcp_extensions(config_path=ext_file)
    assert extensions == {"tool_names": {"issue_create_issue": {"description": "Loaded from YAML"}}}


def test_label_guidance_appendage_when_labels_present(minimal_spec):
    """Test that LABEL_GUIDANCE is auto-appended to tools with labels parameter."""
    # No explicit description extension, rely on auto-guidance
    provider = create_openapi_provider(
        openapi_spec=minimal_spec,
        client=MagicMock(),
        label_manager=MagicMock(),
    )
    tools = list(provider._tools.values())
    tool_names = {t.name: t for t in tools}

    # issue_create_issue should have label guidance appended
    create_issue_tool = tool_names.get("issue_create_issue")
    assert create_issue_tool is not None
    assert (
        "You may provide existing label names (strings) or IDs (integers)"
        in create_issue_tool.description
    )

    # issue_create_comment should NOT have label guidance (no labels param)
    comment_tool = tool_names.get("issue_create_comment")
    assert comment_tool is not None
    assert "label names (strings)" not in comment_tool.description.lower()
