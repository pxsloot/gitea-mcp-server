---
title: Organizations & Teams
description: Managing organizations and teams in Gitea/Forgejo — creating orgs, team types, permissions, member management, and org-level settings.
tags: [organizations, teams, members, permissions, management]
source: Forgejo Docs (CC-BY-SA-4.0)
---

# Organizations & Teams

Organizations allow groups of users to collaborate on shared repositories.

## Creating an Organization

- `gitea_org_create_org` — create a new organization (requires admin or user token with `write:organization`)
- `gitea_org_edit_org` — update org profile (description, website, location, visibility)

## Organization Settings

**Org-level settings** managed via API:
- `gitea_org_edit_org` — update profile and preferences
- Labels — org-wide labels shared across all repos (`gitea_org_create_label`)
- Hooks — org-wide webhooks (`gitea_org_create_hook`)
- `gitea_org_block_user` — block a user from the org
- `gitea_org_list_blocked` — list blocked users
- Quota — manage storage limits (`gitea_org_get_quota`)

## Teams

Teams organize members and grant permissions across repos. Each team has:
- **Name** and **description**
- **Permission level** — Read, Write, or Admin
- **Repository access** — specific repos or all repos in the org

**Team management:**
- `gitea_org_create_team` — create a team (name, permission, repo IDs)
- `gitea_org_edit_team` — update team name, permission, repos
- `gitea_org_delete_team` — remove a team
- `gitea_team_add_team_member` — add a user to a team
- `gitea_team_remove_team_member` — remove from team
- `gitea_team_list_team_members` — list members

**Owner team** — auto-created, full admin access to org and all repos. Cannot be deleted.

## Working with Org Repos

- `gitea_org_list_repos` — list repos owned by the org
- `gitea_repo_create` — create a repo in the org (use org name as owner)
- `gitea_repo_transfer` — transfer a repo to/from an org

## Relevant Tools

- `gitea_org_create_org` / `gitea_org_edit_org` — org lifecycle
- `gitea_org_list_teams` / `gitea_org_create_team` — team management
- `gitea_team_add_team_member` / `gitea_team_remove_team_member`
- `gitea_team_search` — find teams within an org
- `gitea_org_list_hooks` — org webhooks
- `gitea_org_list_labels` — org labels
- `gitea_org_list_members` / `gitea_org_list_public_members`
- `gitea://orgs/{org}` — org profile resource
