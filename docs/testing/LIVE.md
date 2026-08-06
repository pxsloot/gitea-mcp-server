---
audience: developer
type: reference
covers: Live test architecture — Zone 5: World, dependency graph, Workflow facade, RepoState need_* design, quality contracts, how to add/change a live test
---

# Live Test Architecture (Zone 5)

**What it tests**: The full production path — real Forgejo instance, real MCP
server binary over stdio, raw MCP SDK transport. Exercises transport-level
output validation that in-memory ``server.call_tool()`` bypasses.

**Pattern**: Tests in ``tests/live/`` connect to a real Gitea/Forgejo instance,
launch the MCP server binary over stdio, and call tools through the raw MCP SDK
(``mcp.ClientSession`` via ``stdio_client``).  **Every call is an assertion**
— test setup uses MCP tool calls, not raw HTTP.  The one exception is creating
scope-limited tokens (``POST /users/{name}/tokens``) which requires Basic Auth
with a password.

**Design**: Live coverage is organized around user **workflows** (ordered sets
of tools that achieve a goal) plus orthogonal quality contracts.  A workflow
such as ``issue → pull request`` is also a natural reproduction story for a
bug report.  The dependency graph materializes the required world, user,
repository, and content nodes; each node is created and verified once per
isolated test world, then reused as an established fact.

The workflow files use the composable architecture: ``Workflow`` is a thin
facade over the current ``World`` transport and ``RepoState`` operations, while
the World-owned dependency graph materializes prerequisites.  New workflow
tests must keep prerequisites in the graph and cleanup in World fixture
lifecycle handling; do not add module globals, ``pytest.*`` state, or cleanup
test methods.

Quality contracts are independent of workflow composition and can be attached
only where useful: result shape, result content, JSON/Markdown equivalence,
scope behavior, and error content.  This avoids repeating the same checks in
every workflow while ensuring that the agent-facing boundary is useful.

## Directory Layout

```
tests/live/
├── conftest.py              # Session fixtures, world (pooled), mcp_client (per-test)
├── helpers.py               # Tool-based helpers (purge_repo, create_user_token)
├── world.py                 # World (server pool + lazy state graph) + identities
├── dependency_graph.py      # Async verified dependency cache
├── workflows.py             # Workflow facade over World + graph
├── quality.py               # Orthogonal result-quality contracts
├── assertions.py            # Shape/content/cross-format assertion helpers
├── test_meta.py             # Metatests: bootstrap and pool diagnostics
├── test_admin_workflows.py  # Identity, organization, team administration
├── test_world_setup.py      # Phase 1: Error-path tests for bootstrap
├── test_conflict.py         # Unit tests for conflict detection types
├── test_postcondition.py    # Unit tests for mutable postconditions (issue/PR state)
├── test_bootstrap_verify.py # Unit tests for bootstrap verification logic (mocked API)
├── test_repo_workflow.py    # Migrated: repos, branches, files, tags, statuses
├── test_issue_workflow.py   # Migrated: labels, milestones, issues, comments, search
├── test_pr_workflow.py      # Migrated: PRs, diff download, review comments
├── test_cross_format.py     # Migrated concern: format equivalence edge cases
├── test_discovery.py        # Migrated concern: synthetic discovery tools
├── test_resources.py        # Migrated concern: list_resources, read_resource
├── test_scope.py            # Scope enforcement concern tests
└── test_errors.py           # Migrated concern: transport error contracts
```

## Server-Access Patterns

1. **``mcp_client`` context manager** (backward-compatible).  Each test
   function spawns its own server process over stdio.  Used by ``test_errors.py``
   (fresh-server semantics needed).  Server startup is tested each time.

