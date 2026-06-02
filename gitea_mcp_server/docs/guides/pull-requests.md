---
title: Pull Requests & Git Flow
description: Pull request workflow in Gitea/Forgejo — creating PRs, merge styles (merge/squash/rebase), reviews, CODEOWNERS, and merge message templates.
tags: [PRs, pull requests, merging, reviews, code owners, git flow]
source: Forgejo Docs — Pull requests and Git flow (CC-BY-SA-4.0)
---

# Pull Requests & Git Flow

## Creating a Pull Request

After pushing a branch, a "New Pull Request" button appears on the repo if:
- The branch is not the default branch
- The push occurred within the last 6 hours
- There is no open PR for that branch already

You can also create PRs programmatically at any time via `gitea_repo_create_pull_request`.

## Merge Styles

The repository settings (Settings → Repository → Pull Requests) determine which merge methods are available:

| Style | Behavior | When to Use |
|-------|----------|-------------|
| **Merge** | Creates a merge commit; all commits from the PR branch are preserved | Preserves full history, shows when PR was merged |
| **Rebase** | Rebases commits onto base branch without merge commit | Linear history, clean log |
| **Rebase-merge** | Rebases AND creates a merge commit | Linear history + merge visibility |
| **Squash** | All PR commits combined into a single commit on base | Clean history for small changes |

Style availability is set per-repository. Use `gitea_repo_edit` to configure.

## Reviews

Reviewers can approve, request changes, or comment. Reviews can be:
- **Per-PR** — global review of all changes
- **Per-commit** — review a single commit in a multi-commit PR
- **Line comments** — inline feedback on specific lines

**CODEOWNERS** auto-requests reviews: place a `CODEOWNERS` file in the repo root, `docs/`, or `.forgejo/`. Syntax:
```
src/.* @frontend-team
docs/.* @MyOrg/editors
!README\.md @senior-dev
```

## Merge Message Templates

Configure merge commit messages in Settings → Repository → Merge Message Templates. Templates can reference:
- `{title}` — PR title
- `{body}` — PR description
- `{number}` — PR number
- `{author}` — PR author
- `{co-authors}` — co-author trailers from commits

Example:
```
Merge pull request #{number} from {author}
{title}
```

## Relevant Tools

- `gitea_repo_create_pull_request` — create a PR (head, base, title, body)
- `gitea_repo_edit_pull_request` — update PR title, body, labels, milestone
- `gitea_repo_merge_pull_request` — merge with style selection
- `gitea_repo_list_pull_requests` — list PRs by state
- `gitea_repo_get_pull_request` — get PR details
- `gitea_repo_create_pull_review` — submit a review (approve/request-changes/comment)
- `gitea_repo_list_pull_reviews` — list reviews on a PR
- `gitea_repo_get_pull_request_by_base_head` — find PR by branch pair
- `gitea://repos/{owner}/{repo}/pulls` — PRs resource
