"""Regression tests for Issue #316: dot-prefixed and multi-segment file paths.

Issue: read_resource fails on dot-prefixed file paths like .forgejo/workflows/testing.yml.
AC-312: get_raw_file tool twins reject dotfiles at schema/validation layer.

Root causes:
  1. URI template ``gitea://repos/{owner}/{repo}/files/{path}`` uses ``{path}`` which
     matches only one segment (RFC 6570). Multi-segment paths need ``{path*}``.
  2. ``FILEPATH_PATTERN`` regex ``^[a-zA-Z0-9](?:[a-zA-Z0-9_./ -]*[a-zA-Z0-9])?$``
     rejects paths starting with a dot (``.gitignore``, ``.env``, ``.forgejo/...``).
  3. ``validate_filepath()`` applies the same regex, so every tool twin
     (``get_raw_file``, ``create_file``, ``update_file``, etc.) rejects dotfiles.

Each test here MUST fail on the current broken code and pass after the fix.
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.resources.custom import register_custom_resources
from gitea_mcp_server.validation import FILEPATH_PATTERN, validate_filepath


# ============================================================================
# Bug 1: URI template uses {path} instead of {path*}
# ============================================================================


class TestFilesResourceUriTemplate:
    """REG: files resource must use {path*} wildcard for multi-segment paths."""

    @pytest.fixture
    def mock_mcp(self):
        return MagicMock(spec=FastMCP)

    @pytest.fixture
    def mock_client(self):
        return AsyncMock()

    def test_files_uri_template_uses_wildcard_path(
        self, mock_mcp, mock_client
    ):
        """The files resource URI must use {path*} to match multi-segment paths."""
        register_custom_resources(mock_mcp, mock_client)
        uri_templates = [call[0][0] for call in mock_mcp.resource.call_args_list]
        files_uri = next(u for u in uri_templates if "files" in u)
        assert "{path*}" in files_uri, (
            f"Expected {{path*}} wildcard for multi-segment support, got: {files_uri}"
        )


# ============================================================================
# Bug 2: FILEPATH_PATTERN regex rejects dot-prefixed paths
# ============================================================================


class TestFilepathPatternDotfiles:
    """REG: FILEPATH_PATTERN must accept dotfile paths."""

    @pytest.mark.parametrize(
        "value",
        [
            ".gitignore",
            ".env",
            ".env.example",
            ".github/workflows/ci.yml",
            ".forgejo/workflows/testing.yml",
            ".github/ISSUE_TEMPLATE/bug.yml",
            "src/.env",
            "path/to/.hidden",
            ".hidden/with/file.txt",
        ],
    )
    def test_dotfile_paths_should_match_regex(self, value):
        assert re.fullmatch(FILEPATH_PATTERN, value) is not None, (
            f"Dotfile path {value!r} should match FILEPATH_PATTERN"
        )

    @pytest.mark.parametrize(
        "value",
        [
            "/absolute/path",
            "/etc/passwd",
        ],
    )
    def test_truly_invalid_paths_still_rejected(self, value):
        """Absolute paths (starting with /) must still fail, even after fix.
        Note: traversal like '../parent' is blocked by validate_filepath's
        '..' split check, not by the regex itself.
        """
        assert re.fullmatch(FILEPATH_PATTERN, value) is None, (
            f"Invalid path {value!r} should not match FILEPATH_PATTERN"
        )


# ============================================================================
# Bug 3: validate_filepath rejects dot-prefixed paths
# ============================================================================


class TestValidateFilepathDotfiles:
    """REG: validate_filepath must accept dotfile paths."""

    @pytest.mark.parametrize(
        "value",
        [
            ".gitignore",
            ".env",
            ".forgejo/workflows/testing.yml",
            ".github/workflows/ci.yml",
            "src/.env",
            ".hidden/with/file.txt",
        ],
    )
    def test_dotfile_paths_should_pass_validation(self, value):
        validate_filepath(value, field="filepath")

    @pytest.mark.parametrize(
        "value",
        [
            "/absolute/path",
            "../escape",
            "path/../../etc",
            "",
        ],
    )
    def test_truly_invalid_paths_still_rejected(self, value):
        """Absolute and traversal paths must still be rejected."""
        with pytest.raises(ValidationError):
            validate_filepath(value, field="filepath")
