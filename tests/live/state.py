"""Repository state tracker, postcondition verification, and assertion helpers.

Extracted from ``world.py`` to keep that module focused on the ``World``
orchestration facade.  ``RepoState`` manages the lazy state inside a single
test repository — branches, labels, milestones, issues, pull requests,
tags, and files — with idempotent ``need_*`` methods.

Mutable postconditions
----------------------
``need_issue`` and ``need_pull_request`` accept an optional ``state``
parameter.  When a cached entity's observed ``state`` differs from the
expected postcondition, the entity is re-read from the Gitea instance.
An :class:`PostconditionError` is raised if the actual state still does
not match.  For pull requests, a :class:`IrreversibleTransitionError`
is raised when the test expects ``open`` on a merged PR — a permanent
state that cannot be reversed without deleting the entity.

All names are re-exported by ``world.py`` for backward compatibility.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from tests.helpers.mcp_results import extract_text_content
from tests.live.conflict import (
    IrreversibleTransitionError,
    PostconditionError,
    check_conflict,
)

if TYPE_CHECKING:
    from mcp import ClientSession

    from tests.live.identities import User
    from tests.live.world import World


# ---------------------------------------------------------------------------
# Internal helpers (no circular imports)
# ---------------------------------------------------------------------------


def _is_error(result: Any) -> bool:
    """Check if an MCP tool call result indicates an error (has ``.isError``)."""
    return bool(getattr(result, "isError", False))


def _unwrap(result: Any) -> dict[str, Any]:
    """Extract and parse JSON from a tool call result.

    Raises ``TypeError`` if the parsed result is not a dict.
    """
    text = extract_text_content(result.content)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        msg = f"Expected dict result, got {type(parsed).__name__}"
        raise TypeError(msg)
    return cast("dict[str, Any]", parsed)


def _error_text(result: Any) -> str:
    """Extract error text from a tool call result."""
    content = getattr(result, "content", None)
    if not content:
        return ""
    from mcp.types import TextContent

    texts: list[str] = []
    for item in content:
        if isinstance(item, TextContent):
            texts.append(item.text)
        else:
            texts.append(str(item))
    return "\n".join(texts)


def _assert_keys(data: dict[str, Any], *keys: str) -> None:
    """Assert all *keys* are present in *data*."""
    missing = [k for k in keys if k not in data]
    if missing:
        msg = f"Missing required keys: {missing}. Available: {sorted(data.keys())}"
        raise AssertionError(msg)


def _assert_key_types(data: dict[str, Any], **typed: type) -> None:
    """Assert specific keys have the expected types.

    Raises ``TypeError`` if a key has the wrong type.
    """
    for key, expected_type in typed.items():
        actual = data.get(key)
        if not isinstance(actual, expected_type):
            msg = (
                f"Key {key!r}: expected {expected_type.__name__}, "
                f"got {type(actual).__name__} ({actual!r})"
            )
            raise TypeError(msg)


def _assert_content(data: dict[str, Any], **expected: Any) -> None:
    """Assert specific key-value pairs match exactly."""
    for key, expected_val in expected.items():
        actual = data.get(key)
        if actual != expected_val:
            msg = f"Key {key!r}: expected {expected_val!r}, got {actual!r}"
            raise AssertionError(msg)


# =============================================================================
# RepoState — tracks what's inside a known repo
# =============================================================================


@dataclass
class RepoState:
    """Lazy state tracker for a single test repository.

    Created by ``World.need_repo()``.  ``need_*`` methods are
    idempotent — they create+verify the first time and return cached
    state every subsequent call.

    Attrs:
        owner: Repository owner (login name).
        name: Repository name.
        data: Raw API response dict from ``create_repo``.
        branches: ``{branch_name: branch_data}`` — created lazily.
        labels: ``{label_name: label_data}`` — created lazily.
        milestones: ``{milestone_title: milestone_data}`` — created lazily.
        issues: ``{issue_number: issue_data}`` — created lazily.
        tags: ``{tag_name: tag_data}`` — created lazily.
    """

    owner: str
    name: str
    data: dict[str, Any]

    # Back-reference to the World — needed to call tools through the
    # pooled server for this repo's owner+scopes.
    _world: World = field(repr=False)
    _user: User = field(repr=False)
    _scopes: list[str] = field(repr=False)

    branches: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, dict[str, Any]] = field(default_factory=dict)
    milestones: dict[str, dict[str, Any]] = field(default_factory=dict)
    issues: dict[int, dict[str, Any]] = field(default_factory=dict)
    pull_requests: dict[int, dict[str, Any]] = field(default_factory=dict)
    tags: dict[str, dict[str, Any]] = field(default_factory=dict)
    _files: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    """Files cached by ``{branch}:{path}`` key."""

    # ── Per-resource option guards (conflict detection) ─────────────────
    _branch_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _label_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _milestone_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _issue_options: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
    _tag_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _pr_options: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)
    _file_options: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    async def _server(self) -> ClientSession:
        """Get the pooled server for this repo's owner+scopes."""
        return await self._world.server_for(self._user, self._scopes)

    # ── need_* — idempotent create-or-return ──────────────────────────

    async def need_branch(
        self, name: str, *, old: str = "main"
    ) -> dict[str, Any]:
        """Ensure a branch exists.  Creates from *old* if not cached.

        Raises:
            ConflictError: If a previous request for this branch name
                used a different *old* (source branch).
        """
        if name in self.branches:
            check_conflict(
                "branch", name,
                self._branch_options.get(name, {}),
                {"old": old},
            )
            return self.branches[name]

        mcp = await self._server()
        result = await mcp.call_tool(
            "gitea_repo_create_branch",
            {
                "owner": self.owner,
                "repo": self.name,
                "new_branch_name": name,
                "old_branch_name": old,
                "format": "json",
            },
        )
        if _is_error(result):
            msg = f"need_branch({name!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.branches[name] = data
        self._branch_options[name] = {"old": old}
        return data

    async def need_file(
        self,
        path: str,
        content: str,
        *,
        branch: str = "main",
        message: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a file exists on *branch*.  Creates if not cached.

        Note the param name ``path`` (not ``filepath``) — the underlying
        tool uses ``filepath`` (a known naming divergence).
        """
        file_key = f"{branch}:{path}"
        if file_key in self._files:
            check_conflict(
                "file", f"{file_key!r}",
                self._file_options.get(file_key, {}),
                {"content": content},
            )
            return self._files[file_key]

        if branch != "main" and branch not in self.branches:
            await self.need_branch(branch)

        mcp = await self._server()
        encoded = base64.b64encode(content.encode()).decode()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "filepath": path,
            "content": encoded,
            "branch": branch,
            "format": "json",
        }
        if message:
            kwargs["message"] = message

        result = await mcp.call_tool("gitea_repo_create_file", kwargs)
        if _is_error(result):
            msg = f"need_file({path!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self._files[file_key] = data
        self._file_options[file_key] = {"content": content}
        return data

    async def need_label(
        self,
        name: str,
        color: str = "#000000",
        *,
        description: str | None = None,
        exclusive: bool = False,
    ) -> dict[str, Any]:
        """Ensure a label exists.  Creates if not cached.

        Raises:
            ConflictError: If a previous request for this label name
                used different *color* or *exclusive*.
        """
        if name in self.labels:
            check_conflict(
                "label", name,
                self._label_options.get(name, {}),
                {"color": color, "exclusive": exclusive},
            )
            return self.labels[name]

        mcp = await self._server()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "name": name,
            "color": color,
            "exclusive": exclusive,
            "format": "json",
        }
        if description:
            kwargs["description"] = description

        result = await mcp.call_tool("gitea_issue_create_label", kwargs)
        if _is_error(result):
            msg = f"need_label({name!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.labels[name] = data
        self._label_options[name] = {
            "color": color, "exclusive": exclusive,
        }
        return data

    async def need_milestone(
        self,
        title: str,
        *,
        description: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a milestone exists.  Creates if not cached.

        Raises:
            ConflictError: If a previous request for this milestone
                title used different *description* or *due_date*.
        """
        if title in self.milestones:
            check_conflict(
                "milestone", title,
                self._milestone_options.get(title, {}),
                {"description": description, "due_date": due_date},
            )
            return self.milestones[title]

        mcp = await self._server()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "title": title,
            "format": "json",
        }
        if description:
            kwargs["description"] = description
        if due_date:
            kwargs["due_date"] = due_date

        result = await mcp.call_tool("gitea_issue_create_milestone", kwargs)
        if _is_error(result):
            msg = f"need_milestone({title!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.milestones[title] = data
        self._milestone_options[title] = {
            "description": description, "due_date": due_date,
        }
        return data

    async def need_issue(  # noqa: PLR0912
        self,
        title: str,
        *,
        body: str | None = None,
        labels: list[int | str] | None = None,
        milestone: int | None = None,
        assignees: list[str] | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Create an issue and cache it by number.

        Issues are matched by *title* — if an issue with this title
        already exists in the cache, the request options (*body*,
        *labels*, *milestone*, *assignees*) must match.  If an
        issue exists on the Gitea instance (from a previous run),
        it is adopted into the cache.

        **Mutable postcondition**: If *state* is provided and differs
        from the cached issue's current ``state`` field, the issue is
        re-read from Gitea and verified.  This allows later tests to
        assert that a previous test has left the issue in the expected
        state (e.g. ``state="closed"`` after a close workflow).

        Postcondition checks fire on **cache hits only** — not on the
        first call that creates or adopts the entity.  On the first
        encounter (create or adopt-from-Gitea), the *state* is stored
        as a declaration of intent and verified on the next cache hit.

        Raises:
            ConflictError: If a cached issue with the same title was
                created with different *body*, *labels*, *milestone*,
                or *assignees*.
            PostconditionError: If the cached issue's re-read state
                does not match the expected *state*.
        """
        # Check by title in cached issues
        for number, cached in self.issues.items():
            if cached.get("title") == title:
                check_conflict(
                    "issue", f"#{number} ({title!r})",
                    self._issue_options.get(number, {}),
                    {
                        "body": body, "labels": labels,
                        "milestone": milestone, "assignees": assignees,
                    },
                )
                # Postcondition: if caller expects a specific state, verify
                if state is not None and cached.get("state") != state:
                    return await self._verify_issue_postcondition(
                        number, title, state,
                    )
                # Update the stored postcondition state for next cache hit
                if state is not None:
                    opts = self._issue_options.get(number, {})
                    opts["state"] = state
                    self._issue_options[number] = opts
                return cached

        # Check if it exists in Gitea (created by a previous run)
        mcp = await self._server()
        list_result = await mcp.call_tool(
            "gitea_issue_list_issues",
            {"owner": self.owner, "repo": self.name, "format": "json"},
        )
        if not _is_error(list_result):
            try:
                text = extract_text_content(list_result.content)
                existing = json.loads(text)
                if isinstance(existing, list):
                    for item in existing:
                        if item.get("title") == title:
                            number = item["number"]
                            self.issues[number] = item
                            self._issue_options[number] = {
                                "body": body, "labels": labels,
                                "milestone": milestone, "assignees": assignees,
                                "state": state,
                            }
                            return cast("dict[str, Any]", item)
            except (json.JSONDecodeError, AssertionError):
                pass  # Create fresh

        # Create new issue
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "title": title,
            "format": "json",
        }
        if body:
            kwargs["body"] = body
        if labels:
            kwargs["labels"] = labels
        if milestone is not None:
            kwargs["milestone"] = milestone
        if assignees:
            kwargs["assignees"] = assignees

        result = await mcp.call_tool("gitea_issue_create_issue", kwargs)
        if _is_error(result):
            msg = f"need_issue({title!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.issues[data["number"]] = data
        self._issue_options[data["number"]] = {
            "body": body, "labels": labels,
            "milestone": milestone, "assignees": assignees,
            "state": state,
        }
        return data

    async def _verify_issue_postcondition(
        self, number: int, title: str, expected_state: str,
    ) -> dict[str, Any]:
        """Re-read an issue from Gitea and assert its state matches *expected_state*.

        Called when a cached issue's ``state`` field differs from the
        postcondition requested by the current test.  On success the
        cache is updated with fresh data.  On failure a
        :class:`PostconditionError` is raised.
        """
        entity_label = f"issue #{number} ({title!r})"
        mcp = await self._server()
        result = await mcp.call_tool(
            "gitea_issue_get_issue",
            {
                "owner": self.owner,
                "repo": self.name,
                "index": number,
                "format": "json",
            },
        )
        if _is_error(result):
            raise PostconditionError(
                entity_label, "readable", True, False,
            ) from None
        data = _unwrap(result)
        actual_state = data.get("state")
        if actual_state != expected_state:
            raise PostconditionError(
                entity_label, "state", expected_state, actual_state,
            )
        # Update cache with fresh data
        self.issues[number] = data
        # Update the stored postcondition for the next cache hit
        opts = self._issue_options.get(number, {})
        opts["state"] = expected_state
        self._issue_options[number] = opts
        return data

    async def need_pull_request(
        self,
        title: str,
        *,
        head: str,
        base: str = "main",
        body: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a pull request exists, matching cached or remote state.

        **Mutable postcondition**: If *state* is provided and differs
        from the cached PR's current ``state`` field, the PR is re-read
        from Gitea and verified.  An :class:`IrreversibleTransitionError`
        is raised when a test requests ``state="open"`` on a merged PR
        — merging is permanent and cannot be undone.

        Postcondition checks fire on **cache hits only** — not on the
        first call that creates or adopts the entity.  On the first
        encounter (create or adopt-from-Gitea), the *state* is stored
        as a declaration of intent and verified on the next cache hit.

        Raises:
            ConflictError: If a cached PR with the same title was
                created with different *head*, *base*, or *body*.
            PostconditionError: If the cached PR's re-read state does
                not match the expected *state*.
            IrreversibleTransitionError: If the test expects
                ``state="open"`` on a PR that has been merged.
        """
        for number, cached in self.pull_requests.items():
            if cached.get("title") == title:
                check_conflict(
                    "pull_request", f"#{number} ({title!r})",
                    self._pr_options.get(number, {}),
                    {"head": head, "base": base, "body": body},
                )
                # Postcondition: if caller expects a specific state, verify
                if state is not None and cached.get("state") != state:
                    return await self._verify_pr_postcondition(
                        number, title, state,
                    )
                # Update stored postcondition state
                if state is not None:
                    opts = self._pr_options.get(number, {})
                    opts["state"] = state
                    self._pr_options[number] = opts
                return cached

        mcp = await self._server()
        listed = await mcp.call_tool(
            "gitea_repo_list_pull_requests",
            {
                "owner": self.owner,
                "repo": self.name,
                "state": "all",
                "format": "json",
            },
        )
        if not _is_error(listed):
            try:
                data = json.loads(extract_text_content(listed.content))
                if isinstance(data, list):
                    for item in data:
                        if item.get("title") == title:
                            number = item["number"]
                            self.pull_requests[number] = item
                            self._pr_options[number] = {
                                "head": head, "base": base, "body": body,
                                "state": state,
                            }
                            return cast("dict[str, Any]", item)
            except (json.JSONDecodeError, AssertionError):
                pass

        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "head": head,
            "base": base,
            "title": title,
            "format": "json",
        }
        if body:
            kwargs["body"] = body
        result = await mcp.call_tool("gitea_repo_create_pull_request", kwargs)
        if _is_error(result):
            msg = f"need_pull_request({title!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        _assert_keys(data, "number", "title", "state", "head", "base")
        _assert_content(data, title=title, state="open")
        self.pull_requests[data["number"]] = data
        self._pr_options[data["number"]] = {
            "head": head, "base": base, "body": body,
            "state": state,
        }
        return data

    async def _verify_pr_postcondition(
        self, number: int, title: str, expected_state: str,
    ) -> dict[str, Any]:
        """Re-read a PR from Gitea and assert its state matches *expected_state*.

        Detects irreversible transitions: a PR that has been merged
        (``merged=True``) cannot return to ``"open"``.  In that case
        an :class:`IrreversibleTransitionError` is raised.  Otherwise
        a state mismatch raises :class:`PostconditionError`.

        On success the cache is updated with fresh data.
        """
        entity_label = f"PR #{number} ({title!r})"
        mcp = await self._server()
        result = await mcp.call_tool(
            "gitea_repo_get_pull_request",
            {
                "owner": self.owner,
                "repo": self.name,
                "index": number,
                "format": "json",
            },
        )
        if _is_error(result):
            raise PostconditionError(
                entity_label, "readable", True, False,
            ) from None
        data = _unwrap(result)

        # Irreversible: merged PR cannot go back to "open"
        if expected_state == "open" and data.get("merged", False):
            raise IrreversibleTransitionError(
                entity_label, "merged", False, True,
            )

        actual_state = data.get("state")
        if actual_state != expected_state:
            raise PostconditionError(
                entity_label, "state", expected_state, actual_state,
            )
        # Update cache with fresh data
        self.pull_requests[number] = data
        # Update stored postcondition for the next cache hit
        opts = self._pr_options.get(number, {})
        opts["state"] = expected_state
        self._pr_options[number] = opts
        return data

    async def need_tag(
        self,
        name: str,
        *,
        target: str = "main",
        message: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a tag exists.  Creates if not cached.

        Note: the tool parameter is ``tag_name`` but the API response
        uses ``name`` — a known naming divergence.

        Raises:
            ConflictError: If a previous request for this tag name
                used a different *target*.
        """
        if name in self.tags:
            check_conflict(
                "tag", name,
                self._tag_options.get(name, {}),
                {"target": target},
            )
            return self.tags[name]

        mcp = await self._server()
        kwargs: dict[str, Any] = {
            "owner": self.owner,
            "repo": self.name,
            "tag_name": name,
            "target": target,
            "format": "json",
        }
        if message:
            kwargs["message"] = message

        result = await mcp.call_tool("gitea_repo_create_tag", kwargs)
        if _is_error(result):
            msg = f"need_tag({name!r}) failed: {_error_text(result)[:300]}"
            raise AssertionError(msg)
        data = _unwrap(result)
        self.tags[name] = data
        self._tag_options[name] = {"target": target}
        return data
