---
title: Labels
description: How Gitea/Forgejo labels work -- creating, archiving, scoped/exclusive labels, organization-wide labels, and filtering.
tags: [labels, issues, PRs, organization, scoped, exclusive]
source: Forgejo Docs -- Labels (CC-BY-SA-4.0)
---

# Labels

Labels classify issues and pull requests. They can be defined at the repository level or organization level.

## Organization-Wide Labels

Organization labels are shared with **all** repositories in the organization -- both existing and newly created. Manage them in the organization Settings page.

## Creating Labels

Each label has:
- **Name** (required) -- unique within the repo/org
- **Color** (required) -- hex color code
- **Description** (optional) -- explains the label's purpose
- **Exclusive** -- see Scoped Labels below

Use `gitea_issue_create_label` to create via API.

## Scoped Labels (Exclusive)

A scoped label contains `/` in its name (not at either end). The scope is the part **before the last `/`**. Only one label per scope can be assigned to an issue/PR at a time.

**Example:**
- `kind/bug` and `kind/enhancement` share scope `kind` → an issue can be bug OR enhancement, not both
- `priority/high`, `priority/low` share scope `priority` → one priority at a time
- `scope/subscope/item` has scope `scope/subscope`

This is controlled by the **Exclusive** flag. All labels with the same scope prefix that have Exclusive set are mutually exclusive.

## Archiving Labels

When a label is no longer useful but still attached to existing issues/PRs, archive it:
- Won't appear as a suggestion when adding labels
- Cannot be assigned to new issues/PRs
- Existing assignments remain intact

## Applying Labels

Via the web UI, open the issue/PR and click Labels. Via API, use label **names** (strings) or **IDs** (integers):
- `gitea_issue_create_issue` -- set labels at creation
- `gitea_issue_edit_issue` -- update labels on existing issue
- `gitea_issue_clear_labels` -- remove all labels

**Both names and integer IDs are validated** against the repository's existing labels.
Unknown values of either type raise a `ValidationError` listing available labels.
String matching is case-insensitive.

**Best practice**: Use integer label IDs for reliability — they are never affected by
renames.

## Labels Resource

The `gitea://repos/{owner}/{repo}/labels` resource provides a formatted, cached view of
all labels including:
- Accepted format: `[string, integer][]`
- Label names, IDs, colors, and descriptions
- Scoped/exclusive flags
- Archived status

Use this resource before creating or editing issues/PRs to discover valid labels.

## Predefined Label Sets

When creating a repo/org, the `Issue Labels` option lets you choose from globally configured label sets (e.g., Default, GitHub-like, etc.).

## Relevant Tools

- `gitea_issue_create_label` -- create a label (name, color, description, exclusive)
- `gitea_issue_edit_label` -- update label properties or archive
- `gitea_issue_delete_label` -- permanently delete
- `gitea_issue_list_labels` -- list repo labels
- `gitea_org_list_labels` -- list org labels
- `gitea://repos/{owner}/{repo}/labels` -- labels resource (cached, formatted)
