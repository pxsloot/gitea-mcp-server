"""Unit tests for cache invalidation functionality."""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.server.middleware.caching import ResponseCachingMiddleware

from gitea_mcp_server import cache_invalidation as ci_module
from gitea_mcp_server.cache_invalidation import (
    TOOL_INVALIDATION_MAP,
    CacheInvalidationMiddleware,
    _compute_cache_key,
    _substitute_template,
    build_invalidation_map,
    compute_uris_to_invalidate,
    invalidate_cached_resources,
    record_write_tool,
)
from gitea_mcp_server.openapi_converter.type_references import stamp_type_references
from gitea_mcp_server.resources.surface import (
    clear_resource_surface,
    get_resource_surface,
    register_resource_surface,
)
from tests.helpers.spec_fixtures import make_openapi_spec

# ---------------------------------------------------------------------------
# Spec + surface helpers
# ---------------------------------------------------------------------------


def _get_op(op_id: str, ref: str) -> dict:
    """GET operation returning a single ``$ref`` schema."""
    return {
        "operationId": op_id,
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
                },
            }
        },
    }


def _list_op(op_id: str, ref: str) -> dict:
    """GET operation returning an array of ``$ref`` schemas."""
    return {
        "operationId": op_id,
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": f"#/components/schemas/{ref}"},
                        }
                    }
                },
            }
        },
    }


def _write_op(op_id: str, ref: str, code: str = "201") -> dict:
    """Write operation returning the created/updated ``$ref`` schema."""
    return {
        "operationId": op_id,
        "responses": {
            code: {
                "description": "ok",
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
                },
            }
        },
    }


def _empty_op(op_id: str) -> dict:
    """Write operation with a 204 (no content) response."""
    return {"operationId": op_id, "responses": {"204": {"description": "no content"}}}


def _make_spec() -> Any:
    """Build a spec with repo/issues/pulls/labels/milestones/branches/tags resources."""
    return make_openapi_spec(
        paths={
            "/repos/{owner}/{repo}": {
                "get": _get_op("repoGet", "Repository"),
                "patch": _write_op("repoEdit", "Repository", "200"),
            },
            "/repos/{owner}/{repo}/issues": {
                "get": _list_op("issueList", "Issue"),
                "post": _write_op("issueCreate", "Issue"),
            },
            "/repos/{owner}/{repo}/pulls": {
                "get": _list_op("pullList", "PullRequest"),
                "post": _write_op("pullCreate", "PullRequest"),
            },
            "/repos/{owner}/{repo}/labels": {
                "get": _list_op("labelList", "Label"),
                "post": _write_op("labelCreate", "Label"),
            },
            "/repos/{owner}/{repo}/milestones": {
                "get": _list_op("milestoneList", "Milestone"),
                "post": _write_op("milestoneCreate", "Milestone"),
            },
            "/repos/{owner}/{repo}/milestones/{id}": {
                "get": _get_op("milestoneGet", "Milestone"),
                "patch": _write_op("milestoneEdit", "Milestone", "200"),
                "delete": _empty_op("milestoneDelete"),
            },
            "/repos/{owner}/{repo}/branches": {
                "get": _list_op("branchList", "Branch"),
                "post": _write_op("branchCreate", "Branch"),
            },
            "/repos/{owner}/{repo}/branches/{branch}": {
                "get": _get_op("branchGet", "Branch"),
                "delete": _empty_op("branchDelete"),
            },
            "/repos/{owner}/{repo}/tags": {
                "get": _list_op("tagList", "Tag"),
                "post": _write_op("tagCreate", "Tag"),
            },
            "/repos/{owner}/{repo}/tags/{tag}": {
                "get": _get_op("tagGet", "Tag"),
                "delete": _empty_op("tagDelete"),
            },
        },
        components={
            "schemas": {
                "Repository": {"type": "object", "properties": {"name": {"type": "string"}}},
                "Issue": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                        "milestone": {"$ref": "#/components/schemas/Milestone"},
                    },
                },
                "PullRequest": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                        "milestone": {"$ref": "#/components/schemas/Milestone"},
                    },
                },
                "Label": {"type": "object", "properties": {"name": {"type": "string"}}},
                "Milestone": {"type": "object", "properties": {"title": {"type": "string"}}},
                "Branch": {"type": "object", "properties": {"name": {"type": "string"}}},
                "Tag": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
    )


