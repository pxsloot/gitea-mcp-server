---
title: Repository Permissions
description: Permission model for repositories -- collaborator roles, organization teams, code owners, and access levels.
tags: [permissions, collaborators, teams, organizations, access control, roles]
source: Forgejo Docs (CC-BY-SA-4.0)
---

# Repository Permissions

Gitea/Forgejo uses a role-based permission model for repositories and organizations.

## Collaborator Levels

Repository collaborators have one of these permission levels:

| Level | Read Code | Write Code | Manage Issues/PRs | Manage Repo Settings | Add Collaborators |
|-------|-----------|------------|-------------------|---------------------|-------------------|
| **Read** | ✓ | -- | -- | -- | -- |
| **Triage** | ✓ | -- | ✓ (label, assign, close) | -- | -- |
| **Write** | ✓ | ✓ | ✓ | -- | -- |
| **Maintain** | ✓ | ✓ | ✓ | ✓ | -- |
| **Admin** | ✓ | ✓ | ✓ | ✓ | ✓ |

**Owner** (repo creator) has all Admin permissions plus the ability to transfer/delete the repository.

## Organization Teams

Organizations manage permissions through teams. Each team has:
- A **name** and **description**
- **Permission level** (Read, Write, Admin) -- affects all repos the team has access to
- **Repository access** -- specific repos or all repos in the org
- **Members** -- users added to the team inherit its permissions

**Team types:**
- **Owner team** -- full admin access to the org and all its repos (auto-created)
- **Regular teams** -- scoped to specific repos with specific permission levels

## Code Owners

Use a `CODEOWNERS` file to auto-request reviews from specific users/teams when files matching patterns are changed. Place it in the repo root, `docs/`, or `.forgejo/` directory.

**Syntax:**
```
# Format: <regex pattern> <@user or @org/team>
src/.* @frontend-team
docs/.* @MyOrg/editors
!README\.md @senior-dev
```

Patterns use Go-format regular expressions. Prefix with `!` for negative rules. Users by `@username`, teams by `@org/team-name`.

## Repository Visibility

- **Public** -- anyone can read, clone, and create issues/forks
- **Private** -- only collaborators and org members can access
- **Limited** -- similar to private but visible to logged-in users (instance setting)

## Relevant Tools

- `gitea_repo_add_collaborator` -- add a collaborator with permission level
- `gitea_repo_delete_collaborator` -- remove collaborator
- `gitea_repo_get_repo_permissions` -- check a user's permission level
- `gitea_repo_list_collaborators` -- list all collaborators
- `gitea_org_create_team` / `gitea_org_edit_team` -- manage teams
- `gitea_team_add_team_member` / `gitea_team_remove_team_member`
- `gitea://repos/{owner}/{repo}/collaborators` -- resource listing
