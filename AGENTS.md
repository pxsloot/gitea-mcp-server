# Agent Guidelines for development-roles

**READ THIS BEFORE ANY WORK.** These are mandatory requirements.

## Core Principle

All changes must follow: **Issue → Branch → PR → Review → Merge**

No direct edits to main. No bypassing. No exceptions.

## Quick Start

1. **Load skills**: You **must always** load 'development-skill' and any other available skill, even if there is only a 5% chance that the skill is relevant. 
2. **Adhere to standards**: Read the relavant docs/*_STANDARDS.md
3. **Find work**: Look for open issues with `priority/high` or `priority/medium` labels
4. **Create branch**: `git switch -c type/XX-short-description` (XX = issue number)
5. **Make changes**: Work only on your branch
6. **Create PR**: Link the issue with `Fixes #XX` or `Closes #XX` in PR body
7. **Push and PR**: Push branch, open PR, request review

## Mandatory Rules

### Keep detailed and up-to-date todos

To keep focussed on the current task it is important to keep detailed and
up-to-date todos.  Detailed and up-to-date todos are not only a good
organizational tool for the agent, it is a progress indictator for the user.

### Encountering Errors during testing

In order to keep the context focussed and efficient

- Ask a subagent for recommendations
- Fix the code
- Run tests
- if necessary: repeat tasking a subagent

### Starting a task
- **Always** load the mandatory 'developer-skill' skill

### Branch Naming

- Format: `type/XX-short-description`
- Types: `feature`, `fix`, `refactor`, `docs`, `test`
- Example: `fix/31-cso-description` or `docs/33-add-tdd-tests`
- **NEVER** work on `main` directly

### Issue Linking

- Every PR **MUST** reference an issue
- Use GitHub closing keywords: `Fixes #XX`, `Closes #XX`, `Resolves #XX`
- If no issue exists, create one first

### PR Requirements

- Title format: `[Type] Brief description (#XX)`
- Body must include:
  - What changed
  - Why it changed
  - Testing performed
  - Link to related issues
- Keep PRs focused and small (< 300 lines)

### Commit Messages

- Use imperative mood: "Fix bug" not "Fixed bug"
- Be specific: "Add flowchart to merging skill" not "Update docs"
- Reference issues: `(#31)` at end

## Red Flags - STOP

If you catch yourself doing any of these, **STOP IMMEDIATELY**:

- ✗ Editing files on `main` branch
- ✗ Creating a branch from anything but `main`
- ✗ Pushing directly without PR
- ✗ PR without `Fixes #XX` reference
- ✗ "Quick fix" that skips issue creation
- ✗ Ignoring test failures
- ✗ Skipping code review

**All of these mean: Abort. Create issue, create proper branch, follow full workflow.**

## Common Mistakes

1. **"I'll just fix this quickly"** → No. Create issue and branch first.
2. **Forgetting to link issue** → PR cannot be merged without `Fixes #XX`.
3. **Working on stale main** → Always `git pull origin main` before creating branch.
4. **Large, unfocused PRs** → Split into multiple PRs by logical change.
5. **Skipping self-review** → Review your own PR before requesting review.

## Tool Usage

### Gitea MCP Tools

When available, use these instead of git CLI:

- Create branch: `gitea_mcp_gitea_branch_create`
- Create PR: `gitea_mcp_gitea_pr_create`
- Create issue: `gitea_mcp_gitea_issue_create`
- Comment on PR: `gitea_mcp_gitea_pr_comment`

Always include required parameters: `owner`, `repo`, proper branch names, etc.

## Verification Checklist

Before creating PR, verify:

- [ ] Branch created from latest `main`
- [ ] Branch name follows `type/XX-description` format
- [ ] All changes on branch, not main
- [ ] Tests pass (if applicable)
- [ ] PR body includes `Fixes #XX`
- [ ] Self-review completed
- [ ] No debug/console.log statements
- [ ] Documentation updated if needed

## Context

This repository defines mcp server for Gitea. Quality matters

---

**Compliance**: Following these rules is not optional. If you cannot comply, do not accept the task.