def _register_surface() -> None:
    """Register the standard resource surface matching ``_make_spec``."""
    register_resource_surface("gitea://repos/{owner}/{repo}", "/repos/{owner}/{repo}")
    register_resource_surface("gitea://repos/{owner}/{repo}/issues", "/repos/{owner}/{repo}/issues")
    register_resource_surface("gitea://repos/{owner}/{repo}/pulls", "/repos/{owner}/{repo}/pulls")
    register_resource_surface("gitea://repos/{owner}/{repo}/labels", "/repos/{owner}/{repo}/labels")
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/milestones", "/repos/{owner}/{repo}/milestones"
    )
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/milestones/{id}",
        "/repos/{owner}/{repo}/milestones/{id}",
    )
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/branches", "/repos/{owner}/{repo}/branches"
    )
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/branches/{branch*}",
        "/repos/{owner}/{repo}/branches/{branch}",
    )
    register_resource_surface("gitea://repos/{owner}/{repo}/tags", "/repos/{owner}/{repo}/tags")
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/tags/{tag*}",
        "/repos/{owner}/{repo}/tags/{tag}",
    )


def _build_map(spec: Any, tools: list[tuple[str, str, str]]) -> None:
    """Record write tools and derive the invalidation map.

    Mirrors the production flow: the converter stamps type references
    (x-resource-types / x-modifies-type) before the derivation reads them.
    """
    if spec is not None:
        stamp_type_references(spec)
    for name, path, method in tools:
        record_write_tool(name, path, method)
    build_invalidation_map(spec)


@pytest.fixture(autouse=True)
def clear_invalidation_state() -> Generator[None, None, None]:
    """Clear the invalidation map, surface, and pending tools before each test."""
    TOOL_INVALIDATION_MAP.clear()
    clear_resource_surface()
    ci_module._PENDING_WRITE_TOOLS.clear()
    yield
    TOOL_INVALIDATION_MAP.clear()
    clear_resource_surface()
    ci_module._PENDING_WRITE_TOOLS.clear()


class TestComputeCacheKey:
    """Tests for _compute_cache_key function."""

    def test_consistent_hashing(self) -> None:
        """Same URI produces same hash."""
        uri = "gitea://repos/owner/repo/issues"
        key1 = _compute_cache_key(uri)
        key2 = _compute_cache_key(uri)
        assert key1 == key2
        assert len(key1) == 64  # SHA256 hex digest length

    def test_different_uris_different_keys(self) -> None:
        """Different URIs produce different hashes."""
        uri1 = "gitea://repos/owner/repo/issues"
        uri2 = "gitea://repos/owner/repo/pulls"
        key1 = _compute_cache_key(uri1)
        key2 = _compute_cache_key(uri2)
        assert key1 != key2


