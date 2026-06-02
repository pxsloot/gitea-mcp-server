---
title: Token Scopes & Authentication
description: How Gitea/Forgejo API tokens work, the scope model, repository access restrictions, and administrator capabilities.
tags: [auth, tokens, permissions, security, scopes, API]
source: Forgejo Docs -- Access Token Scope (CC-BY-SA-4.0)
---

# Token Scopes & Authentication

API tokens in Gitea/Forgejo are scoped to specific route groups. Every API call must use a token with the appropriate scope.

## Scope Model

Each scope group has a `read:` level (GET routes) and a `write:` level (POST, PUT, PATCH, DELETE -- includes GET).

| Scope | Routes Covered | When to Use |
|-------|---------------|-------------|
| `read:activitypub` / `write:activitypub` | ActivityPub federation | Federation operations |
| `read:admin` / `write:admin` | `/admin/*` | Server administration (site admin only) |
| `read:issue` / `write:issue` | `issues/*`, `labels/*`, `milestones/*` | Issue tracking, labels, milestones |
| `read:misc` / `write:misc` | Settings & miscellaneous | Templates, markup, settings |
| `read:notification` / `write:notification` | `notification/*` | User notifications |
| `read:organization` / `write:organization` | `orgs/*`, `teams/*` | Organizations and teams |
| `read:package` / `write:package` | `/packages/*` | Package registry |
| `read:repository` / `write:repository` | `/repos/*` (except issues) | Repos, files, PRs, releases |
| `read:user` / `write:user` | `/user/*`, `/users/*` | User profiles, settings |

## Token Access Types

When creating a token, three access levels are available:

**All (public, private, and limited)** -- Full access to everything the user can see. No restrictions on scopes.

**Public only** -- Restricted to public repos/orgs. Repository admin and site admin capabilities are disabled. All scope groups available but limited to public resources.

**Specific repositories** -- Restricted to named repos. Only `read:repository`, `write:repository`, `read:issue`, `write:issue` scopes are available. Cannot perform admin operations within the repo (transfer, add collaborators, change visibility).

## Administrator Capabilities

Site administrators with full-access tokens can:
- Impersonate users via `?sudo={username}` on API endpoints
- Access all repos with `write:repository`
- View any user's activity feeds
- Transfer repos without confirmation
- Bypass quota restrictions
- Create repos for other users

Repository administrators can:
- View tracked time by any user on issues
- Query other users' permissions on the repo
- Add/remove collaborators
- Convert mirror repos to normal repos

These capabilities are **disabled** when using a public-only or specific-repository token.

## Relevant Tools

- `gitea_user_get_current` -- verify identity and active token
- `gitea://token/scopes` -- resource listing active token's scopes
- `gitea_admin_create_user` / `gitea_admin_create_org` -- admin operations