2. **``world`` fixture** (recommended).  A session-scoped-per-worker
   ``World`` object that pools **one MCP server per token scope**.  The admin
   server starts at fixture setup; user servers start on the first
   ``Workflow.client()`` or ``Workflow.call()`` call.  With
   ``asyncio_default_test_loop_scope = session`` (set in ``pyproject.toml``),
   tests assigned to one worker share an event loop and server connections.
   Server startup is tested once per worker session, not once per test.

   The World and Workflow facade provide a **lazy state graph** with
   idempotent dependency methods:

   - ``world.need_user(user)`` — create a user (or return cached)
   - ``world.need_org(name)`` — create an org (or return cached)
   - ``world.need_team(org, name)`` — create a team (or return cached)
   - ``Workflow.ensure_repo(owner, name)`` — create and verify a repo through
     the World-owned graph, returning a ``RepoState``
   - ``world.server_for(user, scopes)`` — get a pooled server for that token

   ``RepoState`` tracks what's inside a known repo (branches, labels,
   milestones, issues, pull requests, and tags) and provides idempotent
   ``need_branch``, ``need_file``, ``need_label``, ``need_milestone``,
   ``need_issue``, ``need_tag``, and ``need_pull_request`` methods.  The first
   call creates and verifies the tool; subsequent calls return cached state.

## Key Design Decisions

1. **Server pooling — test once, reuse.**  The ``world`` fixture pools
   one MCP server per token scope (admin, DEV write, RO read-only,
   LIMITED partial).  Server startup (spec fetch, convert, scope filtering)
   is tested once per scope.  This is sufficient — re-testing startup
   86 times adds no coverage, only runtime.

2. **Setup is a test once.**  A dependency graph node calls
   ``Workflow.ensure_repo("dev", "x")`` (or another ``ensure_*`` operation) the
   first time and verifies its result through the full transport.  Subsequent
   workflow steps reuse the verified node without repeating setup.  Concurrent
   requests for one node share the same in-flight setup task.  The creation
   path is tested exactly once per unique state node.

3. **Cached tokens via ``World.token()`` / ``get_token()``.**  Tokens
   are cached per (user, scopes) key — one minted per combination per
   suite run.  This exercises the token-creation path at least once per
   combination while keeping Gitea logs manageable.  The ``World.token()``
   method and the module-level ``get_token()`` function share the same
   cache.

4. **Repository, team, org, and user cleanup is lifecycle-owned.**  ``World.cleanup()``
   deletes run-owned entities in reverse dependency order (repos, teams, orgs,
   users) before pooled servers close.  An ``OwnershipLedger`` distinguishes
   run-created entities from pre-existing ones — only recorded entities are
   deleted.  Token cleanup is an accepted limitation (token IDs are not tracked).
   Cleanup attempts every entity and preserves an existing test failure if
   teardown also encounters an error.  ``purge_repo()`` still runs before
   creation to recover from interrupted runs.

5. **Worker-local Worlds and isolation.**  The ``world`` fixture is
   session-scoped per pytest worker.  ``World.start()`` bootstraps users, org,
   and team once per worker, and worker/run-specific names prevent concurrent
   workers or invocations from sharing Forgejo entities.  Tests assigned to
   one worker execute sequentially; independent live stories may run in
   parallel across workers.  New workflow tests must obtain prerequisites
   through the graph and must not use module globals or ``pytest.*`` attributes
   for state.

6. **Async leak detection.**  An autouse fixture
   ``_detect_async_leaks`` in ``tests/live/conftest.py`` runs after every
   test.  It checks for new running asyncio tasks and fails the test
   if any are found (skipping only known MCP and intentionally long-lived
   ``world-server-*`` tasks).  Combined with the existing
   ``_reset_module_contexts`` fixture (ContextVar reset), this guards
   against state leakage with session-scoped event loops.

7. **Cross-format equivalence.**  The ``assert_formats_equivalent`` helper
   calls the same tool with ``format=json`` and ``format=markdown``, then
   verifies that key leaf values from the JSON result appear in the markdown
   output.  This proves that the two formats carry equivalent information
   through the real transport — principle 5 of the live test design.

8. **Shape/content assertions.**  Every tool call in a world-setup or
    workflow test asserts not just "no error" but structural correctness:
    required keys are present (``assert_keys``), key types are correct
    (``assert_key_types``), and specific values match (``assert_content``).
    See ``tests/live/assertions.py``.