class TestSubstituteTemplate:
    """Tests for _substitute_template function."""

    def test_simple_substitution(self) -> None:
        """Basic parameter substitution."""
        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "myorg", "repo": "myrepo"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/myorg/myrepo/issues"

    def test_multiple_parameters(self) -> None:
        """Multiple parameters are all substituted."""
        template = "gitea://repos/{owner}/{repo}/contents/{filepath}"
        params = {"owner": "org", "repo": "repo", "filepath": "src/main.py"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/org/repo/contents/src/main.py"

    def test_missing_parameter_raises(self) -> None:
        """Missing required parameter raises ValueError."""
        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org"}  # missing repo
        with pytest.raises(ValueError, match="Missing parameters"):
            _substitute_template(template, params)

    def test_extra_parameters_ignored(self) -> None:
        """Extra parameters not in template are ignored."""
        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org", "repo": "repo", "extra": "ignored"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/org/repo/issues"

    def test_wildcard_parameter(self) -> None:
        """Wildcard parameters are handled."""
        template = "gitea://repos/{owner}/{repo}/contents/{filepath*}"
        params = {"owner": "org", "repo": "repo", "filepath": "docs/guide/intro.md"}
        result = _substitute_template(template, params)
        assert result == "gitea://repos/org/repo/contents/docs/guide/intro.md"


class TestComputeUrisToInvalidate:
    """Tests for compute_uris_to_invalidate function."""

    def test_issue_edit_invalidates_issues(self) -> None:
        """issue_edit_issue invalidates issues list."""
        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]
        arguments = {"owner": "myorg", "repo": "myrepo", "index": 42}
        uris = compute_uris_to_invalidate("issue_edit_issue", arguments)
        assert uris == ["gitea://repos/myorg/myrepo/issues"]

    def test_issue_create_invalidates_issues(self) -> None:
        """issue_create_repo_issue invalidates issues list."""
        TOOL_INVALIDATION_MAP["issue_create_repo_issue"] = ["gitea://repos/{owner}/{repo}/issues"]
        arguments = {"owner": "org", "repo": "repo", "title": "Bug"}
        uris = compute_uris_to_invalidate("issue_create_repo_issue", arguments)
        assert uris == ["gitea://repos/org/repo/issues"]

    def test_pr_create_invalidates_pulls(self) -> None:
        """pull_request_create invalidates pulls list."""
        TOOL_INVALIDATION_MAP["pull_request_create"] = ["gitea://repos/{owner}/{repo}/pulls"]
        arguments = {"owner": "org", "repo": "repo", "head": "feature", "base": "main"}
        uris = compute_uris_to_invalidate("pull_request_create", arguments)
        assert uris == ["gitea://repos/org/repo/pulls"]

    def test_unknown_tool_returns_empty(self) -> None:
        """Unknown tool returns empty list."""
        arguments = {"owner": "org", "repo": "repo"}
        uris = compute_uris_to_invalidate("unknown_tool", arguments)
        assert uris == []

    def test_repo_edit_invalidates_repo_resource(self) -> None:
        """repo_edit invalidates repository resource."""
        TOOL_INVALIDATION_MAP["repo_edit"] = ["gitea://repos/{owner}/{repo}"]
        arguments = {"owner": "org", "repo": "repo"}
        uris = compute_uris_to_invalidate("repo_edit", arguments)
        assert uris == ["gitea://repos/org/repo"]

    def test_file_operation_invalidates_file_resource(self) -> None:
        """repo_create_content invalidates file resource with correct path."""
        TOOL_INVALIDATION_MAP["repo_create_content"] = [
            "gitea://repos/{owner}/{repo}/contents/{filepath*}"
        ]
        arguments = {
            "owner": "org",
            "repo": "repo",
            "filepath": "README.md",
        }
        uris = compute_uris_to_invalidate("repo_create_content", arguments)
        assert "gitea://repos/org/repo/contents/README.md" in uris

    def test_missing_parameters_skipped(self) -> None:
        """If required parameters are missing, template is skipped gracefully."""
        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]
        arguments = {"owner": "org"}  # missing repo
        uris = compute_uris_to_invalidate("issue_edit_issue", arguments)
        assert uris == []

    def test_prefix_stripping(self) -> None:
        """Namespaced tool names are stripped before map lookup."""
        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]
        arguments = {"owner": "org", "repo": "repo", "index": 1}
        uris = compute_uris_to_invalidate("gitea_issue_edit_issue", arguments, tool_prefix="gitea_")
        assert uris == ["gitea://repos/org/repo/issues"]

    @pytest.mark.asyncio
    async def test_empty_uris_list_noop(self) -> None:
        """invalidate_cached_resources with empty list returns immediately."""
        mock_caching = MagicMock()
        await invalidate_cached_resources(mock_caching, [], "test_tool")

    @pytest.mark.asyncio
    async def test_cache_delete_key_error_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """KeyError during cache delete is caught and logged."""
        import logging

        caplog.set_level(logging.WARNING)

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()  # exists
        mock_cache.delete.side_effect = KeyError("cache key not found")

        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]
        uris = compute_uris_to_invalidate(
            "issue_edit_issue", {"owner": "org", "repo": "repo", "index": 1}
        )

        await invalidate_cached_resources(mock_caching, uris, "issue_edit_issue")
        assert "Failed to invalidate cache" in caplog.text


