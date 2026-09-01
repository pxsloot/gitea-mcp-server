"""First workflow migrated to the composable live-test architecture.

The story is intentionally small but complete: an actor needs a user, repo,
label, and issue before it can add a label to that issue.  The dependency graph
materializes those prerequisites once, while quality contracts remain
orthogonal to the workflow itself.
"""

from __future__ import annotations

import pytest

from tests.live.assertions import assert_content, assert_result_ok
from tests.live.conftest import live_available
from tests.live.quality import FormatsEquivalent, JsonShape, TextContains
from tests.live.workflows import Workflow
from tests.live.world import DEV, SCOPE_WRITE, World

_REPO = "live-workflow-labels"
_LABEL = "workflow-bug"
_ISSUE_TITLE = "Workflow label target"
_PR_REPO = "live-workflow-pr"
_PR_BRANCH = "workflow/feature"
_PR_FILE = "workflow-feature.py"
_PR_TITLE = "Workflow feature pull request"


@live_available
@pytest.mark.live
async def test_add_label_to_issue_workflow(world: World) -> None:
    """Run the user story and verify the final result through MCP transport."""
    workflow = Workflow(world)
    repo = await workflow.ensure_repo(
        DEV.username,
        _REPO,
        user=DEV,
        scopes=SCOPE_WRITE,
        auto_init=True,
        description="Dependency-graph workflow test repo",
    )
    await workflow.ensure_label(repo, _LABEL, "#ff0000")
    issue = await workflow.ensure_issue(
        repo,
        _ISSUE_TITLE,
        body="Created by the issue-label workflow.",
    )

    added = await workflow.call(
        DEV,
        SCOPE_WRITE,
        "gitea_issue_add_label",
        {
            "owner": DEV.username,
            "repo": _REPO,
            "index": issue["number"],
            "labels": [_LABEL],
            "format": "json",
        },
        contracts=(JsonShape(list),),
    )
    added_data = assert_result_ok(added)
    assert isinstance(added_data, list)
    assert any(item.get("name") == _LABEL for item in added_data)

    verified = await workflow.call(
        DEV,
        SCOPE_WRITE,
        "gitea_issue_get_issue",
        {
            "owner": DEV.username,
            "repo": _REPO,
            "index": issue["number"],
            "format": "json",
        },
        contracts=(
            JsonShape(
                dict,
                keys=("number", "title", "labels"),
                key_types=(("number", int), ("title", str)),
            ),
            FormatsEquivalent(),
        ),
    )
    verified_data = assert_result_ok(verified)
    assert isinstance(verified_data, dict)
    assert_content(verified_data, number=issue["number"], title=_ISSUE_TITLE)
    assert _LABEL in [item["name"] for item in verified_data["labels"]]


@live_available
@pytest.mark.live
async def test_issue_to_pull_diff_workflow(world: World) -> None:
    """Build an issue-to-PR story and verify its raw diff output."""
    workflow = Workflow(world)
    repo = await workflow.ensure_repo(
        DEV.username,
        _PR_REPO,
        user=DEV,
        scopes=SCOPE_WRITE,
        auto_init=True,
        description="Issue-to-PR workflow test repo",
    )
    issue = await workflow.ensure_issue(
        repo,
        "Workflow feature request",
        body="Please implement the workflow feature.",
    )
    await workflow.ensure_branch(repo, _PR_BRANCH)
    await workflow.ensure_file(
        repo,
        _PR_FILE,
        "print('created through the issue-to-pr workflow')\n",
        branch=_PR_BRANCH,
    )
    pull_request = await workflow.ensure_pull_request(
        repo,
        head=_PR_BRANCH,
        base="main",
        title=_PR_TITLE,
        body=f"Implements issue #{issue['number']}.",
        user=DEV,
        scopes=SCOPE_WRITE,
    )

    await workflow.call(
        DEV,
        SCOPE_WRITE,
        "gitea_repo_download_pull_diff_or_patch",
        {
            "owner": DEV.username,
            "repo": _PR_REPO,
            "index": pull_request["number"],
            "diff_type": "diff",
        },
        contracts=(TextContains(("diff --git", _PR_FILE)),),
    )
