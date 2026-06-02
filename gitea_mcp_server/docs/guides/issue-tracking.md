---
title: Issue Tracking
description: Issue tracking in Gitea/Forgejo — creating and managing issues, milestones, projects/kanban boards, dependencies, pinned issues, and search.
tags: [issues, milestones, projects, kanban, tracking, dependencies]
source: Forgejo Docs — The Basics of Issue Tracking (CC-BY-SA-4.0)
---

# Issue Tracking

Issues track bugs, feature requests, and tasks. In Gitea/Forgejo, pull requests are a type of issue — they share the same ID namespace.

## Creating and Managing Issues

- `gitea_issue_create_issue` — create with title, body, labels, assignees, milestone
- `gitea_issue_edit_issue` — update state (open/closed), title, body, labels, milestone
- `gitea_issue_delete_issue` — delete an issue (irreversible)

**Labels:** Pass as names (strings) or IDs (integers). See the [Labels guide](gitea://docs/guide/labels) for scoped labels.

## Issue Dependencies & Blocking

Issues can block other issues. This is tracked bidirectionally:
- `gitea_issue_create_issue_blocking` — mark an issue as blocked by another
- `gitea_issue_list_blocks` — show issues this issue blocks
- `gitea_issue_list_issue_dependencies` — show issues blocking this one

## Milestones

Milestones group issues/PRs toward a target date. Use:
- `gitea_issue_create_milestone` — create (title, description, due date)
- `gitea_issue_edit_milestone` — update
- `gitea_issue_delete_milestone` — delete
- `gitea_issue_get_milestones_list` — list all milestones
- `gitea_issue_get_milestone` — get milestone details with progress

## Projects (Kanban Boards)

Projects provide a kanban-style board for organizing issues/PRs across columns:
- `gitea_repo_list_projects` — list projects in a repo
- `gitea_repo_create_project` — create a project board
- `gitea_issue_create_issue` — assign an issue to a project board column

## Pinned Issues

Highlight important issues at the top of the issue list:
- `gitea_issue_pin_issue` — pin an issue
- `gitea_issue_unpin_issue` — remove pin
- `gitea_issue_list_pinned_issues` — list pinned issues
- `gitea_repo_new_pin_allowed` — check if more pins are allowed

## Automatically Linked References

Issues, PRs, and commit messages can reference each other using:
- `#123` — issue/PR number
- `owner/repo#123` — cross-repo reference
- `SHA` — commit hash (auto-linked)
- `!123` — pull request reference

## Issue & Pull Request Templates

Configured via `.forgejo/` or `.gitea/` directory in the repo. See the [Templates guide](gitea://docs/guide/templates).

## Issue Search

- `gitea_issue_search_issues` — search across all accessible repos
- `gitea_issue_list_issues` — list issues by repo with state filter

## Relevant Tools

- `gitea_issue_create_issue` / `gitea_issue_edit_issue` — main issue operations
- `gitea_issue_list_issues` — list with state, labels, milestone filters
- `gitea_issue_get_issue` — get single issue details
- `gitea_issue_create_comment` — add a comment
- `gitea_issue_create_milestone` / `gitea_issue_get_milestones_list` — milestones
- `gitea_issue_create_issue_blocking` — dependencies
- `gitea://repos/{owner}/{repo}/issues` — issues resource