class TestDeriveTargets:
    """Tests for the spec + surface invalidation derivation (issue #743)."""

    def test_issue_write_full_prefix(self) -> None:
        """An issue write invalidates the repo resource too (full prefix)."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("issueCreate", "/repos/{owner}/{repo}/issues", "POST")])
        assert set(TOOL_INVALIDATION_MAP["issueCreate"]) == {
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/issues",
        }

    def test_label_write_cross_tree(self) -> None:
        """A label write invalidates issues/pulls (Issue references Label)."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("labelCreate", "/repos/{owner}/{repo}/labels", "POST")])
        assert set(TOOL_INVALIDATION_MAP["labelCreate"]) == {
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/labels",
            "gitea://repos/{owner}/{repo}/issues",
            "gitea://repos/{owner}/{repo}/pulls",
        }

    def test_milestone_write_cross_tree(self) -> None:
        """A milestone write invalidates issues/pulls and the milestone resources."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("milestoneCreate", "/repos/{owner}/{repo}/milestones", "POST")])
        assert set(TOOL_INVALIDATION_MAP["milestoneCreate"]) == {
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/milestones",
            "gitea://repos/{owner}/{repo}/milestones/{id}",
            "gitea://repos/{owner}/{repo}/issues",
            "gitea://repos/{owner}/{repo}/pulls",
        }

    def test_milestone_edit_covers_single_milestone(self) -> None:
        """A milestone edit invalidates the single-milestone resource too."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("milestoneEdit", "/repos/{owner}/{repo}/milestones/{id}", "PATCH")])
        assert (
            "gitea://repos/{owner}/{repo}/milestones/{id}" in TOOL_INVALIDATION_MAP["milestoneEdit"]
        )

    def test_branch_create_covers_branch_list(self) -> None:
        """A branch create invalidates the branches list resource."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("branchCreate", "/repos/{owner}/{repo}/branches", "POST")])
        assert "gitea://repos/{owner}/{repo}/branches" in TOOL_INVALIDATION_MAP["branchCreate"]

    def test_branch_delete_covers_single_branch(self) -> None:
        """A branch delete invalidates the single-branch resource (wildcard template)."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("branchDelete", "/repos/{owner}/{repo}/branches/{branch}", "DELETE")])
        assert (
            "gitea://repos/{owner}/{repo}/branches/{branch*}"
            in TOOL_INVALIDATION_MAP["branchDelete"]
        )

    def test_tag_create_covers_tag_list(self) -> None:
        """A tag create invalidates the tags list resource."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("tagCreate", "/repos/{owner}/{repo}/tags", "POST")])
        assert "gitea://repos/{owner}/{repo}/tags" in TOOL_INVALIDATION_MAP["tagCreate"]

    def test_tag_delete_covers_single_tag(self) -> None:
        """A tag delete invalidates the single-tag resource."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("tagDelete", "/repos/{owner}/{repo}/tags/{tag}", "DELETE")])
        assert "gitea://repos/{owner}/{repo}/tags/{tag*}" in TOOL_INVALIDATION_MAP["tagDelete"]

    def test_repo_edit_only_repo(self) -> None:
        """A repo edit invalidates only the repo resource (no cross-tree)."""
        spec = _make_spec()
        _register_surface()
        _build_map(spec, [("repoEdit", "/repos/{owner}/{repo}", "PATCH")])
        assert TOOL_INVALIDATION_MAP["repoEdit"] == ["gitea://repos/{owner}/{repo}"]

    def test_safe_method_not_recorded(self) -> None:
        """GET tools are not recorded and produce no invalidation targets."""
        spec = _make_spec()
        _register_surface()
        record_write_tool("issueList", "/repos/{owner}/{repo}/issues", "GET")
        build_invalidation_map(spec)
        assert "issueList" not in TOOL_INVALIDATION_MAP

    def test_no_spec_skips_cross_tree(self) -> None:
        """Without a spec, only path-prefix targets are derived."""
        _register_surface()
        _build_map(None, [("labelCreate", "/repos/{owner}/{repo}/labels", "POST")])
        assert set(TOOL_INVALIDATION_MAP["labelCreate"]) == {
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/labels",
        }

    def test_non_dict_path_item_skipped(self) -> None:
        """A non-dict path item in the spec is skipped without error.

        Path-prefix derivation still matches (it needs no spec); the
        cross-tree lookup degrades to an empty type set.
        """
        spec = make_openapi_spec(paths={"/weird": "not-a-dict"})
        register_resource_surface("gitea://weird", "/weird")
        _build_map(spec, [("weirdWrite", "/weird", "POST")])
        assert TOOL_INVALIDATION_MAP["weirdWrite"] == ["gitea://weird"]


