---
audience: developer
type: reference
covers: The intent/contract for agent_instructions.md -- voice, content rules, what the doc must not do
---

# Agent Instructions Standards

This document captures the *intent* behind
`gitea_mcp_server/docs/agent_instructions.md` -- the doc injected into every
agent session as server instructions. It is the contract for anyone editing
that file. The machine-checkable invariants live as tests in
``test_server.py`` (see ``test_served_instructions_*``); this file explains
the *why* and the judgment calls that a test cannot catch.

Read this before changing `agent_instructions.md`. The review that shaped these
rules is PR #461 (refs #460); the budget re-baseline and extraction boundary
are from #778.

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

Read it as **welcome + feature introduction + quick start + how to discover
more**, written for the agent as a *user* of the server, not as its
implementer. The agent needs to know how to drive the server, not how it was
built. Implementation internals -- the dual-channel contract
(``content``/``structured_content``), deterministic ``raw``, where the
pagination envelope lives, base64 decoding, skip-slice mechanics -- belong in
`docs/ARCHITECTURE.md`, never on the agent surface. If a sentence explains a
decision *we* made rather than something the agent must *do*, it is in the
wrong doc.

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
   `TOOL_PREFIX`. The doc uses `{{TOOL_PREFIX}}` placeholders which are
   resolved at server startup by ``substitute_placeholders()`` in
   ``server.py``. Do NOT hardcode the literal as the only form, and do NOT
   ship unresolved `{{TOOL_PREFIX}}` placeholders in the served doc -- that
   confuses agents and wastes tokens. The invariant test
   ``test_served_instructions_no_unresolved_placeholders`` guards against
   this.

5. **Invite the agent to use its own tools.** Rather than over-explaining
   mechanics (e.g. how `call_tool` resolves names), tell the agent to run
   `tool_info("{{TOOL_PREFIX}}call_tool")` and try both prefixed and
   unprefixed names. The doc should model the discovery behavior it preaches:
   the agent learns the UX by using the tools, not by reading prose about
   them.

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

8. **Mention `format` for resources and docs too, but never restate its
   default.** `read_resource` and `read_doc` accept `format`
   (json/markdown/raw), not just tools. The *default* is server config
   (`DEFAULT_RESPONSE_FORMAT`, threaded into every tool's schema); the doc
   must not claim a specific format. Point at the schema for the exact value
   — the tool schema is the per-tool source of truth.

## What the doc must NOT do

- Grow into a reference manual. If a section could be a `read_doc` guide or a
  `tool_info` result, cut it and point there. The `tool-output-format` guide is
  the worked example: output/envelope/error detail lives there, the doc points.
- Document implementation internals. The dual-channel contract, deterministic
  ``raw``, envelope location, base64 handling, and skip-slice mechanics are
  developer facts (see `docs/ARCHITECTURE.md`), not user guidance. State the
  observable behaviour, never the machinery -- and never a defensive "it is not
  X" claim (e.g. "never a Python ``repr``").
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
- State a config-derived default (e.g. the `format` default) as a fixed
  value. Config-driven values live in the tool schema; prose points, it does
  not pin. The same rule applies to the agent workflows guides and synthetic
  tool descriptions, which are agent-facing too (#781).

## Budget and the extraction boundary

The injected doc has a line budget, enforced by
``test_agent_instructions_line_budget``. The measurement is the **template** --
``agent_instructions.md`` with placeholders unresolved. The generated
workflow-guide manifest is deliberately excluded: that catalog tracks the guide
count, not the prose we author, and agents use the guides better when they can
see what to expect from them. The test's assertion and budget history are the
single source of truth for the current ceiling (210 lines at the time of
writing).

The budget exists to keep the pressure on every agent's context low while still
being useful in getting the agent up to speed. That means the doc earns its
place by orienting and pointing, not by enumerating: every line it carries is
paid for on every connection. The budget only works if the boundary below is
respected. The injected doc is *orientation*: what the surface is, how to name
and discover things, and the shape of a workflow. Reference-grade content has a
home that is discoverable on demand, and the doc points to it rather than
re-teaching it:

| If the content is... | It belongs in... |
|----------------------|------------------|
| A tool's parameters, output example, schema | ``tool_info`` (and the tool schema) |
| ``format`` / ``detail`` / ``fetch_all`` / ``sudo`` usage | the ``tool-output-format`` guide, which echoes the canonical output contract in ``tools/result_pipeline.py`` (+ the tool schema for per-tool availability) |
| Paging, compact mode, ``$ref`` markers, ``resolve_type``, error shapes | the ``tool-output-format`` workflow guide (``read_doc("tool-output-format")``) |
| A Gitea/Forgejo feature's mechanics | the matching workflow guide |
| Annotation semantics | ``TOOL_ANNOTATIONS.md`` (the doc carries the condensed table) |
| Scope/permission mechanics | ``SCOPE_MODEL.md`` (the doc carries the universal-filtering point) |
| Pipeline internals (dual channel, deterministic ``raw``, envelope location, base64, skip-slice) | ``ARCHITECTURE.md`` (developer docs) -- never the agent surface.  The *contract* -- what each format carries -- is canonical in ``tools/result_pipeline.py`` |

When you add something to the injected doc, first ask whether it can be a
``tool_info`` result, a guide, or a pointer. The default is *point*, not
*re-teach*.

## Relationship to other docs

- `docs/INDEX.md` -- the map of all docs and their audiences.
- `gitea_mcp_server/docs/guides/tool-output-format.md` -- agent-facing guide to
  reading results: formats, compact mode, paging, `$ref` markers, and error
  shapes (the injected doc points here).
- `gitea_mcp_server/tools/result_pipeline.py` (module docstring) -- the
  dev-time canonical output contract (json/raw vs markdown, `detail`); the
  registry descriptions, the agent-time guide, and the injected doc echo it,
  and `format.py` points here.
- `docs/TOOL_ANNOTATIONS.md` -- canonical reference for annotation semantics
  (the agent doc carries only the condensed table).
- `docs/SCOPE_MODEL.md` -- canonical reference for scope/permission mechanics.
- Developer docs (`ARCHITECTURE`, `DEVELOPMENT`, `TESTING_STANDARDS`) -- how the
  server is built; not agent-facing.

## Enforcement

The assertable invariants are guarded by these tests in
``tests/integration/test_server.py``:

| Test | Guards |
|------|--------|
| ``test_served_instructions_no_unresolved_placeholders`` | No ``{{}}`` remains after substitution |
| ``test_served_instructions_no_frontmatter`` | First line is ``# ...`` |
| ``test_agent_instructions_line_budget`` | Template size <= 210 — the assertion is the single source of truth; its history explains each change |
| ``test_served_instructions_key_anchors`` | Key phrases present (filter explanation, scope universality, configurable prefix, ``tool_info`` invite) |
| ``test_agent_surfaces_do_not_hardcode_format_default`` | No agent-facing surface (instructions, ``tool-output-format`` guide, synthetic ``read_resource``/``list_resources`` descriptions) claims a fixed ``format`` default; the default is server config (#781) |
| ``test_tool_output_format_guide_pointer`` | The doc's ``read_doc("tool-output-format")`` pointer resolves to a real, described guide |
| ``test_markdown_vs_json_contract`` | The canonical statement, the guide, the registry ``format``/``detail`` descriptions, and the injected doc agree that ``markdown`` is a schema-derived reading view and ``detail="concise"`` compacts json and markdown (``raw`` excepted); agent-facing docstrings do not hand-copy the parameter text |

A regression in any of these fails ``make test``. This file guards the
*intent* that a test cannot express. Both must be updated together when
the bar changes.
