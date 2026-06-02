---
title: Repository Management
description: Repository lifecycle management -- creation, mirrors (push/pull), push-to-create, archiving, transfer, deletion, topics, and flags.
tags: [repositories, mirrors, archiving, transfer, topics, settings]
source: Forgejo Docs (CC-BY-SA-4.0)
---

# Repository Management

## Creating a Repository

Use `gitea_admin_create_repo` (admin) or `gitea_repo_create` for user-visible repos. Key settings:
- **Auto-init** -- create with README, `.gitignore`, and/or license
- **Template** -- mark as template repo for use with `gitea_repo_generate`
- **Issue labels** -- choose a predefined label set at creation
- **Visibility** -- public, private, or limited (instance-dependent)

## Repository Templates

Template repos serve as the base for generating new repos. When generating from a template:
- All files and branches are copied
- Issues, PRs, and wiki content can also be included
- Use `gitea_repo_generate` to create from a template

## Mirroring

**Pull mirrors** -- sync a remote repo into Gitea/Forgejo. Pull mirrors auto-fetch from the source on a schedule. Create with `gitea_repo_create` using the `mirror` parameter.

**Push mirrors** -- push changes from a Gitea repo to a remote destination. Manage with:
- `gitea_repo_add_push_mirror` -- add a push target (URL + credentials)
- `gitea_repo_sync_push_mirror` -- trigger a manual sync
- `gitea_repo_remove_push_mirror` -- remove a push target

## Push-to-Create

Push to a non-existent repo URL and Gitea auto-creates it. Controlled by Git push options:
```
git push -o repo.private=false -o repo.template=true origin HEAD
```
Requires instance config `ENABLE_PUSH_CREATE_USER` to be enabled.

## Archiving

Archiving a repo makes it read-only: no pushes, no issues/PRs, no wiki edits. The repo remains visible. Use:
- `gitea_repo_edit` with `archived=true` to archive
- `gitea_repo_edit` with `archived=false` to unarchive

## Transfer & Deletion

- **Transfer** -- `gitea_repo_transfer` moves ownership to another user or org. New owner must accept.
- **Deletion** -- `gitea_admin_delete_repo` (admin) or repo owner can delete via UI. Irreversible.

## Topics

Topics are searchable tags on a repo. Use:
- `gitea_repo_add_topic` -- add a topic
- `gitea_repo_delete_topic` -- remove a topic
- `gitea_repo_search` -- search repos by topic

## Repository Flags

Flags are arbitrary key-value metadata on repos (Forgejo feature). Example use: marking repos for migration status, internal categorization.

## Relevant Tools

- `gitea_repo_create` -- create repo (user/org)
- `gitea_repo_edit` -- update settings (visibility, description, topics)
- `gitea_repo_delete` -- delete repository
- `gitea_repo_transfer` -- transfer ownership
- `gitea_repo_mirror_sync` -- trigger mirror sync
- `gitea_repo_add_push_mirror` -- add push mirror target
- `gitea_repo_generate` -- create repo from template
- `gitea_repo_search` -- find repos by name, topic, owner
