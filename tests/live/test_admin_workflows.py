"""Administration workflow: identity, organization, and team verification."""

from __future__ import annotations

import pytest

from tests.live.assertions import assert_content, assert_result_ok
from tests.live.conftest import live_available
from tests.live.quality import JsonShape
from tests.live.workflows import Workflow
from tests.live.world import DEV, ORG_NAME, SCOPE_WRITE, TEAM_NAME, World

_UNITS_MAP = {
    "repo.code": "write",
    "repo.issues": "write",
    "repo.pulls": "write",
}


@live_available
@pytest.mark.live
async def test_identity_organization_team_workflow(world: World) -> None:
    """Verify the admin-created world and a user token end to end."""
    workflow = Workflow(world)

    user = await workflow.ensure_user(DEV)
    assert user["username"] == DEV.username

    org = await workflow.ensure_org(
        ORG_NAME,
        full_name="Live Test Organization",
        description="Bootstrap org for live integration tests",
    )
    assert org["username"] == ORG_NAME

    team = await workflow.ensure_team(
        ORG_NAME,
        TEAM_NAME,
        permission="write",
        units_map=_UNITS_MAP,
    )
    assert team["name"] == TEAM_NAME
    assert "id" in team, "Team setup must expose its id for later admin steps"

    org_result = await workflow.admin_call(
        "gitea_org_get",
        {"org": ORG_NAME, "format": "json"},
        contracts=(
            JsonShape(
                dict,
                keys=("id", "username", "full_name", "description", "visibility"),
                key_types=(("id", int), ("username", str)),
            ),
        ),
    )
    org_data = assert_result_ok(org_result)
    assert isinstance(org_data, dict)
    assert_content(
        org_data,
        username=ORG_NAME,
        full_name="Live Test Organization",
    )

    team_result = await workflow.admin_call(
        "gitea_org_get_team",
        {"id": team["id"], "format": "json"},
        contracts=(
            JsonShape(
                dict,
                keys=("id", "name", "permission", "units_map"),
                key_types=(("id", int), ("name", str), ("permission", str)),
            ),
        ),
    )
    team_data = assert_result_ok(team_result)
    assert isinstance(team_data, dict)
    assert_content(team_data, id=team["id"], name=TEAM_NAME, permission="write")

    user_result = await workflow.call(
        DEV,
        SCOPE_WRITE,
        "gitea_user_get_current",
        {"format": "json"},
        contracts=(
            JsonShape(
                dict,
                keys=("id", "login", "username", "email"),
                key_types=(("id", int), ("login", str)),
            ),
        ),
    )
    current = assert_result_ok(user_result)
    assert isinstance(current, dict)
    assert_content(current, login=DEV.username, username=DEV.username)
