---
title: Wiki
description: Built-in wiki in Gitea/Forgejo -- git-backed storage, permissions, markdown content, and management.
tags: [wiki, documentation, markdown, git]
source: Forgejo Docs -- Integrated Wiki (CC-BY-SA-4.0)
---

# Wiki

Every repository has a built-in wiki for documentation.

## Git-Backed Storage

The wiki is a separate git repository (`{repo}.wiki.git`). Pages are stored as markdown files. You can:
- Edit via the web UI (with markdown editor)
- Clone and edit locally: `git clone https://gitea.example.com/owner/repo.wiki.git`
- Use standard git operations (commit, push, pull)

## Wiki Structure

- Pages are markdown files (`.md`) in the wiki repository root
- `_Sidebar.md` -- sidebar navigation
- `_Footer.md` -- page footer
- `Home.md` -- default landing page (must exist on wiki init)

## Permissions

Wiki access follows the repository's permission model:
- **Public repo:** anyone can read the wiki
- **Private repo:** only collaborators can read the wiki
- **Write access:** users with write permission to the repo can edit the wiki
- Wiki can be disabled in repository settings (`has_wiki=false`)

## Relevant Tools

- Wiki is primarily managed via git (clone/push/pull) or the web UI
- Use `gitea_repo_edit` with `has_wiki=true/false` to enable/disable
- No dedicated wiki API endpoints -- manage content via the wiki git repo
