# Agent Guidelines

**READ THIS BEFORE ANY WORK.** These are project-specific rules supplementing the loaded skills.

## Important: fastmcp has changed since your training

Always use https://gofastmcp.com/llms.txt for up-to-date documentation.
Dont use old style fastmcp from memory: it will impact code quality negatively.

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
- [ ] Tests pass (`uv run pytest tests/unit/ -x -q`)
- [ ] PR body includes `Fixes #XX`
- [ ] Self-review completed
- [ ] No debug/console.log statements
- [ ] Documentation updated if needed
