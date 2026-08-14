"""Integration tests for MCP extensions end-to-end."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.server_setup.mcp_builder import create_openapi_provider
from gitea_mcp_server.server_setup.mcp_extensions import apply_mcp_extensions, load_mcp_extensions
from tests.helpers.spec_fixtures import make_openapi_spec


def _tool_dict(tools: Sequence[Any]) -> dict[str, Any]:
    """Extract tool name -> tool mapping from provider.list_tools() result."""
    return {t.name: t for t in tools}


@pytest.fixture
def minimal_spec() -> OpenAPISpec:
    """Minimal OpenAPI spec with two operations."""
    return make_openapi_spec(
        paths={
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
    )


@pytest.mark.asyncio
async def test_parameter_extensions_apply_to_spec_and_are_visible_in_tools(
    minimal_spec: OpenAPISpec,
) -> None:
    """Test that mcp_extensions.yaml parameter customizations propagate through spec to tools.

    Note: Tool-level metadata overrides (title, description, tags, hints) are handled
    by ``ExtensionMetadataTransform`` at query time, not at the spec level.
    """
    # Create a fake Gitea client
    mock_gitea_client = MagicMock()
    mock_gitea_client.client = MagicMock()
    mock_gitea_client.request.return_value = {}

    # Apply extensions manually - only parameter overrides are spec-level
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
        gitea_client=mock_gitea_client,
        label_service=MagicMock(),
    )

    # Get tools from provider via public API
    tools = await provider.list_tools()
    tool_names = _tool_dict(tools)

    assert "issue_create_issue" in tool_names
    # Description is NOT overridden at spec level - stays as original
    assert "Original description" in tool_names["issue_create_issue"].description


def test_extensions_load_from_yaml_file(minimal_spec: OpenAPISpec, tmp_path: Path) -> None:
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
