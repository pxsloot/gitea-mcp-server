---
title: Webhooks
description: Webhooks in Gitea/Forgejo -- event types, payload structure, creation, and management for repository and organization hooks.
tags: [webhooks, events, notifications, integrations, automation]
source: Forgejo Docs -- Webhooks (CC-BY-SA-4.0)
---

# Webhooks

Webhooks notify external services when events happen in Gitea/Forgejo.

## Hook Types

| Type | Scope | Use Case |
|------|-------|----------|
| **Repository hooks** | Single repo | CI/CD, issue sync, repo-specific automation |
| **Organization hooks** | All repos in org | Cross-repo automation, org-wide notifications |
| **System hooks** | Entire instance | Server-wide monitoring (admin only) |

## Supported Formats

- **Gitea/Forgejo** -- JSON payload posted to a URL
- **Slack** -- formatted for Slack integration
- **Discord** -- formatted for Discord webhooks
- **Telegram** -- formatted for Telegram bots
- **Mattermost** -- formatted for Mattermost webhooks
- **Packagist** -- for package registry integration

## Event Types

Webhooks fire on events including:
- `push` -- commits pushed to a branch
- `create` / `delete` -- branch/tag created or deleted
- `issues` -- issue opened, closed, edited, reopened
- `issue_comment` -- comment added, edited, deleted
- `pull_request` -- PR opened, closed, merged, edited, review requested
- `pull_request_review` -- review submitted, edited, dismissed
- `repository` -- repo created, deleted, transferred, renamed, archived
- `release` -- release published, edited, deleted
- `fork` -- repo forked
- `wiki` -- wiki page created, edited, deleted

## Creating and Managing

- `gitea_repo_create_hook` -- create a repo hook (type, URL, events, secret, active)
- `gitea_repo_edit_hook` -- update hook config
- `gitea_repo_delete_hook` -- remove hook
- `gitea_repo_list_hooks` -- list repo hooks
- `gitea_org_create_hook` / `gitea_org_edit_hook` -- org hooks

**Secrets:** Provide a secret token; Gitea signs the payload with HMAC-SHA256 and sends it in the `X-Hub-Signature-256` header. Verify on the receiving end.

## Git Hooks

Git hooks run on the server before/after git operations (pre-receive, update, post-receive). Managed via:
- `gitea_repo_list_git_hooks` -- list available git hooks
- `gitea_repo_edit_git_hook` -- set hook content (script)
- `gitea_repo_delete_git_hook` -- reset to default

## Relevant Tools

- `gitea_repo_create_hook` -- create webhook (type, config, events, active)
- `gitea_repo_edit_hook` -- update webhook
- `gitea_repo_delete_hook` -- delete webhook
- `gitea_repo_list_hooks` -- list repo webhooks
- `gitea_repo_get_hook` -- get single hook details
- `gitea_org_create_hook` / `gitea_org_edit_hook` -- org webhooks
- `gitea_org_list_hooks` -- list org webhooks
- `gitea://repos/{owner}/{repo}/hooks` -- hooks resource
