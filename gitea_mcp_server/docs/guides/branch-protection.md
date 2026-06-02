---
title: Branch & Tag Protection
description: Branch protection rules (force push, approvals, merge restrictions), glob patterns, rule priority, and tag protection with glob/regex patterns.
tags: [branches, protection, tags, rules, permissions, security, glob, regex]
source: Forgejo Docs — Branch and Tag Protection (CC-BY-SA-4.0)
---

# Branch & Tag Protection

Protection rules prevent destructive actions on critical branches and tags.

## Branch Protection

Configured per-repository in Settings → Branch. Each rule protects a branch pattern.

**Pattern matching uses glob** where `/` is the separator and `**` spans across separators:
- `main` — exact branch
- `release/**` — all `release/` branches (e.g., `release/v1`, `release/v2/hotfix`)
- `precious*` — branches starting with `precious`

**Rule priority:** If two rules match the same branch, the one **without a glob** takes precedence over the one with a glob.

**Available protections:**
- Enable/disable push (prevent force push)
- Require pull request before merging
- Require approvals (minimum number of approving reviews)
- Dismiss stale approvals when new commits are pushed
- Require signed commits
- Block merge if PR is outdated (requires status check)
- Restrict who can push to matching branches (users, teams, or nobody)

## Tag Protection

Configured per-repository in Settings → Tags. Each rule matches a tag name pattern and restricts who can create/update it.

**Pattern types:**
- **Exact name:** `v1.0` matches only `v1.0`
- **Glob pattern:** `v*` matches `v`, `v-1`, `version2`
- **Regular expression:** Enclose in `/` slashes

**Glob examples:**
| Pattern | Matches |
|---------|---------|
| `v*` | `v`, `v-1`, `version2` |
| `v[0-9]` | `v0` through `v9` |
| `*-release` | `2.1-release`, `final-release` |
| `forgejo` | only `forgejo` |
| `{v,rel}-*` | `v-1`, `rel-x` |
| `*` | all tags |

**Regex examples:**
| Pattern | Matches |
|---------|---------|
| `/\Av/` | any tag starting with `v` |
| `/\Av\d+\.\d+\.\d+\z/` | semver like `v1.0.17` |
| `/-release\z/` | tags ending in `-release` |
| `/.+/` | all tags |

**Permission control:** If no users or teams are specified in the allowed list, **no one** can create or modify that tag (except repo admins with sufficient scope).

## Relevant Tools

- `gitea_repo_create_branch_protection` — create a branch protection rule
- `gitea_repo_edit_branch_protection` — modify existing rule
- `gitea_repo_delete_branch_protection` — remove rule
- `gitea_repo_list_branch_protection` — list all rules
- `gitea_repo_get_branch_protection` — get single rule details
- `gitea_repo_get_branch` — returns effective branch protection
- `gitea://repos/{owner}/{repo}/branch_protections` — resource
