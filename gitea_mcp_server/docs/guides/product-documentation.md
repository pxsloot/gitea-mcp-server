---
title: Product Documentation with the Wiki
description: Use the repository wiki as a product documentation layer — holding vision, PRDs, design decisions, and cross-issue context that individual issues can reference.
tags: [wiki, documentation, product, workflow, convention]
---

# Product Documentation with the Wiki

## The problem

Issues describe individual tasks. Milestones group them. But neither carries
rich context — the *why* behind a feature, the requirements that span multiple
issues, or the design decisions that shape implementation.

When this context lives only in someone's head or in scattered discussions, it
gets lost. Every new contributor (human or agent) has to rediscover it.

## The pattern: wiki as product layer

Use the repository wiki as a lightweight product documentation layer. Wiki
pages hold the context that spans issues, while issues focus on individual
units of work.

This separates concerns cleanly:

| Layer | Purpose | Who edits |
|---|---|---|
| `docs/` in repo | Developer docs: architecture, setup, API | Developers (PR workflow) |
| Repository wiki | Product docs: vision, PRDs, design decisions | PM, product owners, anyone (no PR needed) |

The wiki is a separate git repo — version controlled like code, but editable
through the web UI without knowing git.

## Linking conventions

### From an issue to a wiki page

Issue bodies use relative markdown links:

```markdown
See [PRD-Feature-Name](wiki/PageName) for full requirements.
```

The `wiki/` prefix is relative to the repository root.

### From within the wiki

Wiki pages link to each other using Gitea's bracket syntax or standard
markdown — whichever fits:

```markdown
[[Vision]]                       # Gitea wiki syntax (recommended)
[Vision](Vision)                 # Markdown link
```

Both render correctly in the Gitea web UI.

### From a wiki page to an issue

```markdown
See issue #42 for implementation details.
```

## Recommended page organization

Use flat names with hyphens — the Gitea wiki API does not reliably support
`/` in page names:

- `Home` — landing page with navigation
- `Vision` — project direction and principles
- `PRD-Feature-Name` — product requirements for a feature
- `Design-Component-Name` — design decisions and trade-offs

## Templates

### PRD template

```markdown
# PRD-Feature-Name

## Problem
What problem does this solve? Who is affected?

## Requirements
- Bullet list of functional requirements
- Each should be testable

## Out of scope
What is explicitly NOT in scope for this work.

## Examples
Concrete examples of expected behaviour. These double as acceptance criteria.

## Linked issues
- #XX — implementation task
- #YY — related change

## Notes
Context, prior art, discussions, links to related wiki pages.
```

### Design doc template

```markdown
# Design-Topic

## Context
What prompted this decision? What alternatives were considered?

## Decision
What we chose and why.

## Consequences
What this means for the codebase, agents, or users.
```

## Agent workflow

When an issue references a wiki page, read it before implementing:

```python
# Follow the wiki link from the issue body
wiki_page = read_resource("gitea://repos/{owner}/{repo}/wiki/page/{PageName}")
```

This gives you the broader context — requirements, examples, linked issues —
that the issue alone may not capture.

## Adding pages

Create wiki pages either way:

- **Web UI**: Navigate to the wiki tab, click "New Page"
- **Git**: Clone `https://git.example.com/owner/repo.wiki.git`, add a
  `PageName.md` file, commit and push
