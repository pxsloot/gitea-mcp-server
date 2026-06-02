---
title: Server Administration
description: Gitea/Forgejo server administration -- configuration cheat sheet, moderation tools, quotas, command-line interface, and upgrade guides.
tags: [admin, server, configuration, moderation, quotas, CLI, upgrade]
source: Forgejo Docs -- Administrator Guide (CC-BY-SA-4.0)
---

# Server Administration

Server administration tasks require `read:admin` / `write:admin` scopes and site admin privileges.

## Configuration Cheat Sheet

Key configuration categories (configured in `app.ini`):
- **Repository** -- default branch, max file size, PR settings
- **Server** -- domain, protocol, SSH port, HTTP port
- **Database** -- type (SQLite/MySQL/PostgreSQL), host, credentials
- **Security** -- secret keys, reverse proxy auth, CORS
- **Mailer** -- SMTP settings for notifications
- **Actions** -- runner registration, artifact storage, default action versions
- **Quota** -- storage limits per user/org (enabled in `app.ini`)

See the Forgejo Configuration Cheat Sheet for full details.

## Moderation Tools

Admins can moderate users and content:
- `gitea_admin_list_users` -- list all users
- `gitea_admin_edit_user` -- update user properties, activate/deactivate
- `gitea_admin_delete_user` -- delete a user account
- `gitea_admin_create_user` -- create a user
- `gitea_org_block_user` -- block a user from an org
- `gitea_user_block_user` -- user-level blocking

Admins can also:
- View audit logs
- Manage instance-level OAuth2 applications
- View cron jobs and system status
- Search across all repos (`gitea_repo_search`)

## Quota Management

When quotas are enabled (config), you can track and manage storage:
- `gitea_user_get_quota` -- check user quota
- `gitea_org_get_quota` -- check org quota
- `gitea://orgs/{org}/quota` -- quota resource
- Quota subjects: artifacts, attachments, packages

## Command-Line Interface

The `gitea` CLI (run on the server) provides additional operations:
- Admin user management: `gitea admin user create`, `change-password`
- Repository operations: `gitea admin repo delete`
- Maintenance: `gitea doctor`, database migrations
- Dump and restore: `gitea dump`, `gitea restore`

## Upgrading

- Follow the [Upgrade Guide](https://forgejo.org/docs/latest/admin/upgrade/) for version-specific steps
- From Gitea → Forgejo: requires specific migration steps
- Backup database and files before upgrading

## Relevant Tools

- `gitea_admin_create_user` / `gitea_admin_edit_user` / `gitea_admin_delete_user`
- `gitea_admin_list_users` -- list all users
- `gitea_admin_create_repo` -- create repo as admin
- `gitea_admin_delete_repo` -- delete any repo
- `gitea_admin_create_runner` -- register instance runner (deprecated)
- `gitea://version` -- server version resource
- `gitea://server/info` -- server metadata resource
