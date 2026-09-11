"""Integration tests for cache invalidation with the MCP server.

These tests verify that write operations properly invalidate cached resources
by using respx to mock the Gitea API and observing cache behavior.

The cache is project-owned (``response_cache.py``, issue #755): the
tautological "key matches FastMCP format" test is gone because there is no
FastMCP key format to match — the key format is owned here, so nothing can
drift.  End-to-end staleness is covered by the read → write → read regression
in ``test_tool_edge_cases.py`` (base URI) and ``TestQueryVariantStaleness``
below (query variants).
"""

from collections.abc import Generator
from typing import Any

import pytest
import respx

from gitea_mcp_server.cache_invalidation import (
    TOOL_INVALIDATION_MAP,
    build_invalidation_map,
    compute_uris_to_invalidate,
    record_write_tool,
)
from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.openapi_converter.type_references import stamp_type_references
from gitea_mcp_server.resources.surface import (
    clear_resource_surface,
    register_resource_surface,
)
from gitea_mcp_server.server import create_mcp_server
from tests.conftest import SimpleConfig
from tests.helpers.spec_fixtures import make_openapi_spec

BASE_TEST_URL = "https://git.example.com"


def _get_op(op_id: str, ref: str) -> dict:
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


def _make_spec() -> Any:
    """Build a spec with repo/issues/pulls/labels resources + write ops."""
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
            "/repos/{owner}/{repo}/contents/{filepath}": {
                "get": _get_op("repoGetContents", "FileContentsResponse"),
                "put": _write_op("repoCreateContent", "FileContentsResponse", "201"),
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
                    },
                },
                "Label": {"type": "object", "properties": {"name": {"type": "string"}}},
                "FileContentsResponse": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        },
    )


def _register_surface() -> None:
    register_resource_surface("gitea://repos/{owner}/{repo}", "/repos/{owner}/{repo}")
    register_resource_surface("gitea://repos/{owner}/{repo}/issues", "/repos/{owner}/{repo}/issues")
    register_resource_surface("gitea://repos/{owner}/{repo}/pulls", "/repos/{owner}/{repo}/pulls")
    register_resource_surface("gitea://repos/{owner}/{repo}/labels", "/repos/{owner}/{repo}/labels")
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/contents/{filepath*}",
        "/repos/{owner}/{repo}/contents/{filepath}",
    )
    # Convenience wrapper: concrete api_path, non-spec-mirror URI.
    register_resource_surface(
        "gitea://repos/{owner}/{repo}/readme",
        "/repos/{owner}/{repo}/contents/README.md",
    )


@pytest.fixture(autouse=True)
def clear_invalidation_state() -> Generator[None, None, None]:
    """Clear the invalidation map, surface, and pending tools before each test."""
    from gitea_mcp_server import cache_invalidation as ci_module

    TOOL_INVALIDATION_MAP.clear()
    clear_resource_surface()
    ci_module._PENDING_WRITE_TOOLS.clear()
    yield
    TOOL_INVALIDATION_MAP.clear()
    clear_resource_surface()
    ci_module._PENDING_WRITE_TOOLS.clear()


