"""Boolean-check normalization: is_merged returns an unambiguous boolean.

Gitea models "is this thing true?" endpoints (e.g. ``repoPullRequestIsMerged``)
as a GET returning 204 on success and 404 when the answer is "no".  The
normalized surface (design decision #17, ``openapi_converter/normalize.py``)
returns ``{"result": true}`` on 204, ``{"result": false}`` on 404 when the
underlying resource exists, and a clear error when the resource is missing.

This live test exercises the full flow against a real Forgejo instance:
create a repo with a feature branch + file, open a PR, verify the unmerged
check returns ``false``, merge the PR (using the normalized ``do`` param),
verify the merged check returns ``true``, and verify a non-existent PR
index raises an error.

Design decisions
----------------
- **Single sequential flow**: merging is a state transition, so the
  unmerged → merge → merged steps live in one test method rather than
  being split across methods with ordering dependencies.
- **Scalar result**: the boolean-check returns a scalar boolean, not a
  dict/list — ``assert_result_ok`` requires dict/list, so the text is
  parsed directly with ``json.loads`` and the ``result`` envelope key
  is asserted.
- **Normalized param**: ``gitea_repo_merge_pull_request`` takes ``do``
  (snake_case), not ``Do`` — Rule A normalization renames the body
  property while the wire request still sends ``Do``.
- **Cleanup**: the session-scoped ``World`` deletes registered repositories.
"""

from __future__ import annotations

import json

import pytest

from tests.helpers.mcp_results import extract_text_content
from tests.live.conftest import live_available
from tests.live.world import DEV, SCOPE_WRITE, World

_REPO = "live-boolean-check"
_BRANCH = "feature/boolean-check"
_FILE = "boolean-check.py"
_BODY = """# Boolean-check feature
print('hello from boolean-check live test')
"""


@live_available
class TestBooleanCheck:
    """is_merged returns an unambiguous boolean across the PR lifecycle."""

    @pytest.mark.live
    async def test_is_merged_lifecycle(self, world: World) -> None:
        """Unmerged → merge → merged → not-found, asserting the boolean each step."""
        repo = await world.need_repo(
            DEV.username,
            _REPO,
            user=DEV,
            scopes=SCOPE_WRITE,
            auto_init=True,
            description="Boolean-check normalization test repo",
            branch=_BRANCH,
            files={_FILE: _BODY},
        )
        pr = await repo.need_pull_request(
            "Feature: boolean-check",
            head=_BRANCH,
            body="This PR adds a boolean-check feature for testing.",
        )
        index = pr["number"]
        mcp = await world.server_for(DEV, SCOPE_WRITE)

        # ── Unmerged: the condition is false, the PR exists ─────────────
        result = await mcp.call_tool(
            "gitea_repo_pull_request_is_merged",
            {"owner": DEV.username, "repo": _REPO, "index": index, "format": "json"},
        )
        assert not result.isError, (
            f"is_merged (unmerged) failed: {extract_text_content(result.content)}"
        )
        text = extract_text_content(result.content)
        assert json.loads(text)["result"] is False, f"Expected false, got: {text[:200]!r}"

        # ── Merge: normalized param is ``do``, not ``Do`` ───────────────
        result = await mcp.call_tool(
            "gitea_repo_merge_pull_request",
            {
                "owner": DEV.username,
                "repo": _REPO,
                "index": index,
                "do": "merge",
                "format": "json",
            },
        )
        assert not result.isError, (
            f"merge_pull_request failed: {extract_text_content(result.content)}"
        )

        # ── Merged: the condition now holds ─────────────────────────────
        result = await mcp.call_tool(
            "gitea_repo_pull_request_is_merged",
            {"owner": DEV.username, "repo": _REPO, "index": index, "format": "json"},
        )
        assert not result.isError, (
            f"is_merged (merged) failed: {extract_text_content(result.content)}"
        )
        text = extract_text_content(result.content)
        assert json.loads(text)["result"] is True, f"Expected true, got: {text[:200]!r}"

        # ── Not found: a non-existent PR index raises an error ──────────
        result = await mcp.call_tool(
            "gitea_repo_pull_request_is_merged",
            {"owner": DEV.username, "repo": _REPO, "index": 999999},
        )
        assert result.isError, (
            "Expected is_merged on a non-existent PR to error, "
            f"got: {extract_text_content(result.content)[:200]!r}"
        )
