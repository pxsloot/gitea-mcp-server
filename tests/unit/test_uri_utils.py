"""Tests for gitea_mcp_server.uri_utils."""

import pytest
from fastmcp.resources.template import match_uri_template

from gitea_mcp_server.uri_utils import (
    clean_resource_uri,
    expand_path_params,
    render_wildcard_segment,
    wildcard_param_names,
)


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


class TestWildcardParamNames:
    """Tests for wildcard_param_names — extract {param*} names."""

    def test_extracts_wildcard_names(self) -> None:
        assert wildcard_param_names("gitea://repos/{owner}/{repo}/contents/{filepath*}") == {
            "filepath"
        }

    def test_ignores_simple_params(self) -> None:
        assert wildcard_param_names("gitea://repos/{owner}/{repo}") == set()

    def test_ignores_query_suffix(self) -> None:
        assert wildcard_param_names("gitea://repos/{owner}/{repo}/issues{?state}") == set()


class TestRenderWildcardSegment:
    """Tests for render_wildcard_segment — segment-aware {param} → {param*}."""

    def test_renders_exact_segment(self) -> None:
        assert (
            render_wildcard_segment("gitea://repos/{owner}/{repo}/contents/{filepath}", "filepath")
            == "gitea://repos/{owner}/{repo}/contents/{filepath*}"
        )

    def test_does_not_rewrite_same_named_substring(self) -> None:
        """A different segment with a prefix/suffix name is untouched."""
        assert (
            render_wildcard_segment("gitea://x/{filepath2}/{filepath}", "filepath")
            == "gitea://x/{filepath2}/{filepath*}"
        )

    def test_query_suffix_is_not_treated_as_path_segment(self) -> None:
        """A segment carrying a query suffix is not an exact path segment.

        ``derive_resource_uri`` renders the wildcard before appending
        ``{?query}``, so the conservative behavior is correct: the query
        suffix is never mangled by wildcard rendering.
        """
        assert (
            render_wildcard_segment("gitea://x/{filepath}{?filepath}", "filepath")
            == "gitea://x/{filepath}{?filepath}"
        )

    def test_already_wildcard_unchanged(self) -> None:
        assert (
            render_wildcard_segment("gitea://x/{filepath*}{?ref}", "filepath")
            == "gitea://x/{filepath*}{?ref}"
        )


class TestExpandPathParams:
    """Tests for expand_path_params — percent-encoded path substitution."""

    def test_unreserved_values_unchanged(self) -> None:
        """The current restricted charset needs no encoding — no behavior change."""
        assert (
            expand_path_params(
                "gitea://repos/{owner}/{repo}",
                {"owner": "mcp-server_1.0", "repo": "gitea-mcp-server"},
            )
            == "gitea://repos/mcp-server_1.0/gitea-mcp-server"
        )

    def test_simple_param_encodes_space_and_percent(self) -> None:
        assert (
            expand_path_params(
                "gitea://repos/{owner}/{repo}",
                {"owner": "foo bar", "repo": "50% off"},
            )
            == "gitea://repos/foo%20bar/50%25%20off"
        )

    def test_simple_param_encodes_slash(self) -> None:
        assert (
            expand_path_params("/repos/{owner}/{ref}", {"owner": "o", "ref": "feature/x"})
            == "/repos/o/feature%2Fx"
        )

    def test_wildcard_param_preserves_slash(self) -> None:
        assert (
            expand_path_params(
                "/repos/{owner}/{repo}/contents/{filepath*}",
                {"owner": "o", "repo": "r", "filepath": "src/a b.py"},
            )
            == "/repos/o/r/contents/src/a%20b.py"
        )

    def test_non_string_values_are_stringified(self) -> None:
        assert expand_path_params("/pulls/{index}", {"index": 42}) == "/pulls/42"

    def test_missing_param_left_untouched(self) -> None:
        assert (
            expand_path_params("gitea://repos/{owner}/{repo}", {"owner": "o"})
            == "gitea://repos/o/{repo}"
        )

    def test_query_suffix_left_untouched(self) -> None:
        assert (
            expand_path_params("gitea://repos/{owner}/{repo}/issues{?state,type}", {"owner": "o"})
            == "gitea://repos/o/{repo}/issues{?state,type}"
        )

    @pytest.mark.parametrize(
        ("template", "params"),
        [
            ("gitea://repos/{owner}/{repo}", {"owner": "foo bar", "repo": "a/b"}),
            (
                "gitea://repos/{owner}/{repo}/contents/{filepath*}",
                {"owner": "o", "repo": "r", "filepath": "src/a b/%c.py"},
            ),
        ],
    )
    def test_round_trips_through_fastmcp_matcher(
        self, template: str, params: dict[str, str]
    ) -> None:
        """Encoding is the exact inverse of FastMCP's resource matcher."""
        expanded = expand_path_params(template, params)
        assert match_uri_template(expanded, template) == params