class TestDrift:
    """The invalidation map must never drift from the registered surface."""

    def test_every_invalidation_target_matches_registered_resource(self) -> None:
        """Every derived invalidation URI template is a registered resource."""
        spec = _make_spec()
        _register_surface()
        surface = get_resource_surface()
        tools = [
            ("issueCreate", "/repos/{owner}/{repo}/issues", "POST"),
            ("labelCreate", "/repos/{owner}/{repo}/labels", "POST"),
            ("milestoneCreate", "/repos/{owner}/{repo}/milestones", "POST"),
            ("milestoneEdit", "/repos/{owner}/{repo}/milestones/{id}", "PATCH"),
            ("branchCreate", "/repos/{owner}/{repo}/branches", "POST"),
            ("branchDelete", "/repos/{owner}/{repo}/branches/{branch}", "DELETE"),
            ("tagCreate", "/repos/{owner}/{repo}/tags", "POST"),
            ("tagDelete", "/repos/{owner}/{repo}/tags/{tag}", "DELETE"),
            ("repoEdit", "/repos/{owner}/{repo}", "PATCH"),
        ]
        _build_map(spec, tools)
        assert TOOL_INVALIDATION_MAP, "expected at least one derived target"
        for tool, targets in TOOL_INVALIDATION_MAP.items():
            for target in targets:
                assert target in surface, (
                    f"{tool} invalidation target {target!r} is not a registered resource"
                )


class TestCacheInvalidationMiddleware:
    """Tests for CacheInvalidationMiddleware behavior."""

    @pytest.mark.asyncio
    async def test_successful_tool_invalidates_cache(self) -> None:
        """Successful tool call triggers cache invalidation."""
        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        assert mock_cache.delete.called

    @pytest.mark.asyncio
    async def test_error_tool_no_invalidation(self) -> None:
        """Failed tool call does not invalidate cache."""
        mock_cache = AsyncMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=True)

        await middleware.on_call_tool(mock_context, mock_call_next)

        assert not mock_cache.delete.called

    @pytest.mark.asyncio
    async def test_unknown_tool_no_invalidation(self) -> None:
        """Tool not in invalidation map does not trigger invalidation."""
        mock_cache = AsyncMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "some_unknown_tool"
        mock_context.message.arguments = {}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        assert not mock_cache.delete.called

    @pytest.mark.asyncio
    async def test_missing_read_resource_cache_graceful(self) -> None:
        """Graceful degradation when _read_resource_cache attribute is missing."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        del mock_caching._read_resource_cache

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

    @pytest.mark.asyncio
    async def test_invalidate_cached_resources_missing_attribute(self) -> None:
        """invalidate_cached_resources handles missing _read_resource_cache gracefully."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        del mock_caching._read_resource_cache

        await invalidate_cached_resources(
            mock_caching, ["gitea://repos/org/repo/issues"], "test_tool"
        )


