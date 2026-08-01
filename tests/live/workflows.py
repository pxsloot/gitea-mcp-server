"""Composable live workflows backed by a verified dependency graph.

This is the first migration layer beside the existing live suite.  It keeps
the current ``World`` as the transport and identity implementation while
making workflow setup declarative and reusable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from tests.live.assertions import assert_content, assert_keys, assert_result_ok
from tests.live.dependency_graph import DependencyGraph, node_key
from tests.live.quality import QualityContract, verify_contracts

if TYPE_CHECKING:
    from mcp import ClientSession

    from tests.live.world import RepoState, User, World


@dataclass
class Workflow:
    """Build verified prerequisites and execute MCP workflow steps.

    By default, workflows share the dependency graph owned by ``World``.
    Passing a graph explicitly remains useful for isolated unit tests.
    """

    world: World
    graph: DependencyGraph | None = None

    def __post_init__(self) -> None:
        """Attach this facade to the World-owned graph by default."""
        if self.graph is None:
            self.graph = self.world.dependency_graph

    @property
    def dependencies(self) -> DependencyGraph:
        """Return the non-optional graph used by workflow operations."""
        assert self.graph is not None, "Workflow graph was not initialized"
        return self.graph

    async def ensure_user(self, user: User) -> dict[str, Any]:
        """Ensure and cache one test identity."""
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("user", user.username),
            lambda: self.world.need_user(user),
        ))

    async def ensure_org(
        self,
        name: str,
        *,
        full_name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Ensure an organization as an administration dependency."""
        identity = (name, full_name or "", description or "")
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("organization", *identity),
            lambda: self.world.need_org(
                name, full_name=full_name, description=description,
            ),
        ))

    async def ensure_team(
        self,
        org: str,
        name: str,
        *,
        permission: str = "read",
        units_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ensure a team and its repository-unit permissions."""
        identity = (
            org, name, permission,
            repr(sorted((units_map or {}).items())),
        )
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("team", *identity),
            lambda: self.world.need_team(
                org, name, permission=permission, units_map=units_map,
            ),
        ))

    async def ensure_repo(
        self,
        owner: str,
        name: str,
        *,
        user: User,
        scopes: list[str],
        **options: Any,
    ) -> RepoState:
        """Ensure a repository and its requested initial state."""
        await self.ensure_user(user)
        identity = (
            owner,
            name,
            tuple(sorted(scopes)),
            repr(sorted(options.items())),
        )
        return cast("RepoState", await self.dependencies.ensure(
            node_key("repository", *identity),
            lambda: self.world.need_repo(
                owner, name, user=user, scopes=scopes, **options
            ),
        ))

    async def ensure_label(
        self,
        repo: RepoState,
        name: str,
        color: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Ensure a repository label as a workflow dependency."""
        identity = (repo.owner, repo.name, name, color, repr(sorted(options.items())))
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("label", *identity),
            lambda: repo.need_label(name, color, **options),
        ))

    async def ensure_branch(
        self,
        repo: RepoState,
        name: str,
        *,
        old: str = "main",
    ) -> dict[str, Any]:
        """Ensure a branch as a workflow dependency."""
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("branch", repo.owner, repo.name, name, old),
            lambda: repo.need_branch(name, old=old),
        ))

    async def ensure_file(
        self,
        repo: RepoState,
        path: str,
        content: str,
        *,
        branch: str = "main",
    ) -> dict[str, Any]:
        """Ensure a committed file as a workflow dependency."""
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("file", repo.owner, repo.name, branch, path, content),
            lambda: repo.need_file(path, content, branch=branch),
        ))

    async def ensure_tag(
        self,
        repo: RepoState,
        name: str,
        *,
        target: str = "main",
        message: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a tag as a repository workflow dependency."""
        identity = (repo.owner, repo.name, name, target, message or "")
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("tag", *identity),
            lambda: repo.need_tag(name, target=target, message=message),
        ))

    async def ensure_issue(
        self,
        repo: RepoState,
        title: str,
        **options: Any,
    ) -> dict[str, Any]:
        """Ensure an issue as a workflow dependency."""
        identity = (repo.owner, repo.name, title, repr(sorted(options.items())))
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("issue", *identity),
            lambda: repo.need_issue(title, **options),
        ))

    async def ensure_milestone(
        self,
        repo: RepoState,
        title: str,
        *,
        description: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        """Ensure a milestone as an issue workflow dependency."""
        identity = (repo.owner, repo.name, title, description or "", due_date or "")
        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("milestone", *identity),
            lambda: repo.need_milestone(
                title, description=description, due_date=due_date,
            ),
        ))

    async def ensure_pull_request(
        self,
        repo: RepoState,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        user: User,
        scopes: list[str],
    ) -> dict[str, Any]:
        """Create and verify a pull request dependency once."""
        identity = (
            repo.owner, repo.name, head, base, title, body,
            user.username, tuple(sorted(scopes)),
        )

        async def create() -> dict[str, Any]:
            mcp = await self.world.server_for(user, scopes)
            result = await mcp.call_tool(
                "gitea_repo_create_pull_request",
                {
                    "owner": repo.owner,
                    "repo": repo.name,
                    "head": head,
                    "base": base,
                    "title": title,
                    "body": body,
                    "format": "json",
                },
            )
            data = assert_result_ok(result)
            assert isinstance(data, dict), "Pull request setup must return an object"
            assert_keys(data, "number", "title", "state", "head", "base")
            assert_content(data, title=title, state="open")
            return data

        return cast("dict[str, Any]", await self.dependencies.ensure(
            node_key("pull-request", *identity), create,
        ))

    async def call(
        self,
        user: User,
        scopes: list[str],
        tool_name: str,
        arguments: dict[str, Any],
        *,
        contracts: tuple[QualityContract, ...] = (),
    ) -> Any:
        """Execute one workflow step and apply its optional quality contracts."""
        mcp = await self.world.server_for(user, scopes)
        result = await mcp.call_tool(tool_name, arguments)
        await verify_contracts(
            contracts,
            mcp=mcp,
            tool_name=tool_name,
            args=arguments,
            result=result,
        )
        return result

    async def admin_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        contracts: tuple[QualityContract, ...] = (),
    ) -> Any:
        """Execute one step with the World admin token."""
        mcp = await self.world.admin_server()
        result = await mcp.call_tool(tool_name, arguments)
        await verify_contracts(
            contracts,
            mcp=mcp,
            tool_name=tool_name,
            args=arguments,
            result=result,
        )
        return result

    async def client(self, user: User, scopes: list[str]) -> ClientSession:
        """Return the pooled MCP client for direct read-only assertions."""
        return await self.world.server_for(user, scopes)
