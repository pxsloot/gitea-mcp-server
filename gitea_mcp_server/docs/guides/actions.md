---
title: Actions (CI/CD)
description: Forgejo Actions -- runner setup, workflow syntax, secrets, variables, OIDC security, and differences from GitHub Actions.
tags: [actions, CI, CD, runner, workflows, automation, OIDC]
source: Forgejo Docs -- Forgejo Actions (CC-BY-SA-4.0)
---

# Actions (CI/CD)

Forgejo Actions provides CI/CD compatible with GitHub Actions workflows.

## Architecture

Actions consists of two components:
1. **Forgejo server** -- schedules and monitors jobs
2. **Forgejo Runner** -- executes jobs (install separately)

Runners are registered at the instance, organization, or repository level.

## Runners

**Registration:**
- `gitea_admin_create_runner` -- register a runner (admin, deprecated in v15)
- Use the web UI to get registration tokens for org/repo runners (v15+)
- `gitea://orgs/{org}/actions/runners` -- list org runners
- `gitea://repos/{owner}/{repo}/actions/runners` -- list repo runners

## Workflow Files

Workflows are YAML files in `.forgejo/workflows/` (or `.gitea/workflows/`). Compatible with GitHub Actions syntax:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make test
```

**Key differences from GitHub Actions:**
- Some GitHub Actions are supported natively; others need `forgejo-actions/setup` alternatives
- Container registry is built-in; use `gitea://packages/` for OCI images
- OIDC tokens for cloud provider auth
- Caching infrastructure may differ

## Secrets & Variables

**Repository-level:**
- `gitea_repo_create_actions_secret` / `gitea_repo_delete_actions_secret`
- `gitea_repo_list_actions_secrets`
- `gitea://repos/{owner}/{repo}/actions/secrets` -- resource

**Organization-level:**
- `gitea_org_create_actions_secret` / `gitea_org_delete_actions_secret`
- `gitea_org_list_actions_secrets`
- `gitea://orgs/{org}/actions/secrets` -- resource

**Variables:**
- `gitea_repo_create_actions_variable` / `gitea_repo_delete_actions_variable`
- `gitea_org_create_actions_variable` / `gitea_org_delete_actions_variable`
- `gitea://repos/{owner}/{repo}/actions/variables` -- resource
- `gitea://orgs/{org}/actions/variables` -- resource

## Security

- **OIDC** -- workflows can request OIDC tokens for cloud provider auth
- **PR security** -- forked PRs run with restricted permissions by default
- **Runner isolation** -- runners should be isolated (Docker, VMs)
- **Docker access** -- configure which registries runners can access

## Relevant Tools

- `gitea_admin_create_runner` -- register instance runner (deprecated)
- `gitea_list_action_runs` -- list workflow runs in a repo
- `gitea_action_run` -- get a specific run's details
- `gitea_repo_create_actions_secret` -- manage secrets
- `gitea_repo_create_actions_variable` -- manage variables
- `gitea_repo_search_run_jobs` -- search jobs by filter
- `gitea://repos/{owner}/{repo}/actions/runs` -- runs resource
