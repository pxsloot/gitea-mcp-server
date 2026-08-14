"""Tests for gitea_mcp_server.uri_utils."""

import pytest

from gitea_mcp_server.uri_utils import clean_resource_uri


class TestCleanResourceUri:
    """Tests for clean_resource_uri — strip RFC 6570 {?query} suffix."""

    @pytest.mark.parametrize(
        ("uri", "expected"),
        [
            # Single param
            ("gitea://repos/{owner}/{repo}/pulls{?state}", "gitea://repos/{owner}/{repo}/pulls"),
            # Multiple params
            (
                "gitea://repos/{owner}/{repo}/issues{?state,type}",
                "gitea://repos/{owner}/{repo}/issues",
            ),
            # Mixed with draft
            (
                "gitea://repos/{owner}/{repo}/releases{?draft,q}",
                "gitea://repos/{owner}/{repo}/releases",
            ),
            # No query suffix — pass through unchanged
            ("gitea://repos/{owner}/{repo}", "gitea://repos/{owner}/{repo}"),
            ("gitea://repos/{owner}/{repo}/labels", "gitea://repos/{owner}/{repo}/labels"),
            # {?query} in middle (should not strip — anchored at end)
            ("gitea://{?param}/repos/{owner}/{repo}", "gitea://{?param}/repos/{owner}/{repo}"),
            # Empty string
            ("", ""),
        ],
    )
    def test_strips_query_suffix(self, uri: str, expected: str) -> None:
        """clean_resource_uri strips {?query} suffix or passes through."""
        assert clean_resource_uri(uri) == expected