9. **Mutable postcondition modeling.**  ``RepoState.need_issue`` and
    ``need_pull_request`` accept an optional ``state`` parameter that declares
    the entity's expected runtime state (``"open"``, ``"closed"``).  When a
    cached entity's observed state differs from the expected postcondition,
    the entity is re-read from the Gitea instance via ``gitea_issue_get_issue``
    / ``gitea_repo_get_pull_request`` and verified.  A
    :class:`PostconditionError` is raised if the actual state still does not
    match.  For pull requests, an :class:`IrreversibleTransitionError` guards
    against tests that expect ``state="open"`` on a merged PR (merging is
    permanent).

    Postcondition checks fire on **cache hits only**.  On the first
    encounter — whether the entity is freshly created or adopted from a
    previous run via Gitea listing — the *state* is stored as a
    declaration of intent.  Only a subsequent ``need_*`` call that finds
    a cached entity with a mismatched ``state`` triggers the re-read and
    verification.  This means the postcondition model correctly supports
    the most common workflow pattern (test A mutates, test B verifies)
    while keeping the first-encounter path simple.

    Pure unit tests in ``test_postcondition.py`` exercise every
    path with mocked ``ClientSession.call_tool`` responses — no live instance
    needed.

10. **Bootstrap verification unit tests.**  ``World.need_user``,
    ``need_org``, and ``need_team`` contain verification logic that only
    fires when a pre-existing entity happens to have mismatched config on
    the live instance — rare in CI.  ``test_bootstrap_verify.py`` uses
    mocked admin ``ClientSession.call_tool`` responses to exercise every
    path: login/email/active/prohibit_login mismatches for users,
    username/full_name mismatches for orgs, permission/units_map mismatches
    for teams, plus the error-to-success (already-exists-adopted) path for
    each entity type.  All 20 paths are pure unit tests with no live
    dependency.

## Infrastructure

- ``conftest.py`` provides ``live_available`` (skipif marker),
  ``gitea_url`` / ``admin_token`` / ``server_args`` (session fixtures),
  ``world`` (worker-local session World with pooled servers and lazy graph),
  and ``mcp_client(gitea_url, server_args, token)`` (per-test async context
  manager, backward-compatible).
- ``world.py`` defines the ``World`` class (server pool, lazy state graph,
  idempotent ``need_*`` methods), ``OwnershipLedger``, and the backward-
  compatible ``get_token()`` function.
- ``identities.py`` defines canonical test identities, scope constants,
  org/team names, and namespace utilities — re-exported by ``world.py``.
- ``state.py`` defines ``RepoState`` (per-repo state tracker) and the
    internal assertion helpers — re-exported by ``world.py``.
- ``conflict.py`` defines ``ConflictError``, ``BootstrapVerificationError``,
    ``PostconditionError``, ``IrreversibleTransitionError``, ``RepoRequest``,
    and ``check_conflict``.
- ``test_conflict.py`` unit-tests ``RepoRequest`` contracts,
    ``check_conflict`` helper, and error object properties.
- ``test_postcondition.py`` unit-tests mutable postcondition verification
    for ``need_issue`` and ``need_pull_request`` with mocked API responses.
- ``test_bootstrap_verify.py`` unit-tests bootstrap verification logic in
    ``need_user``, ``need_org``, and ``need_team`` with mocked admin
    ``call_tool`` responses.
- ``assertions.py`` provides reusable shape/content/cross-format assertion
  helpers: ``assert_keys``, ``assert_key_types``, ``assert_content``,
  ``assert_result_ok``, ``assert_formats_equivalent``.
- ``helpers.py`` provides the minimal external helpers:
   ``create_user_token()`` (the one httpx call) and ``purge_repo()``.  All other
   tool calls are handled by Workflow dependencies or the World lifecycle.
- ``dependency_graph.py`` provides ``DependencyGraph`` and ``NodeKey``.  A
  factory is both setup and verification; successful values are cached and
  failed factories remain retryable.
