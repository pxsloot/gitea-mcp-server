---
title: Authentication
description: Authentication methods in Gitea/Forgejo — OAuth2 providers, LDAP, PAM, OIDC, 2FA, and access token authentication.
tags: [auth, OAuth2, LDAP, OIDC, 2FA, authentication, SSO]
source: Forgejo Docs — Authentication (CC-BY-SA-4.0)
---

# Authentication

Gitea/Forgejo supports multiple authentication sources and methods.

## Access Tokens

The primary auth method for API access. See the [Token Scopes guide](gitea://docs/guide/token-scopes) for scope details.

**Token management:**
- `gitea_user_create_token` — create a token with specific scopes
- `gitea_user_delete_token` — revoke a token
- `gitea_user_list_tokens` — list all tokens for the current user
- `gitea://token/scopes` — check active token's scopes

## OAuth2 Provider

Gitea/Forgejo can act as an OAuth2 provider for third-party applications.

**Grant types:**
- Authorization Code Grant (recommended for web apps)
- Implicit Grant (for client-side apps)
- Resource Owner Password Credentials Grant

**Configuration via API:**
- List, create, update, and delete OAuth2 applications
- Administer redirect URIs and client secrets

## LDAP / PAM / FreeIPA

Authentication can be delegated to external directories:
- **LDAP** — authenticate against LDAP (OpenLDAP, Active Directory)
- **PAM** — Linux Pluggable Authentication Modules
- **FreeIPA** — Identity, Policy, and Audit system

Configuration is via server admin settings, not API. See the Forgejo admin docs.

## OIDC (Forgejo Next)

OIDC Group Mappings (Forgejo 15+) allow mapping OIDC groups to organization team membership automatically. Configured in admin settings.

## Two-Factor Authentication (2FA)

Users can enable TOTP-based 2FA on their account:
- `gitea_user_get_twofa_status` — check if 2FA is enabled
- 2FA enrollment must be done via the web UI

## OAuth2 Applications (User-facing)

Third-party applications can request OAuth2 access:
- `gitea_user_list_oauth2_applications` — list authorized apps
- Application grants and revocations

## Relevant Tools

- `gitea_user_create_token` — create scoped API token
- `gitea_user_delete_token` — revoke API token
- `gitea_user_list_tokens` — list tokens
- `gitea_user_get_current` — verify authentication
- `gitea://token/scopes` — active token scope resource
- `gitea://user` — current user profile resource
