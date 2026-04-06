"""Unit tests for MCP extensions processing."""

import pytest
from unittest.mock import mock_open, patch

from gitea_mcp_server.server_setup.mcp_extensions import (
    apply_mcp_extensions,
    load_mcp_extensions,
)


class TestApplyMcpExtensions:
    """Tests for the apply_mcp_extensions function."""

    def test_applies_title_override(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "summary": "Original title",
                        "description": "Original description",
                    }
                }
            }
        }
        extensions = {
            "tool_names": {
                "create_issue": {
                    "title": "Custom Create Issue Title",
                    "description": "Custom description",
                }
            }
        }

        apply_mcp_extensions(spec, extensions)

        op = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]
        assert op["summary"] == "Custom Create Issue Title"
        assert op["description"] == "Custom description"
        assert "x-mcp" not in op

    def test_applies_parameter_customization(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "parameters": [
                            {
                                "name": "title",
                                "in": "query",
                                "description": "Original param description",
                                "schema": {"type": "string"},
                            }
                        ],
                    }
                }
            }
        }
        extensions = {
            "tool_names": {
                "create_issue": {
                    "parameters": [
                        {
                            "name": "title",
                            "description": "Custom title parameter description",
                            "examples": ["Bug: Something broke", "Feature: Add something"],
                        }
                    ]
                }
            }
        }

        apply_mcp_extensions(spec, extensions)

        param = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]["parameters"][0]
        assert param["description"] == "Custom title parameter description"
        assert "examples" in param
        assert param["examples"] == ["Bug: Something broke", "Feature: Add something"]

    def test_handles_multiple_parameters(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "parameters": [
                            {"name": "title", "in": "query", "description": "Orig title"},
                            {"name": "body", "in": "query", "description": "Orig body"},
                        ],
                    }
                }
            }
        }
        extensions = {
            "tool_names": {
                "create_issue": {
                    "parameters": [
                        {"name": "title", "description": "Custom title desc"},
                        {"name": "body", "description": "Custom body desc"},
                    ]
                }
            }
        }

        apply_mcp_extensions(spec, extensions)

        params = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]["parameters"]
        assert params[0]["description"] == "Custom title desc"
        assert params[1]["description"] == "Custom body desc"

    def test_skips_unknown_tool_names(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "summary": "Original title",
                    }
                }
            }
        }
        extensions = {
            "tool_names": {
                "unknown_operation": {
                    "title": "Should not apply",
                }
            }
        }

        apply_mcp_extensions(spec, extensions)

        op = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]
        assert op["summary"] == "Original title"

    def test_removes_x_mcp_after_processing(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "summary": "Original",
                        "x-mcp": {"title": "Custom"},
                    }
                }
            }
        }
        extensions = {"tool_names": {"create_issue": {"title": "Custom"}}}

        apply_mcp_extensions(spec, extensions)

        op = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]
        assert "x-mcp" not in op

    def test_handles_missing_operation_id_in_spec(self):
        spec = {
            "paths": {
                "/some/path": {
                    "post": {
                        "summary": "No op ID",
                    }
                }
            }
        }
        extensions = {"tool_names": {"some_op": {"title": "Custom"}}}

        # Should not crash, just skip
        apply_mcp_extensions(spec, extensions)

        assert spec["paths"]["/some/path"]["post"]["summary"] == "No op ID"

    def test_applies_only_provided_fields(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "summary": "Original title",
                        "description": "Original description",
                    }
                }
            }
        }
        extensions = {
            "tool_names": {
                "create_issue": {
                    "title": "New title",
                    # description not provided, should not change
                }
            }
        }

        apply_mcp_extensions(spec, extensions)

        op = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]
        assert op["summary"] == "New title"
        assert op["description"] == "Original description"

    def test_handles_empty_extensions(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "summary": "Original",
                    }
                }
            }
        }
        extensions = {"tool_names": {}}

        apply_mcp_extensions(spec, extensions)

        op = spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]
        assert op["summary"] == "Original"

    def test_merges_multiple_operations(self):
        spec = {
            "paths": {
                "/repos/{owner}/{repo}/issues": {
                    "post": {
                        "operationId": "create_issue",
                        "summary": "Original create",
                    }
                },
                "/repos/{owner}/{repo}/issues/{index}": {
                    "put": {
                        "operationId": "edit_issue",
                        "summary": "Original edit",
                    }
                },
            }
        }
        extensions = {
            "tool_names": {
                "create_issue": {"title": "Custom create"},
                "edit_issue": {"title": "Custom edit"},
            }
        }

        apply_mcp_extensions(spec, extensions)

        assert spec["paths"]["/repos/{owner}/{repo}/issues"]["post"]["summary"] == "Custom create"
        assert (
            spec["paths"]["/repos/{owner}/{repo}/issues/{index}"]["put"]["summary"] == "Custom edit"
        )


class TestLoadMcpExtensions:
    """Tests for the load_mcp_extensions function."""

    def test_loads_yaml_file(self):
        yaml_content = """
tool_names:
  create_issue:
    title: "Custom Title"
    description: "Custom description"
    """
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = load_mcp_extensions()

        assert result == {
            "tool_names": {
                "create_issue": {"title": "Custom Title", "description": "Custom description"}
            }
        }

    def test_returns_empty_when_file_missing(self):
        with patch("pathlib.Path.exists", return_value=False):
            result = load_mcp_extensions()

        assert result == {}

    def test_returns_empty_when_file_empty(self):
        yaml_content = ""
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                result = load_mcp_extensions()

        assert result == {}

    def test_handles_invalid_yaml(self):
        yaml_content = "invalid: yaml: content:"
        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch("pathlib.Path.exists", return_value=True):
                with pytest.raises(Exception):  # Should raise YAMLError
                    load_mcp_extensions()