class TestCacheInvalidationIntegration:
    """Integration tests for cache invalidation using respx mocks."""

    @pytest.mark.asyncio
    async def test_issue_edit_invalidation_mapping(self) -> None:
        """Test that issue_edit_issue is mapped to invalidate issues resources."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        record_write_tool("issue_edit_issue", "/repos/{owner}/{repo}/issues/{index}", "PATCH")
        build_invalidation_map(spec)

        arguments = {"owner": "org", "repo": "repo", "index": 1}
        uris = compute_uris_to_invalidate("issue_edit_issue", arguments)

        expected = {
            "gitea://repos/org/repo",
            "gitea://repos/org/repo/issues",
        }
        assert set(uris) == expected

    @pytest.mark.asyncio
    async def test_pr_create_invalidation_mapping(self) -> None:
        """Test that PR creation invalidates pulls resources."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        record_write_tool("repoCreatePullRequest", "/repos/{owner}/{repo}/pulls", "POST")
        build_invalidation_map(spec)

        arguments = {"owner": "org", "repo": "repo", "head": "feature", "base": "main"}
        uris = compute_uris_to_invalidate("repoCreatePullRequest", arguments)

        expected = {
            "gitea://repos/org/repo",
            "gitea://repos/org/repo/pulls",
        }
        assert set(uris) == expected

    @pytest.mark.asyncio
    async def test_repo_edit_invalidation_mapping(self) -> None:
        """Test that repo edit invalidates repo resource."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        record_write_tool("repo_edit", "/repos/{owner}/{repo}", "PATCH")
        build_invalidation_map(spec)

        arguments = {"owner": "myorg", "repo": "myrepo"}
        uris = compute_uris_to_invalidate("repo_edit", arguments)
        assert uris == ["gitea://repos/myorg/myrepo"]

    @pytest.mark.asyncio
    async def test_file_content_invalidation_mapping(self) -> None:
        """Test file content operations use filepath parameter correctly."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        record_write_tool("repo_create_content", "/repos/{owner}/{repo}/contents/{filepath}", "PUT")
        build_invalidation_map(spec)

        arguments = {
            "owner": "org",
            "repo": "repo",
            "filepath": "README.md",
            "content": "new content",
        }
        uris = compute_uris_to_invalidate("repo_create_content", arguments)
        # The contents resource AND the readme convenience wrapper (whose
        # api_path is the concrete README.md path) are invalidated.
        assert "gitea://repos/org/repo/contents/README.md" in uris
        assert "gitea://repos/org/repo/readme" in uris

    @pytest.mark.asyncio
    async def test_label_operations_invalidation(self) -> None:
        """Test label CRUD invalidates labels, issues, and pulls."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        record_write_tool("label_create", "/repos/{owner}/{repo}/labels", "POST")
        build_invalidation_map(spec)

        assert set(TOOL_INVALIDATION_MAP["label_create"]) == {
            "gitea://repos/{owner}/{repo}",
            "gitea://repos/{owner}/{repo}/labels",
            "gitea://repos/{owner}/{repo}/issues",
            "gitea://repos/{owner}/{repo}/pulls",
        }

    @pytest.mark.asyncio
    async def test_path_based_pattern_mapping_coverage(self) -> None:
        """Comprehensive test of derived invalidation coverage."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)

        tools = [
            ("issue_create", "/repos/{owner}/{repo}/issues", "POST"),
            ("issue_delete", "/repos/{owner}/{repo}/issues/{index}", "DELETE"),
            ("pull_create", "/repos/{owner}/{repo}/pulls", "POST"),
            ("pull_merge", "/repos/{owner}/{repo}/pulls/{index}/merge", "POST"),
            ("repo_edit", "/repos/{owner}/{repo}", "PATCH"),
            ("file_put", "/repos/{owner}/{repo}/contents/{filepath}", "PUT"),
            ("label_create", "/repos/{owner}/{repo}/labels", "POST"),
        ]
        for name, path, method in tools:
            record_write_tool(name, path, method)
        build_invalidation_map(spec)

        # Issues
        assert "gitea://repos/{owner}/{repo}/issues" in TOOL_INVALIDATION_MAP["issue_create"]
        assert "gitea://repos/{owner}/{repo}/issues" in TOOL_INVALIDATION_MAP["issue_delete"]
        # Pulls (merge has no response type — path-prefix still covers the list)
        assert "gitea://repos/{owner}/{repo}/pulls" in TOOL_INVALIDATION_MAP["pull_create"]
        assert "gitea://repos/{owner}/{repo}/pulls" in TOOL_INVALIDATION_MAP["pull_merge"]
        # Repo (full prefix: issue writes invalidate the repo resource too)
        assert "gitea://repos/{owner}/{repo}" in TOOL_INVALIDATION_MAP["issue_create"]
        # Files + readme wrapper
        assert (
            "gitea://repos/{owner}/{repo}/contents/{filepath*}" in TOOL_INVALIDATION_MAP["file_put"]
        )
        assert "gitea://repos/{owner}/{repo}/readme" in TOOL_INVALIDATION_MAP["file_put"]
        # Labels cross-tree
        assert "gitea://repos/{owner}/{repo}/issues" in TOOL_INVALIDATION_MAP["label_create"]
        assert "gitea://repos/{owner}/{repo}/pulls" in TOOL_INVALIDATION_MAP["label_create"]

    @pytest.mark.asyncio
    async def test_safe_methods_not_recorded(self) -> None:
        """Safe methods (GET, HEAD, OPTIONS) do not produce invalidation."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        record_write_tool("issue_list", "/repos/{owner}/{repo}/issues", "GET")
        record_write_tool("issue_head", "/repos/{owner}/{repo}/issues", "HEAD")
        record_write_tool("issue_options", "/repos/{owner}/{repo}/issues", "OPTIONS")
        build_invalidation_map(spec)
        assert "issue_list" not in TOOL_INVALIDATION_MAP
        assert "issue_head" not in TOOL_INVALIDATION_MAP
        assert "issue_options" not in TOOL_INVALIDATION_MAP


class TestTemplateSubstitution:
    """Test URI template substitution logic."""

    def test_simple_substitution(self) -> None:
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "myorg", "repo": "myrepo"}
        assert _substitute_template(template, params) == "gitea://repos/myorg/myrepo/issues"

    def test_filepath_substitution(self) -> None:
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/contents/{filepath}"
        params = {"owner": "org", "repo": "repo", "filepath": "src/main.py"}
        assert (
            _substitute_template(template, params) == "gitea://repos/org/repo/contents/src/main.py"
        )

    def test_missing_parameter_raises(self) -> None:
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org"}  # missing repo
        with pytest.raises(ValueError, match="Missing parameters"):
            _substitute_template(template, params)

    def test_extra_parameters_ignored(self) -> None:
        from gitea_mcp_server.cache_invalidation import _substitute_template

        template = "gitea://repos/{owner}/{repo}/issues"
        params = {"owner": "org", "repo": "repo", "extra": "ignored"}
        assert _substitute_template(template, params) == "gitea://repos/org/repo/issues"


class TestToolInvalidationCoverage:
    """Test that all important write tools are covered."""

    def test_pr_write_tools_are_mapped(self) -> None:
        """All PR write operations should have invalidation targets."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        pr_write_paths = [
            "/repos/{owner}/{repo}/pulls",
            "/repos/{owner}/{repo}/pulls/{index}",
            "/repos/{owner}/{repo}/pulls/{index}/merge",
            "/repos/{owner}/{repo}/pulls/{index}/close",
        ]
        for i, path in enumerate(pr_write_paths):
            record_write_tool(f"pr_write_{i}", path, "POST")
        build_invalidation_map(spec)
        for i in range(len(pr_write_paths)):
            assert "gitea://repos/{owner}/{repo}/pulls" in TOOL_INVALIDATION_MAP[f"pr_write_{i}"]

    def test_repo_write_tools_are_mapped(self) -> None:
        """Repository write operations should invalidate repo resource."""
        spec = _make_spec()
        _register_surface()
        stamp_type_references(spec)
        paths_and_methods = [
            ("/repos/{owner}/{repo}", "PUT"),
            ("/repos/{owner}/{repo}", "DELETE"),
            ("/repos/{owner}/{repo}/contents/{filepath}", "PUT"),
        ]
        for i, (path, method) in enumerate(paths_and_methods):
            record_write_tool(f"repo_write_{i}", path, method)
        build_invalidation_map(spec)
        for i in range(len(paths_and_methods)):
            assert "gitea://repos/{owner}/{repo}" in TOOL_INVALIDATION_MAP[f"repo_write_{i}"]


