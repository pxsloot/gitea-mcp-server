---
audience: developer
type: reference
covers: How we treat documentation -- audience split, the de-duplication invariant, and the pragmatic Diátaxis view
---

# Documentation Standards

This document captures the *principles* behind how this project writes and
maintains documentation. It is the contract for anyone adding, splitting, or
trimming a doc. The machine-checkable map of what doc covers what lives in
`docs/INDEX.md`; this file explains the *why* and the judgment calls a grep
cannot catch.

Read this before restructuring docs (e.g. work on issue #460 and its
follow-ups).

## Three audiences

Documentation is written for three audiences, recorded as YAML frontmatter on
every doc **except** the injected agent doc (which is loaded verbatim and must
stay frontmatter-free):

| Audience   | Who                                                                 |
|------------|---------------------------------------------------------------------|
| `agent`    | An LLM agent using the tools at runtime (instructions injected on connect) |
| `developer`| A contributor to this codebase (human or agent)                      |
| `enduser`  | The person installing and wiring the server into their agent software |

Frontmatter shape (one block, top of file):

```yaml
---
audience: developer
type: <explanation|how-to|reference>
covers: <one line, mirrors the INDEX "Covers" column>
---
```

The `type` field is a *pragmatic* nod to Diátaxis (see below) — it labels the
dominant purpose of the doc, it does not force a folder restructure.

## The de-duplication invariant

> Each topic has exactly one **canonical home**. Every other mention is a
> one-line pointer + link.

But "one home" does **not** mean "say it only once." Overlap is acceptable —
expected, even — when the *angle* differs. The invariant guards against
**redundant copies**, not against **different views on the same topic**.

### Different views are different audiences or purposes

A topic that appears in more than one doc is legitimate when each appearance
serves a distinct reader or goal. Examples from this repo:

- **Transform execution order** has two *different* axes, not one duplicated:
  - The *query-time* transform chain (TolerantSearch → GiteaNamespace →
    ExtensionMetadata → Exclusion → PermissionFilter) — one canonical home in
    `ARCHITECTURE.md`. `SCOPE_MODEL.md` points there rather than repeating it.
  - The *startup customization* order (scope filter → exclusion → runtime
    wrap) — documented only in `DEVELOPMENT.md`, because it answers "what
    happens when I add a customization," a contributor concern with no other
    coverage. It stays; it is just labelled distinctly so the two views do not
    read as contradictory.
- **OpenTelemetry** appears in two docs with different purposes:
  `ARCHITECTURE.md` carries the *design rationale* (why we add custom spans,
  why they are no-ops when unset); `DEVELOPMENT.md` carries the *operational
  how-to* (viewer, exporters, env vars). Both stay; the rationale trims its
  restated detail and points to the how-to.
- **`x-*` stripping** appears as a *design decision* in `ARCHITECTURE.md` and
  as a *contributor pitfall* in `DEVELOPMENT.md`. Different purpose: rationale
  vs warning. Both stay.
- **Scope / `sudo` gating** is a *reference* in `SCOPE_MODEL.md` (the
  mechanism) and a *how-to* in `DEVELOPMENT.md` (the step when adding a param).
  The how-to keeps its angle and points to the reference for the mechanism.

The test for a cut is simple: **is this block saying the same thing from the
same angle as another block?** If yes → collapse to one canonical home + a
pointer. If no (different audience/purpose) → keep both, but trim any
*redundant restatement* so each block owns its angle.

## Pragmatic Diátaxis

We are aware of the Diátaxis quadrant model (tutorial / how-to / explanation /
reference) and apply it **pragmatically**, not dogmatically:

- We label docs with a `type` that reflects their dominant Diátaxis purpose.
- We do **not** force a folder restructure into four quadrants for a small doc
  set. A 5-6 doc project does not need the full quadrant machinery; the
  `audience` + `type` frontmatter plus `INDEX.md` gives the same navigability
  without the overhead.
- A single doc may blend types (a how-to that links to an explanation). That is
  fine. The `type` field names the *dominant* purpose only.

## Structure over content truth

When docs grow, the first win is **structure**, not rewriting prose:

- A first-impression map (`INDEX.md`) with tables/diagrams that point to depth
  elsewhere.
- Clear "start here if…" routing so a reader picks the right doc without
  reading everything.
- Condensed tables in the agent doc; full semantics in the reference docs.

Agents must have the same confidence in the docs as in the tools. A doc that
says where to look is more valuable than one that tries to say everything.

## Relationship to other docs

- `docs/INDEX.md` -- the map of all docs, their audiences, and topic ownership.
- `docs/AGENT_INSTRUCTIONS_STANDARDS.md` -- the contract for the injected agent
  doc specifically.
- This file -- the contract for the documentation *set* as a whole.
