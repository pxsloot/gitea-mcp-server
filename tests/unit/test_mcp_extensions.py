"""Unit tests for MCP extensions processing."""

from unittest.mock import MagicMock, mock_open, patch

import pytest

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

    def test_loads_yaml_file(self, tmp_path):
        yaml_content = """
tool_names:
  create_issue:
    title: "Custom Title"
    description: "Custom description"
"""
        yaml_file = tmp_path / "mcp_extensions.yaml"
        yaml_file.write_text(yaml_content)

        with patch.dict("os.environ", {"MCP_EXTENSIONS_PATH": str(yaml_file)}):
            result = load_mcp_extensions()

        assert result == {
            "tool_names": {
                "create_issue": {"title": "Custom Title", "description": "Custom description"}
            }
        }

    def test_returns_empty_when_file_missing(self, tmp_path):
        nonexistent = tmp_path / "nonexistent.yaml"

        with patch.dict("os.environ", {"MCP_EXTENSIONS_PATH": str(nonexistent)}):
            result = load_mcp_extensions()

        assert result == {}

    def test_returns_empty_when_file_empty(self, tmp_path):
        yaml_file = tmp_path / "mcp_extensions.yaml"
        yaml_file.write_text("")

        with patch.dict("os.environ", {"MCP_EXTENSIONS_PATH": str(yaml_file)}):
            result = load_mcp_extensions()

        assert result == {}

    def test_handles_invalid_yaml(self, tmp_path):
        yaml_file = tmp_path / "mcp_extensions.yaml"
        yaml_file.write_text("invalid: yaml: content:")

        with patch.dict("os.environ", {"MCP_EXTENSIONS_PATH": str(yaml_file)}):
            with pytest.raises(Exception):
                load_mcp_extensions()


class TestLoadMcpExtensionsEdgeCases:
    """Tests for edge cases in load_mcp_extensions."""

    def test_project_root_not_found_falls_back_to_cwd(self, tmp_path):
        """When pyproject.toml is not found, fall back to cwd."""
        from pathlib import Path
        from unittest.mock import patch

        with (
            patch("gitea_mcp_server.server_setup.mcp_extensions._find_project_root") as mock_find,
            patch.object(Path, "cwd", return_value=tmp_path),
        ):
            mock_find.side_effect = RuntimeError("No pyproject.toml")
            result = load_mcp_extensions()
            assert result == {}

    def test_runtime_error_from_find_project_root(self):
        """_find_project_root raises RuntimeError when no pyproject.toml found."""
        from pathlib import Path
        from unittest.mock import patch

        # Mock the __file__ path to be in a dir without pyproject.toml
        with (
            patch("pathlib.Path.exists", return_value=False),
        ):
            from gitea_mcp_server.server_setup.mcp_extensions import _find_project_root
            with pytest.raises(RuntimeError, match="Could not find project root"):
                _find_project_root()

    def test_os_error_on_read_propagates(self, tmp_path):
        """OSError when reading extensions file propagates."""
        yaml_file = tmp_path / "mcp_extensions.yaml"
        yaml_file.write_text("tool_names:\n  test: {}")

        with patch.dict("os.environ", {"MCP_EXTENSIONS_PATH": str(yaml_file)}):
            with patch("gitea_mcp_server.server_setup.mcp_extensions.Path.open") as mock_open:
                mock_open.side_effect = OSError("Permission denied")
                with pytest.raises(OSError, match="Permission denied"):
                    load_mcp_extensions()

    def test_apply_parameter_extensions_skips_missing_name(self):
        """apply_mcp_extensions skips parameter extensions with no name."""
        spec = {
            "paths": {
                "/test": {
                    "post": {
                        "operationId": "test_op",
                        "parameters": [
                            {
                                "name": "existing",
                                "in": "query",
                                "description": "Original",
                                "schema": {"type": "string"},
                            }
                        ],
                    }
                }
            }
        }
        extensions = {
            "tool_names": {
                "test_op": {
                    "parameters": [
                        {"description": "No name field"},
                    ]
                }
            }
        }
        apply_mcp_extensions(spec, extensions)
        param = spec["paths"]["/test"]["post"]["parameters"][0]
        assert param["description"] == "Original"

    def test_apply_skips_non_dict_path_item(self):
        """apply_mcp_extensions skips path items that are not dicts."""
        spec = {
            "paths": {
                "/valid": {
                    "get": {
                        "operationId": "get_valid",
                    }
                },
                "/broken": "not_a_dict",
            }
        }
        extensions = {"tool_names": {"get_valid": {"description": "Updated"}}}
        apply_mcp_extensions(spec, extensions)
        # Non-dict path is skipped, no crash
        assert spec["paths"]["/valid"]["get"]["operationId"] == "get_valid"

    def test_apply_skips_invalid_operation_types(self):
        """apply_mcp_extensions skips non-dict operations or invalid methods."""
        spec = {
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "get_test",
                    },
                    "invalid_method": "this is not a dict",
                    "parameters": [{"name": "p1", "in": "query"}],
                }
            }
        }
        extensions = {"tool_names": {"get_test": {"description": "Updated"}}}
        apply_mcp_extensions(spec, extensions)
        assert spec["paths"]["/test"]["get"]["description"] == "Updated"

    def test_apply_with_empty_tool_names(self):
        """apply_mcp_extensions with no tool_names returns early."""
        spec = {"paths": {}}
        apply_mcp_extensions(spec, extensions={"tool_names": {}})
        assert True  # No error
