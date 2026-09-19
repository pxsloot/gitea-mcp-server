"""Regression tests for issue #759: detail=concise must summarize, not drop.

Before #759, ``detail=concise`` on list tools collapsed every root-list item
to a bare ``$ref:TypeName`` label — zero useful data — because
``collapse_data`` had no way to resolve the item ``$ref`` and walked only
schema fragments.  The same trap hit ``read_resource`` (e.g. the labels
resource returned content-free ``$ref:Label`` bullets).

The agreed fix (S1-lite): the collapse resolves a root list's item ``$ref``
one level via the server's OpenAPI spec, so each item keeps its scalar
fields and only its nested ``$ref``-backed fields collapse to labels.  The
root object case (write-tool echoes like ``create_pull_request``) already
had these semantics — root scalars intact, nested objects collapsed — and
is asserted here to lock it.

These tests exercise the *pipeline* (``render``), asserting both channels:
the text an agent reads (content is the contract) and the mirrored
``structured_content``.
"""

from __future__ import annotations

from typing import Any

from gitea_mcp_server.tools.result_pipeline import ExecutionResult, render
from tests.helpers.mcp_results import extract_text_content, get_structured, parse_json_content
from tests.helpers.spec_fixtures import make_openapi_spec


def _spec() -> Any:
    """A spec with Issue (scalars + $ref user + $ref milestone + $ref-label list)."""
    return make_openapi_spec(
        components={
            "schemas": {
                "User": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "login": {"type": "string"},
                        "avatar_url": {"type": "string"},
                    },
                },
                "Milestone": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
                "Label": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                },
                "Issue": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer"},
                        "title": {"type": "string"},
                        "state": {"type": "string"},
                        "html_url": {"type": "string"},
                        "user": {"$ref": "#/components/schemas/User"},
                        "milestone": {"$ref": "#/components/schemas/Milestone"},
                        "labels": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Label"},
                        },
                    },
                },
            }
        }
    )


_ISSUES_SCHEMA = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}


def _issue(number: int, title: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "html_url": f"https://git.example/issues/{number}",
        "user": {"id": 12, "login": "dev2", "avatar_url": "https://git.example/a.png"},
        "milestone": {
            "id": 29,
            "title": "One Agent-Facing Contract",
            "description": "D" * 500,  # the repeated-milestone-description case
        },
        "labels": [{"id": 1, "name": "Kind/Bug"}],
    }


