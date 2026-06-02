---
title: Issue & PR Templates
description: Issue and pull request templates in Gitea/Forgejo -- YAML-based forms, markdown templates, and organization-wide defaults.
tags: [templates, issues, PRs, forms, YAML, configuration]
source: Forgejo Docs -- Issue and Pull Request Templates (CC-BY-SA-4.0)
---

# Issue & PR Templates

Templates standardize the information collected when creating issues and pull requests.

## Template Location

Place configuration in the repository root in one of these directories:
- `.forgejo/`
- `.gitea/`
- `.github/`

Templates are discovered automatically when creating an issue/PR.

## YAML Form Templates

Create `.forgejo/ISSUE_FORM.yml` or `.forgejo/PULL_REQUEST_FORM.yml`:

```yaml
name: Bug Report
title: "[Bug] "
description: Report a bug
body:
  - type: markdown
    attributes:
      value: Thanks for reporting!
  - type: input
    id: version
    attributes:
      label: Version
      placeholder: e.g., 1.2.3
    validations:
      required: true
  - type: textarea
    id: steps
    attributes:
      label: Steps to reproduce
    validations:
      required: true
  - type: dropdown
    id: priority
    attributes:
      label: Priority
      options:
        - Low
        - Medium
        - High
```

**Supported form fields:** `markdown`, `input`, `textarea`, `dropdown`, `checkboxes`

## Markdown Templates

For simpler templates, use markdown files:
- `.forgejo/ISSUE_TEMPLATE.md` -- issue template
- `.forgejo/PULL_REQUEST_TEMPLATE.md` -- PR template

These are pre-filled in the issue/PR body.

## Multiple Templates

Use a `config.yml` to offer multiple template choices:
```yaml
blank_issues_enabled: true
contact_links:
  - name: Discussion Forum
    url: https://example.com/forum
    about: Ask questions here
```

## Organization Defaults

Organizations can set default templates that apply to all new repos:
- Place templates in the organization's `.forgejo` repository
- New repos inherit the org's template directory

## Issue Config Validation

- `gitea_repo_get_issue_config` -- read current issue config
- `gitea_repo_validate_issue_config` -- validate YAML config
- `gitea://repos/{owner}/{repo}/issue_config` -- config resource
- `gitea://repos/{owner}/{repo}/issue_templates` -- templates resource

## Gitignore & Label Templates

When creating a repo, you can apply:
- **Gitignore templates** -- language-specific `.gitignore` files
- **Label templates** -- predefined label sets (e.g., Default, GitHub)
- **License templates** -- open-source license files

Resources: `gitea://gitignore/templates/{name}`, `gitea://label/templates/{name}`, `gitea://licenses/{name}`