class TestQueryVariantInvalidation:
    """Query-variant reads are recorded and invalidated with their base URI."""

    @pytest.mark.asyncio
    async def test_read_records_query_variant(self) -> None:
        """on_read_resource records the full URI under its base."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.uri = "gitea://repos/org/repo/issues?state=open"

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock()

        await middleware.on_read_resource(mock_context, mock_call_next)

        assert middleware._read_uris["gitea://repos/org/repo/issues"] == {
            "gitea://repos/org/repo/issues?state=open"
        }

    @pytest.mark.asyncio
    async def test_read_without_query_records_base(self) -> None:
        """A plain read records the base URI itself."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.uri = "gitea://repos/org/repo/issues"

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock()

        await middleware.on_read_resource(mock_context, mock_call_next)

        assert middleware._read_uris["gitea://repos/org/repo/issues"] == {
            "gitea://repos/org/repo/issues"
        }

    @pytest.mark.asyncio
    async def test_read_uri_dedup(self) -> None:
        """Re-reading the same URI does not double-count it."""
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        middleware = CacheInvalidationMiddleware(mock_caching)

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock()

        for _ in range(3):
            mock_context = MagicMock()
            mock_context.message.uri = "gitea://repos/org/repo/issues?state=open"
            await middleware.on_read_resource(mock_context, mock_call_next)

        assert middleware._read_uri_count == 1
        assert middleware._read_uris["gitea://repos/org/repo/issues"] == {
            "gitea://repos/org/repo/issues?state=open"
        }

    @pytest.mark.asyncio
    async def test_read_uris_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The read-URI registry is bounded — oldest bases are evicted."""
        from gitea_mcp_server import cache_invalidation as ci_module

        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        middleware = CacheInvalidationMiddleware(mock_caching)
        monkeypatch.setattr(ci_module, "_MAX_READ_URIS", 3)

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock()

        for i in range(5):
            mock_context = MagicMock()
            mock_context.message.uri = f"gitea://repos/org/repo/res{i}"
            await middleware.on_read_resource(mock_context, mock_call_next)

        # Only the 3 most recent bases survive; the 2 oldest were evicted.
        assert set(middleware._read_uris.keys()) == {
            "gitea://repos/org/repo/res2",
            "gitea://repos/org/repo/res3",
            "gitea://repos/org/repo/res4",
        }
        assert middleware._read_uri_count == 3

    @pytest.mark.asyncio
    async def test_read_uris_bounded_single_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single base with many variants cannot exceed the cap."""
        from gitea_mcp_server import cache_invalidation as ci_module

        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        middleware = CacheInvalidationMiddleware(mock_caching)
        monkeypatch.setattr(ci_module, "_MAX_READ_URIS", 3)

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock()

        for i in range(5):
            mock_context = MagicMock()
            mock_context.message.uri = f"gitea://repos/org/repo/issues?page={i}"
            await middleware.on_read_resource(mock_context, mock_call_next)

        total = sum(len(v) for v in middleware._read_uris.values())
        assert total <= 3
        assert middleware._read_uri_count <= 3

    @pytest.mark.asyncio
    async def test_write_invalidates_query_variants(self) -> None:
        """A write clears the base URI and every recorded query variant."""
        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching)
        middleware._read_uris["gitea://repos/org/repo/issues"].add(
            "gitea://repos/org/repo/issues?state=open"
        )
        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        deleted_uris = [call[1]["key"] for call in mock_cache.delete.call_args_list]
        assert _compute_cache_key("gitea://repos/org/repo/issues") in deleted_uris
        assert _compute_cache_key("gitea://repos/org/repo/issues?state=open") in deleted_uris


class TestIntegration:
    """Integration tests for cache invalidation."""

    @pytest.mark.asyncio
    async def test_close_issue_invalidates_resources(self) -> None:
        """Closing an issue via issue_edit_issue invalidates relevant caches."""
        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]

        middleware = CacheInvalidationMiddleware(mock_caching)

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {
            "owner": "testorg",
            "repo": "testrepo",
            "index": 5,
            "state": "closed",
        }

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        deleted_uris = [call[1]["key"] for call in mock_cache.delete.call_args_list]
        expected_key = _compute_cache_key("gitea://repos/testorg/testrepo/issues")
        assert deleted_uris == [expected_key]


