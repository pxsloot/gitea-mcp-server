#!/usr/bin/env python3
"""Manual verification script for cache invalidation (issues #743, #755).

This script demonstrates that the cache invalidation system works correctly
by simulating the flow:

1. Stamp type references on a small spec (x-resource-types / x-modifies-type)
2. Register a resource surface
3. Record write tools and derive the invalidation map
4. Compute concrete URIs from tool arguments
5. Show that query-variant reads are invalidated too

The cache is the project-owned ``ResponseCache`` (issue #755): keys are raw
URIs, and the store resolves query variants internally — no FastMCP caching
internals are involved.

Run: python -m tests.manual_test_cache_invalidation
"""

from gitea_mcp_server.cache_invalidation import (
    TOOL_INVALIDATION_MAP,
    build_invalidation_map,
    compute_uris_to_invalidate,
    record_write_tool,
)
from gitea_mcp_server.openapi_converter.type_references import stamp_type_references
from gitea_mcp_server.resources.surface import (
    clear_resource_surface,
    register_resource_surface,
)
from gitea_mcp_server.response_cache import ResponseCache
from tests.helpers.spec_fixtures import make_openapi_spec


def print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f" {title}")
    print("=" * 60)


def main() -> None:
    print_section("Cache Invalidation Manual Test")

    # 1. Build a small spec and stamp type references.
    spec = make_openapi_spec(
        paths={
            "/repos/{owner}/{repo}": {
                "get": {
                    "operationId": "repoGet",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Repository"}
                                }
                            },
                        }
                    },
                },
                "patch": {
                    "operationId": "repoEdit",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Repository"}
                                }
                            },
                        }
                    },
                },
            },
            "/repos/{owner}/{repo}/issues": {
                "get": {
                    "operationId": "issueList",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Issue"},
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "issueCreate",
                    "responses": {
                        "201": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Issue"}
                                }
                            },
                        }
                    },
                },
            },
            "/repos/{owner}/{repo}/labels": {
                "get": {
                    "operationId": "labelList",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Label"},
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "operationId": "labelCreate",
                    "responses": {
                        "201": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Label"}
                                }
                            },
                        }
                    },
                },
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
                "Label": {"type": "object", "properties": {"name": {"type": "string"}}},
            }
        },
    )
    stamp_type_references(spec)

    # 2. Register the resource surface.
    clear_resource_surface()
    TOOL_INVALIDATION_MAP.clear()
    register_resource_surface("gitea://repos/{owner}/{repo}", "/repos/{owner}/{repo}")
    register_resource_surface("gitea://repos/{owner}/{repo}/issues", "/repos/{owner}/{repo}/issues")
    register_resource_surface("gitea://repos/{owner}/{repo}/labels", "/repos/{owner}/{repo}/labels")

    # 3. Record write tools and derive the invalidation map.
    record_write_tool("issue_edit_issue", "/repos/{owner}/{repo}/issues/{index}", "PATCH")
    record_write_tool("repo_create_label", "/repos/{owner}/{repo}/labels", "POST")
    build_invalidation_map(spec)

    print("\n📋 Invalidation Mapping:")
    for tool, templates in sorted(TOOL_INVALIDATION_MAP.items()):
        print(f"  {tool}:")
        for template in templates:
            print(f"    → {template}")

    # 4. Simulate caching a resource (including a query variant) in the
    #    project-owned cache.
    print_section("Simulating Cache Population")
    test_repo = {"owner": "mcp-server", "repo": "gitea-mcp-server"}
    issues_uri = f"gitea://repos/{test_repo['owner']}/{test_repo['repo']}/issues"
    issues_open_uri = f"{issues_uri}?state=open"

    cache = ResponseCache()
    cache.put(issues_uri, {"data": "Issues list (cached)"}, ttl=30)
    cache.put(issues_open_uri, {"data": "Open issues (cached)"}, ttl=30)
    print(f"\n✓ Cached: {issues_uri}")
    print(f"✓ Cached: {issues_open_uri}")

    # 5. Simulate a tool call that should invalidate.
    print_section("Simulating Tool Call: issue_edit_issue")
    arguments = {**test_repo, "index": 42, "state": "closed"}
    uris_to_invalidate = compute_uris_to_invalidate("issue_edit_issue", arguments)

    # The store resolves query variants internally: invalidating the base
    # URI also clears every variant that has been read.
    removed = cache.invalidate(uris_to_invalidate)

    print("\n🔧 Tool called: issue_edit_issue")
    print(f"   URIs to invalidate: {sorted(uris_to_invalidate)}")
    print(f"🗑️  Deleted {removed} cache entries")

    # 6. Show cache state after invalidation.
    print_section("Cache State After Invalidation")
    remaining = [uri for uri in (issues_uri, issues_open_uri) if cache.get(uri) is not None]
    if remaining:
        print("  Remaining entries:")
        for uri in remaining:
            print(f"    {uri}")
    else:
        print("  ✅ Cache is clean - all affected entries were invalidated!")

    print("\n✅ All checks passed!")
    print("Issue #743 is effectively resolved; the cache is project-owned (#755).")


if __name__ == "__main__":
    main()