# ---------------------------------------------------------------------------
# End-to-end: full server startup derives a correct invalidation map
# ---------------------------------------------------------------------------

# Swagger 2.0 spec with GET + write ops and cross-referencing schemas
# (Issue references Label) so both derivation rules are exercised through
# the real wiring: provider mcp_component_fn → record_write_tool →
# register_all_resources → build_invalidation_map.
E2E_SWAGGER_SPEC = {
    "swagger": "2.0",
    "info": {"title": "Gitea API", "version": "1.0"},
    "basePath": "/api/v1",
    "paths": {
        "/repos/{owner}/{repo}": {
            "get": {
                "operationId": "repoGet",
                "summary": "Get a repository",
                "responses": {
                    "200": {"description": "ok", "schema": {"$ref": "#/definitions/Repository"}}
                },
            },
            "patch": {
                "operationId": "repoEdit",
                "summary": "Edit a repository",
                "responses": {
                    "200": {"description": "ok", "schema": {"$ref": "#/definitions/Repository"}}
                },
            },
        },
        "/repos/{owner}/{repo}/issues": {
            "get": {
                "operationId": "issueList",
                "summary": "List issues",
                "responses": {
                    "200": {
                        "description": "ok",
                        "schema": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/Issue"},
                        },
                    }
                },
            },
            "post": {
                "operationId": "issueCreate",
                "summary": "Create an issue",
                "responses": {
                    "201": {"description": "ok", "schema": {"$ref": "#/definitions/Issue"}}
                },
            },
        },
        "/repos/{owner}/{repo}/labels": {
            "get": {
                "operationId": "labelList",
                "summary": "List labels",
                "responses": {
                    "200": {
                        "description": "ok",
                        "schema": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/Label"},
                        },
                    }
                },
            },
            "post": {
                "operationId": "labelCreate",
                "summary": "Create a label",
                "responses": {
                    "201": {"description": "ok", "schema": {"$ref": "#/definitions/Label"}}
                },
            },
        },
    },
    "definitions": {
        "Repository": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Issue": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "labels": {"type": "array", "items": {"$ref": "#/definitions/Label"}},
            },
        },
        "Label": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
}


