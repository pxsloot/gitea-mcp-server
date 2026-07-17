---
audience: developer
type: reference
covers: The intent/contract for agent_instructions.md -- voice, content rules, what the doc must not do
---

# Agent Instructions Standards

This document captures the *intent* behind
`gitea_mcp_server/docs/agent_instructions.md` -- the doc injected into every
agent session as server instructions. It is the contract for anyone editing
that file. The machine-checkable invariants live as a test (see issue #462,
which adds preprocessing); this file explains the *why* and the judgment calls
that a test cannot catch.

Read this before changing `agent_instructions.md`. The review that shaped these
rules is PR #461 (refs #460).

## Purpose

`agent_instructions.md` is the only doc an agent receives unprompted, on every
connection. It pays a token cost on every session, so its job is narrow and
high-leverage:

- Give the agent **confidence** that everything is discoverable and predictable.
- Teach the **naming grammar** and a few **workflow shapes** so the agent can
  guess correctly and confirm with one search.
- Set **honest expectations** about what exists, what is filtered, and what the
  tools are (and are not).

It is NOT a reference manual. Depth belongs in `tool_info`, the workflow
guides (`read_doc`), and the developer docs. The agent doc points; it does not
re-teach.

## Voice and tone

- Welcome the agent as a valued user, not a burden. A touch of "I've used these
  tools -- here's how to get going" enthusiasm is welcome. It should not read
  as cold industrial boilerplate, nor as marketing fluff.
- Be honest about limits. If something is filtered, say so. If a tool is large,
  say so. Never imply capabilities the server does not have.
- Set expectations up front: what the agent will see, what it will not, and
  why. Predictability beats surprise.

## Content rules (intent)

These are the interpretation decisions from the #461 review. They are guidance,
not grep patterns.

1. **Tools mirror the API 1:1.** The tools are generated from this host's
   Swagger/OpenAPI spec with no invented abstractions. Do NOT call them
   "official Gitea tools" -- we are not Gitea. Convey "as close to the raw API
   as it gets" by showing the generation path, not by claiming endorsement.

2. **Explain where tools come from and why some are missing.** State the filter
   chain: generated from spec -> filtered by token scope -> optionally filtered
   by server config. The visible set is *complete for the agent's token*. A
   missing tool is filtered, not missing. This primes the agent to trust
   `search_tools` instead of hunting for unavailable tools.

3. **Scope filtering is universal.** Every tool and resource is scope-filtered,
   not just admin ones. `sudo` is one scope among others: powerful, ordinary in
   mechanism. Never phrase scope filtering as an admin-only special case.

4. **The prefix is configurable.** It defaults to `gitea_` but is set by
   `TOOL_PREFIX`. Describe it as "the server's configured prefix (default
   `gitea_`)". Do NOT hardcode the literal as the only form, and do NOT ship
   unresolved `{{TOOL_PREFIX}}` placeholders in the served doc -- that confuses
   agents and wastes tokens. Placeholder injection is a server feature (#462);
   until it lands, use plain configurable wording.

5. **Invite the agent to use its own tools.** Rather than over-explaining
   mechanics (e.g. how `call_tool` resolves names), tell the agent to run
   `tool_info("gitea_call_tool")` and try both prefixed and unprefixed names.
   The doc should model the discovery behavior it preaches: the agent learns
   the UX by using the tools, not by reading prose about them.

6. **Workflows are narratives, not CRUD menus.** Show a real sequence the agent
   will actually run -- e.g. plan creates issue + labels -> research reads and
   comments -> plan revises -> dev reads, commits, opens PR -> review reads PR
   and comments. A bare create/read/update/delete table is not a workflow. End
   with a pointer to `search_docs` and the workflow guides, since features
   often need explanation beyond the tools.

7. **`detail="full"` is large -- say so.** The compact `output_example` is
   enough for almost every call. `detail="full"` returns the full JSON Schema
   and is hundreds of lines on big tools. Tell the agent to use it rarely and
   to run it once on a small tool to get a feel for the shape.

8. **Mention `format` for resources and docs too.** `read_resource` and
   `read_doc` accept `format` (markdown/json/raw), not just tools.

## What the doc must NOT do

- Grow into a reference manual. If a section could be a `read_doc` guide or a
  `tool_info` result, cut it and point there.
- Leak metadata into agent context. The doc is loaded verbatim; it must stay
  free of YAML frontmatter and unresolved `{{}}` placeholders.
- Reference repo paths. The agent doc is shipped as a package resource and
  injected as the server instructions; the repo's `docs/` directory is NOT
  available to a deployed server. Never point to `docs/...`, `gitea-mcp-server/...`,
  or name this file by path. The agent reaches everything else through the
  discovery tools (`search_tools`, `search`, `tool_info`) and the workflow
  guides (`read_doc` / `gitea://docs/guide/{topic}`). Refer to this doc only as
  "these server instructions", never by filename.
- Claim completeness it does not have, or omit the filtering that explains
  absence.

## Relationship to other docs

- `docs/INDEX.md` -- the map of all docs and their audiences.
- `docs/TOOL_ANNOTATIONS.md` -- canonical reference for annotation semantics
  (the agent doc carries only the condensed table).
- `docs/SCOPE_MODEL.md` -- canonical reference for scope/permission mechanics.
- Developer docs (`ARCHITECTURE`, `DEVELOPMENT`, `TESTING_STANDARDS`) -- how the
  server is built; not agent-facing.

## Enforcement

The assertable invariants (no frontmatter, no `{{}}`, line budget, key anchor
phrases) are guarded by a test added alongside the #462 preprocessing work, so
a regression fails `make test`. This file guards the *intent* that a test
cannot express. Both must be updated together when the bar changes.
