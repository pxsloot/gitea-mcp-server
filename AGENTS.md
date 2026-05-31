# Agent Guidelines

**READ THIS BEFORE ANY WORK.** These are project-specific rules supplementing the loaded skills.

## We're building gitea-mcp-server, the tool *you* use

Our focus is on usability for agents, with the least amount of friction,
surprise and confusion. To prevent token waste and context pollution we keep
the results succinct and make discovery easy. gitea-mcp-server is aligned with
agents and should feel as an extention for them: intuitive and natural.

gitea-mcp-server project makes heavy use of the latest version FastMCP. We take
gitea's swagger and convert it to OpenAPI v3, then use that to auto-generate
tools and resources. We add a few synthetic tools for convenience and to enable
lazy loading.

FastMCP is a good framework and we want to work *with* it, not
around or against. The code already transforms gitea swagger OpenAPI v2 to v3,
so we'd rather add code to do conversions/transformations to fix what fastmcp's
api doesn't provide (yet), than hack around with fastmcp internals. When the
fastmcp api is changed later on, the changes to our code would be a rather
simple removal of our 'fix'.


## Load the project skill first

This project has a `gitea-mcp-server` skill in `.opencode/skills/gitea-mcp-server/`.
Load it with `/skill gitea-mcp-server`. It encodes all the architecture,
development workflow, customization patterns, and project conventions from
`docs/ARCHITECTURE.md` and `docs/DEVELOPMENT.md` — you do not need to read
those files separately.

## FastMCP docs always up to date

Always use https://gofastmcp.com/llms.txt for up-to-date documentation.
Do not use old style fastmcp from memory: it will impact code quality negatively.

## Use skill knowledge, don't re-discover

After loading the `gitea-mcp-server` skill, use that knowledge directly.
Do not launch subagent exploration for information already documented in the
skill. Reserve subagents for dynamic investigation: test failures, runtime
behavior debugging, or tracing data flow.

Browse specific source files with the Read tool when you need implementation
details — not a broad subagent exploration.

## Tool naming

Tools are called with `call_tool()` using the names exactly as returned by
`search_tools` (e.g., `gitea_issue_create_issue`). The MCP client's server
instance prefix is handled transparently — do not include it.

## When to commit/push

Only commit, push, or create PRs when explicitly asked by the user.
Do not push changes proactively.

## Red Flags - STOP

- ✗ Editing files on `main` branch
- ✗ PR without `Fixes #XX` reference
- ✗ "Quick fix" that skips issue creation
- ✗ Ignoring test failures

**All of these mean: Abort. Create issue, create proper branch, follow full workflow.**

## Verification Checklist (before PR)

- [ ] Branch created from latest `main`
- [ ] Branch name follows `type/XX-description` format
- [ ] All changes on branch, not main
- [ ] Tests pass (`make test`)
- [ ] PR body includes `Fixes #XX`
- [ ] Self-review completed
- [ ] No debug/console.log statements
- [ ] Documentation, meta docs, docstrings, context updated if needed