class TestEndToEndInvalidationMap:
    """Full server startup derives a correct invalidation map (issue #743).

    Guards the wiring that the unit/integration tests above mock by hand:
    if ``record_write_tool`` stops firing (provider hook regression) or
    ``build_invalidation_map`` runs before resource registration, the map
    would be silently empty and every other test would still pass.
    """

    @pytest.mark.asyncio
    async def test_server_startup_derives_invalidation_map(self) -> None:
        """create_mcp_server wires record_write_tool → surface → build_invalidation_map."""
        from gitea_mcp_server import cache_invalidation as ci_module
        from gitea_mcp_server.resources.surface import get_resource_surface

        config = SimpleConfig(url=BASE_TEST_URL, token="test_token")
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(200, json=E2E_SWAGGER_SPEC)
            await create_mcp_server(gitea_client)

        surface = get_resource_surface()
        assert surface, "expected a registered resource surface after startup"

        assert ci_module.TOOL_INVALIDATION_MAP, (
            "expected a non-empty invalidation map after startup — "
            "record_write_tool / build_invalidation_map wiring is broken"
        )

        # Drift: every derived target must be a registered resource.
        for tool, targets in ci_module.TOOL_INVALIDATION_MAP.items():
            for target in targets:
                assert target in surface, (
                    f"{tool} invalidation target {target!r} is not a registered resource"
                )

        # Spot-check the derivation rules through the real wiring.
        # Path-prefix: an issue write invalidates the repo resource too.
        assert "gitea://repos/{owner}/{repo}" in ci_module.TOOL_INVALIDATION_MAP["issue_create"]
        assert (
            "gitea://repos/{owner}/{repo}/issues" in ci_module.TOOL_INVALIDATION_MAP["issue_create"]
        )
        # Cross-tree: a label write invalidates issues (Issue references Label).
        assert (
            "gitea://repos/{owner}/{repo}/issues" in ci_module.TOOL_INVALIDATION_MAP["label_create"]
        )


# ---------------------------------------------------------------------------
# End-to-end: query-variant staleness regression (issue #755)
# ---------------------------------------------------------------------------