class TestListToolItemSummaries:
    """The reproduction pair from the issue: concise returns item summaries."""

    def test_json_channel_item_summaries(self) -> None:
        """format=json: per item — scalars intact, nested refs collapsed."""
        result = render(
            ExecutionResult(
                data=[_issue(759, "concise drops items"), _issue(760, "formatter reuse")],
                total_count=2,
                shape="list",
                paginated=True,
            ),
            fmt="json",
            detail="concise",
            schema=_ISSUES_SCHEMA,
            openapi_spec=_spec(),
        )
        parsed = parse_json_content(result)
        items = parsed["result"]
        assert len(items) == 2
        for item in items:
            assert isinstance(item, dict)
            assert item["title"]  # scalar intact
            assert item["state"] == "open"
            assert item["user"] == {"$ref": "User"}  # nested ref becomes marker
            assert item["milestone"] == {"$ref": "Milestone"}
            assert item["labels"] == {"$ref": "Label", "count": 1}  # collapsed list

    def test_markdown_channel_item_summaries(self) -> None:
        """format=markdown: the text channel shows titles, not bare $ref:Issue bullets."""
        result = render(
            ExecutionResult(
                data=[_issue(759, "concise drops items")],
                total_count=1,
                shape="list",
                paginated=True,
            ),
            fmt="markdown",
            detail="concise",
            schema=_ISSUES_SCHEMA,
            openapi_spec=_spec(),
        )
        text = extract_text_content(result.content)
        assert "concise drops items" in text  # title visible to the agent
        assert "$ref:User" in text  # nested ref labelled
        assert "$ref:Issue" not in text  # items are never label-replaced
        assert "avatar" not in text  # nested User payload is gone
        assert "D" * 500 not in text  # nested Milestone payload is gone

    def test_structured_content_mirrors_text(self) -> None:
        """Both channels carry the same collapsed page (content is the contract)."""
        result = render(
            ExecutionResult(
                data=[_issue(1, "t")],
                total_count=1,
                shape="list",
                paginated=True,
            ),
            fmt="markdown",
            detail="concise",
            schema=_ISSUES_SCHEMA,
            openapi_spec=_spec(),
        )
        sc = get_structured(result)
        assert sc["result"][0]["user"] == {"$ref": "User"}
        assert sc["result"][0]["title"] == "t"

    def test_no_spec_fallback_is_documented(self) -> None:
        """Without a spec the pre-#759 whole-item marker fallback applies."""
        result = render(
            ExecutionResult(data=[_issue(1, "t")], shape="list"),
            fmt="json",
            detail="concise",
            schema=_ISSUES_SCHEMA,
        )
        parsed = parse_json_content(result)
        assert parsed["result"] == [{"$ref": "Issue"}]

    def test_raw_never_collapses(self) -> None:
        """format=raw stays the unprocessed-data contract even with a spec."""
        result = render(
            ExecutionResult(data=[_issue(1, "t")], shape="list"),
            fmt="raw",
            detail="concise",
            schema=_ISSUES_SCHEMA,
            openapi_spec=_spec(),
        )
        parsed = parse_json_content(result)
        assert parsed["result"][0]["user"]["login"] == "dev2"


class TestPerResultSchemaPath:
    """read_resource-style executors supply a per-result schema; the spec still resolves."""

    def test_execution_result_schema_with_spec_summarizes(self) -> None:
        per_uri_schema = {
            "type": "array",
            "items": {"$ref": "#/components/schemas/Label"},
        }
        result = render(
            ExecutionResult(
                data=[{"id": 1, "name": "Kind/Bug"}, {"id": 2, "name": "Priority/Medium"}],
                shape="list",
                schema=per_uri_schema,
            ),
            fmt="json",
            detail="concise",
            schema=None,  # tool-level schema absent — per-result wins as before
            openapi_spec=_spec(),
        )
        parsed = parse_json_content(result)
        # Label is all-scalar: a summarized item is the full scalar dict —
        # the content-free marker bullets are gone.
        assert parsed["result"] == [
            {"id": 1, "name": "Kind/Bug"},
            {"id": 2, "name": "Priority/Medium"},
        ]


class TestWriteToolEcho:
    """Root-object semantics (acceptance criterion 2): nested objects collapse, root scalars stay."""

    def test_create_like_response_collapses_nested_repo_and_user(self) -> None:
        spec = _spec()
        schema = {
            "type": "object",
            "properties": {
                "number": {"type": "integer"},
                "title": {"type": "string"},
                "user": {"$ref": "#/components/schemas/User"},
                "head": {
                    "type": "object",
                    "properties": {"repo": {"$ref": "#/components/schemas/User"}},
                },
            },
        }
        data = {
            "number": 1,
            "title": "PR",
            "user": {"id": 1, "login": "dev2", "avatar_url": "a"},
            "head": {"repo": {"id": 1, "login": "owner", "avatar_url": "b"}},
        }
        result = render(
            ExecutionResult(data=data, shape="object"),
            fmt="json",
            detail="concise",
            schema=schema,
            openapi_spec=spec,
        )
        parsed = parse_json_content(result)
        assert parsed["result"]["number"] == 1
        assert parsed["result"]["title"] == "PR"
        assert parsed["result"]["user"] == {"$ref": "User"}
        assert parsed["result"]["head"]["repo"] == {"$ref": "User"}