class TestClearLabelServiceCache:
    """Tests for CacheInvalidationMiddleware._clear_label_service_cache."""

    @pytest.mark.asyncio
    async def test_label_uri_clears_label_cache(self) -> None:
        """URI ending with /labels clears LabelService cache for that repo."""
        from unittest.mock import AsyncMock, MagicMock

        from gitea_mcp_server.label_service import LabelService

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        label_service = MagicMock(spec=LabelService)
        middleware = CacheInvalidationMiddleware(mock_caching, label_service=label_service)

        TOOL_INVALIDATION_MAP["repo_create_label"] = ["gitea://repos/{owner}/{repo}/labels"]

        mock_context = MagicMock()
        mock_context.message.name = "repo_create_label"
        mock_context.message.arguments = {"owner": "myorg", "repo": "myrepo"}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        # LabelService cache should be cleared for myorg/myrepo
        label_service.clear_cache_for.assert_called_once_with("myorg", "myrepo")

    @pytest.mark.asyncio
    async def test_non_label_uri_does_not_clear_label_cache(self) -> None:
        """URI not ending with /labels does not clear LabelService cache."""
        from unittest.mock import AsyncMock, MagicMock

        from gitea_mcp_server.label_service import LabelService

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        label_service = MagicMock(spec=LabelService)
        middleware = CacheInvalidationMiddleware(mock_caching, label_service=label_service)

        TOOL_INVALIDATION_MAP["issue_edit_issue"] = ["gitea://repos/{owner}/{repo}/issues"]

        mock_context = MagicMock()
        mock_context.message.name = "issue_edit_issue"
        mock_context.message.arguments = {"owner": "org", "repo": "repo", "index": 1}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_label_service_skips_gracefully(self) -> None:
        """When label_service is None, no error is raised."""
        from unittest.mock import AsyncMock, MagicMock

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching, label_service=None)

        TOOL_INVALIDATION_MAP["repo_create_label"] = ["gitea://repos/{owner}/{repo}/labels"]

        mock_context = MagicMock()
        mock_context.message.name = "repo_create_label"
        mock_context.message.arguments = {"owner": "org", "repo": "repo"}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        # Should not raise even though label_service is None
        await middleware.on_call_tool(mock_context, mock_call_next)

    # ------------------------------------------------------------------
    # URI parsing edge cases — tested through on_call_tool with patched
    # compute_uris_to_invalidate to inject edge-case URIs that cannot
    # arise from the normal invalidation system (all registered patterns
    # produce well-formed gitea://repos/.../labels URIs).
    # ------------------------------------------------------------------

    def _make_middleware(self, label_service: Any = None) -> CacheInvalidationMiddleware:
        """Helper: create CacheInvalidationMiddleware with a mock cache and optional label_service."""
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache
        return CacheInvalidationMiddleware(mock_caching, label_service=label_service)

    def _make_context_and_call_next(
        self, tool_name: str = "test_label_tool", arguments: dict[str, Any] | None = None
    ) -> tuple[MagicMock, Any]:
        """Helper: create mock context and call_next for on_call_tool tests."""
        from unittest.mock import MagicMock

        mock_context = MagicMock()
        mock_context.message.name = tool_name
        mock_context.message.arguments = arguments or {}

        async def mock_call_next(context: Any) -> MagicMock:
            return MagicMock(is_error=False)

        return mock_context, mock_call_next

    @pytest.mark.asyncio
    async def test_uri_too_few_parts_skips(self) -> None:
        """URI with fewer than _MIN_LABEL_URI_PARTS parts does not call clear_cache_for."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        TOOL_INVALIDATION_MAP["test_label_tool"] = ["gitea://repos/{owner}/{repo}/labels"]
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_not_gitea_scheme_skips(self) -> None:
        """URI where parts[1] is not empty string skips.

        URIs like ``gitea:repos/owner/repo/labels`` (missing ``//``) produce
        fewer parts or no empty string at ``parts[1]``.
        """
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        TOOL_INVALIDATION_MAP["test_label_tool"] = ["gitea://repos/{owner}/{repo}/labels"]
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea:repos/owner/repo/labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_not_repos_segment_skips(self) -> None:
        """URI where parts[2] is not 'repos' skips."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        TOOL_INVALIDATION_MAP["test_label_tool"] = ["gitea://repos/{owner}/{repo}/labels"]
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://user/owner/repo/labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_with_empty_owner_skips(self) -> None:
        """URI with empty owner (parts[3] == '') skips."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        TOOL_INVALIDATION_MAP["test_label_tool"] = ["gitea://repos/{owner}/{repo}/labels"]
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://repos//repo/labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    @pytest.mark.asyncio
    async def test_uri_with_empty_repo_skips(self) -> None:
        """URI with empty repo (parts[4] == '') skips."""
        from unittest.mock import patch

        from gitea_mcp_server.label_service import LabelService

        TOOL_INVALIDATION_MAP["test_label_tool"] = ["gitea://repos/{owner}/{repo}/labels"]
        label_service = MagicMock(spec=LabelService)
        middleware = self._make_middleware(label_service=label_service)
        mock_context, mock_call_next = self._make_context_and_call_next()

        with patch(
            "gitea_mcp_server.cache_invalidation.compute_uris_to_invalidate",
            return_value=["gitea://repos/owner//labels"],
        ):
            await middleware.on_call_tool(mock_context, mock_call_next)

        label_service.clear_cache_for.assert_not_called()

    def test_clear_label_service_cache_with_none_label_service(self) -> None:
        """Calling _clear_label_service_cache directly with label_service=None is a no-op."""
        from unittest.mock import AsyncMock, MagicMock

        from fastmcp.server.middleware.caching import ResponseCachingMiddleware

        mock_cache = AsyncMock()
        mock_cache.get.return_value = MagicMock()
        mock_caching = MagicMock(spec=ResponseCachingMiddleware)
        mock_caching._read_resource_cache = mock_cache

        middleware = CacheInvalidationMiddleware(mock_caching, label_service=None)
        # Direct call to the inner guard method — should not raise
        middleware._clear_label_service_cache(
            ["gitea://repos/org/repo/labels"], {"owner": "org", "repo": "repo"}
        )