# Swagger spec with an issues resource that accepts a ``state`` query param
# and an issue-edit write tool.  The write must clear the cached
# ``?state=open`` variant, not just the base URI.
QUERY_VARIANT_SWAGGER_SPEC = {
    "swagger": "2.0",
    "info": {"title": "Gitea API", "version": "1.0"},
    "basePath": "/api/v1",
    "paths": {
        "/repos/{owner}/{repo}/issues": {
            "get": {
                "operationId": "issueList",
                "summary": "List issues",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "type": "string"},
                    {"name": "repo", "in": "path", "required": True, "type": "string"},
                    {"name": "state", "in": "query", "required": False, "type": "string"},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "schema": {
                            "type": "array",
                            "items": {"$ref": "#/definitions/Issue"},
                        },
                    }
                },
            },
        },
        "/repos/{owner}/{repo}/issues/{index}": {
            "patch": {
                "operationId": "issueEdit",
                "summary": "Edit an issue",
                "parameters": [
                    {"name": "owner", "in": "path", "required": True, "type": "string"},
                    {"name": "repo", "in": "path", "required": True, "type": "string"},
                    {"name": "index", "in": "path", "required": True, "type": "integer"},
                    {
                        "name": "body",
                        "in": "body",
                        "required": False,
                        "schema": {
                            "type": "object",
                            "properties": {"title": {"type": "string"}},
                        },
                    },
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "schema": {"$ref": "#/definitions/Issue"},
                    }
                },
            },
        },
    },
    "definitions": {
        "Issue": {
            "type": "object",
            "properties": {
                "number": {"type": "integer"},
                "title": {"type": "string"},
                "state": {"type": "string"},
            },
        },
    },
}


class TestQueryVariantStaleness:
    """A write clears cached query-variant reads (issue #755 regression).

    The cache key includes the query string, so a write must clear every
    variant that has been read — not just the base URI.  This is the
    end-to-end guard for the store's base→variants index.
    """

    @pytest.mark.asyncio
    async def test_write_invalidates_query_variant_read(self) -> None:
        """Read ?state=open (cached) → edit issue → read again returns fresh data."""
        import httpx

        config = SimpleConfig(
            url=BASE_TEST_URL,
            token="test_token",
            log_level="ERROR",
            tool_filtering_enabled=False,
        )
        gitea_client = GiteaClient(config)

        with respx.mock() as mock:
            mock.get(f"{BASE_TEST_URL}/swagger.v1.json").respond(
                200, json=QUERY_VARIANT_SWAGGER_SPEC
            )
            issues_route = mock.get(
                f"{BASE_TEST_URL}/api/v1/repos/owner/repo/issues",
                params={"state": "open"},
            )
            issues_route.side_effect = [
                httpx.Response(200, json=[{"number": 1, "title": "v1", "state": "open"}]),
                httpx.Response(200, json=[{"number": 1, "title": "v2", "state": "open"}]),
            ]
            mock.patch(f"{BASE_TEST_URL}/api/v1/repos/owner/repo/issues/1").respond(
                200,
                json={"number": 1, "title": "v2", "state": "open"},
            )

            mcp = await create_mcp_server(gitea_client)

            # First read — cache miss → API returns v1.
            r1 = await mcp.read_resource("gitea://repos/owner/repo/issues?state=open")
            assert "v1" in r1.contents[0].content

            # Second read — cache hit → still v1, no second API call.
            r2 = await mcp.read_resource("gitea://repos/owner/repo/issues?state=open")
            assert "v1" in r2.contents[0].content
            assert issues_route.call_count == 1, "second read should come from cache"

            # Write tool — invalidates the base URI and its query variants.
            await mcp.call_tool(
                "gitea_issue_edit",
                {"owner": "owner", "repo": "repo", "index": 1, "title": "v2"},
            )

            # Third read — cache invalidated → API returns v2.
            r3 = await mcp.read_resource("gitea://repos/owner/repo/issues?state=open")
            assert "v2" in r3.contents[0].content
            assert issues_route.call_count == 2, "third read should hit the API again"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
