---
audience: developer
type: reference
covers: How we treat documentation -- audience split, the de-duplication invariant, decision-shaped content, and the pragmatic Diátaxis view
---

# Documentation Standards

This document captures the *structural policies* that follow from the
project's documentation philosophy (see `docs/SKILL.md`). It is the contract
for anyone adding, splitting, or trimming a doc. The machine-checkable map
of what doc covers what lives in `docs/INDEX.md`; this file explains the
*structural rules* and the judgment calls a grep cannot catch.

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
    ExtensionMetadata) — one canonical home in `ARCHITECTURE.md`. Filtering
    (deprecated + scope + config-excluded) now happens at spec-prep time via
    `route_map_fn`; `SCOPE_MODEL.md` covers that and points to `ARCHITECTURE.md`
    rather than repeating the chain.
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
- **`x-*` stripping** appears as a *constraint* in `ARCHITECTURE.md` and as a
  *contributor pitfall* in `DEVELOPMENT.md`. Different purpose: the rule vs the
  warning. Both stay.
- **Scope / `sudo` gating** is a *reference* in `SCOPE_MODEL.md` (the
  mechanism) and a *how-to* in `DEVELOPMENT.md` (the step when adding a param).
  The how-to keeps its angle and points to the reference for the mechanism.

The test for a cut is simple: **is this block saying the same thing from the
same angle as another block?** If yes → collapse to one canonical home + a
pointer. If no (different audience/purpose) → keep both, but trim any
*redundant restatement* so each block owns its angle.

## Decision-shaped content decays

An architecture decision record is a *temporal* genre: it captures a choice at
a moment, usually paired with a lifecycle (proposed → accepted → superseded).
This project keeps no supersession machinery and follows an "only the now
counts" rule (see `docs/SKILL.md`), so a standing "design decisions" list has
no valid container here. Every refactor edits the code that *embodies* a
choice while the prose that steered it stays frozen, so the list is guaranteed
to drift — and the drift is invisible because nothing guards it.

Once a choice is embodied, its prose payload is no longer a *decision*. It is
one of four different things, and each has a home:

| Residue | Question it answers | Home |
|---------|--------------------|------|
| **Invariant / contract** | "X must hold" | A reference section (e.g. `ARCHITECTURE.md` → Contracts & Invariants) plus an executable guard |
| **Pitfall / rationale** | "Y broke Z; don't reintroduce it" | A constraint entry, or `DEVELOPMENT.md` → Common Pitfalls |
| **Mechanism** | "how X works" | The canonical module docstring / module map / flow |
| **Chronology** | "we used to do Y" | Delete — git history and the issue are the record |

The rule:

- Do **not** keep a standing decision list in a developer doc. A decision that
  still steers belongs in one of the first three rows; a decision that no
  longer steers is chronology and goes.
- A surviving constraint is stated as a present-tense rule with a source
  pointer, and ideally enforced by a test. A constraint that exists only as
  prose is the next stale block.
- The steering *process* lives in the issue/plan layer (`ISSUE_STANDARDS.md` →
  "The plan": a plan starts with a decisions log), not in the architecture doc.
  Do not keep a frozen copy.

**The test:** can the sentence be stated without referring to a time or a
discarded alternative? If it can only be phrased as "we chose X over Y" or
"this replaced Z", it is history. If it states a present constraint, it is an
invariant. If it describes how code works, it belongs with the module that owns
it.

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

### "What this doc is NOT" — negative routing

Large docs (>200 lines) carry a **bail-out section** near the top, right after
the introductory paragraph.  It lists the 3–5 adjacent topics most likely to
be confused with the current doc, and points to the correct canonical home:

```markdown
## What this doc is NOT

This doc covers X.  If you need:

| Topic | See |
|-------|-----|
| Y    | `docs/OTHER.md` |
| Z    | `docs/ANOTHER.md` |
```

This is **not** a copy of INDEX.md.  INDEX.md answers "where should I go?"
before you open a doc.  The bail-out section answers "am I in the wrong
place?" after you have already arrived — from a search hit, a cross-reference,
or a wrong guess.  It uses a compact one-liner format and lists only adjacent
concerns, not all docs.

When to add: every doc over ~200 lines.  When to skip: small focused references
(<200 lines) that are self-evident from their title and frontmatter.

### Module docstrings as canonical source

Every production module carries a module-level docstring that is the canonical
source for:
- **Role**: what the module does in one paragraph
- **Public API**: classes and functions a consumer should import
- **Design invariants**: key structural constraints (e.g. circular-import
  breaker pattern, spec-level filtering guarantees)

ARCHITECTURE.md carries the pipeline, contracts & invariants, and
cross-cutting topics.  Its module map is a compact orientation table — one
line per module; for details, read the module docstring.  This means the
docstring and the ARCHITECTURE module map must not drift: if a module's role
changes, update the docstring first (it is the canonical source), then check
the ARCHITECTURE one-liner still matches.

## Relationship to other docs

- `docs/SKILL.md` -- the philosophical foundation: why documentation is
  treated as integral part of the codebase. This file derives its structural
  policies from that foundation.
- `docs/INDEX.md` -- the map of all docs, their audiences, and topic ownership.
- `docs/AGENT_INSTRUCTIONS_STANDARDS.md` -- the contract for the injected agent
  doc specifically.
- This file -- the policies (audience split, de-duplication, Diátaxis) derived
  from the philosophy in `docs/SKILL.md`.