- ``workflows.py`` provides ``Workflow.ensure_*`` dependency methods and
  ``Workflow.call`` for target steps.  Workflow facades share the authoritative
  graph owned by their worker-local ``World``; an explicit graph is supported
  only for isolated unit tests.
- ``quality.py`` provides composable ``JsonShape``, ``JsonContent``,
  ``FormatsEquivalent``, ``TextContains``, and ``ErrorContent`` contracts.

## RepoState ``need_*`` Design

The ``need_*`` methods on ``RepoState`` (``need_branch``, ``need_file``,
``need_label``, ``need_milestone``, ``need_issue``, ``need_tag``,
``need_pull_request``) follow an intentional **lazy-materialize** pattern
that is a command-query hybrid by design.

**Why command-query separation is not applied.**  The contract is
"ensure this resource exists and give me the data", not "check if it
exists, then fetch it separately."  Splitting into ``ensure_X()`` (void
command) and ``get_X()`` (query) would double every call site in ~15
workflow test files for zero functional gain.  The cache is a
performance optimisation, not a semantic boundary.

**Three-phase flow.**  Every ``need_*`` method follows the same shape:

1. **Cache hit** — conflict detection on immutable fields
   (``check_conflict``), postcondition verification on mutable state
   (``_verify_*_postcondition``), return cached data.

2. **Adopt from Gitea** — for ``need_issue`` and ``need_pull_request``
   only: scan existing Gitea entities for a title match.  The
   ``_adopt_and_cache_issue`` / ``_adopt_and_cache_pr`` helpers mutate
   the cache AND return the adopted dict — symmetric with step 3.

3. **Create new** — call the Gitea API, cache the result
   (``self.<collection>`` + ``self._*_options``), return fresh data.

**Immutable config vs mutable state.**  Each ``need_*`` method stores
immutable creation parameters in a ``_*_options`` dict
(e.g. ``_issue_options``: body, labels, milestone, assignees) and
mutable postcondition state in a separate ``_*_postcondition`` dict
(e.g. ``_issue_postcondition``: ``"open"`` / ``"closed"`` / ``None``).
This separation means ``check_conflict`` never needs to know about
state — it only compares immutable fields, and a mismatch raises
``ConflictError``.

**Postcondition storage.**  The ``_issue_postcondition`` and
``_pr_postcondition`` dicts record the last caller's expected state.
On first creation (or adoption), the state is always stored.  On
subsequent cache hits, state is only updated when explicitly provided
(``state is not None``) — a ``None`` preserves the prior postcondition.
The stored value is never consulted for the comparison itself (the
comparison always uses ``cached.get("state")`` from the actual entity
data); it serves as documentation of intent and structural separation
from immutable config.

**When to extract a new ``_adopt_and_cache_*`` helper.**  ``need_issue``
and ``need_pull_request`` scan Gitea for pre-existing entities.  This
scan-and-adopt logic is extracted into a private helper when it involves
more than a trivial API call + loop match.  The helper follows the same
hybrid shape: mutate the cache and return the data.  See
``_adopt_and_cache_issue`` and ``_adopt_and_cache_pr`` in
``tests/live/state.py``.

**Adding a new ``need_*`` method.**  Copy the pattern: cache check with
``check_conflict`` → (optional) adopt-from-Gitea helper → create new.
Store immutable config in ``_*_options``, mutable state in
``_*_postcondition``.  Document the new dict fields in the
``RepoState`` docstring.

## How to Add or Change a Live Test

Start by identifying the **story step** or **quality concern** being tested.
Do not repeat an existing workflow merely to reach a resource: declare the
prerequisites and let the World-owned graph reuse them.

### Add a step to a workflow

For example, adding a label to an issue needs a repository, label, and issue.
The setup calls are verified once; the final call is the behavior under test:

```python
from tests.live.assertions import assert_result_ok
from tests.live.quality import JsonShape, FormatsEquivalent
from tests.live.workflows import Workflow


@pytest.mark.live
async def test_add_label_to_issue(world: World) -> None:
    workflow = Workflow(world)
    repo = await workflow.ensure_repo(
        DEV.username, "live-label-story", user=DEV, scopes=SCOPE_WRITE,
    )
    await workflow.ensure_label(repo, "bug", "#ff0000")
    issue = await workflow.ensure_issue(repo, "Login fails")

    result = await workflow.call(
        DEV,
        SCOPE_WRITE,
        "gitea_issue_add_label",
        {
            "owner": DEV.username,
            "repo": repo.name,
            "index": issue["number"],
            "labels": ["bug"],
            "format": "json",
        },
        contracts=(JsonShape(list),),
    )
    assert_result_ok(result)

    await workflow.call(
        DEV,
        SCOPE_WRITE,
        "gitea_issue_get_issue",
        {
            "owner": DEV.username,
            "repo": repo.name,
            "index": issue["number"],
        },
        contracts=(FormatsEquivalent(),),
    )
```

The World owns cleanup. Do not add a ``TestCleanup`` method or delete the
repository manually in the test.

### Add an administration step

Use ``ensure_org`` / ``ensure_team`` for graph dependencies and
``admin_call`` for an admin-token operation:

```python
workflow = Workflow(world)
await workflow.ensure_user(DEV)
org = await workflow.ensure_org("live-org", full_name="Live Organization")
team = await workflow.ensure_team(
    org["username"], "developers", permission="write",
    units_map={"repo.code": "write", "repo.issues": "write"},
)
result = await workflow.admin_call(
    "gitea_org_get_team",
    {"id": team["id"], "format": "json"},
    contracts=(JsonShape(dict, keys=("id", "name", "permission")),),
)
```

### Add a quality concern

Quality contracts are orthogonal to workflows. Use ``TextContains`` for raw
text, ``ErrorContent`` for failures, ``JsonShape`` / ``JsonContent`` for JSON,
and ``FormatsEquivalent`` for information-preservation checks. Attach only
the contracts relevant to the behavior; do not apply every contract to every
call.

For tests requiring fresh server startup semantics, retain the
``mcp_client`` fixture and apply the contract directly:

```python
async with mcp_client(gitea_url, server_args, admin_token) as mcp:
    result = await mcp.call_tool("gitea_call_tool", arguments)
    await ErrorContent(("not found",)).verify(
        mcp, "gitea_call_tool", arguments, result,
    )
```

### Verification loop

Run the smallest relevant test first, then the worker-isolated live suite:

```bash
uv run pytest tests/live/test_workflows.py -q
uv run pytest tests/live/ -n 4 -q
make test
```

**Bug regressions caught live**:

| Regression | Where caught | How |
|------------|-------------|-----|
| Commit status wrong state enum | ``test_repo_workflow.py`` | Setting ``state=pending`` on a commit |
| Param naming divergence (``filepath`` vs ``file_path``) | ``test_repo_workflow.py`` | Calling ``gitea_repo_create_file`` with ``filepath`` |
| Param naming divergence (``tag_name`` vs ``name``) | ``test_repo_workflow.py`` | ``gitea_repo_create_tag`` accepts ``tag_name`` but response uses ``name`` |
| Empty list renders ``_(empty)_`` | ``test_cross_format.py`` | Fixed in ``_format_list_as_markdown`` — was ``*None*``
| Output validation error for text/plain diff | ``test_pr_workflow.py`` | Raw diff through ``gitea_repo_download_pull_diff_or_patch`` |

**Prerequisite**: A running Gitea instance with credentials in
``.env.dev.local`` (written by ``gitea_dev_start.sh``).

**Skip behaviour**: Tests are marked with ``@pytest.mark.live`` and skip
gracefully when no Gitea instance is reachable (checked at collection time).

```bash
# Requires .env.dev.local from gitea_dev_start.sh
uv run pytest tests/live/ -v

# Automatically skips when Gitea is not running
uv run pytest tests/live/ -v    # → all skipped
```

**Coverage target**: Not enforced (requires external service).
