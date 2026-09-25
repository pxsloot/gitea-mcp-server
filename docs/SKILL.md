---
audience: developer
type: reference
covers: Project conventions, workflows, checklists, FAQ, and documentation philosophy for developer agents
---

# Developer Handbook

This is the project knowledge base. It covers the conventions, workflows,
and principles you need to work on gitea-mcp-server. Everything here is
available directly — no skill loading required.

> **Documentation map**: See `docs/INDEX.md` for the full doc set with
> audience, type, and topic ownership for every document.

---

## Documentation philosophy

Code, comments, docstrings, tests, and the `docs/` directory together form a
single layered knowledge base. Through an index (`docs/INDEX.md`) this
knowledge is navigable, making discovery straightforward.

- Documentation is as much a deliverable as code.
- A doc that says where to look is more valuable than one trying to say
  everything.
- Always update and improve the documentation. It makes the next job easier.

The structural policies that follow from this philosophy (audience split,
de-duplication invariant, pragmatic Diátaxis) live in
`docs/DOCUMENTATION_STANDARDS.md`.

---

## RED FLAGS — STOP AND ABORT

- ✗ Starting work without reading the relevant `docs/INDEX.md` first to
    navigate the docs
- ✗ Editing files on `main` branch
- ✗ PR without `Fixes #XX` reference
- ✗ "Quick fix" that skips issue creation
- ✗ Ignoring test failures
- ✗ Launching a discovery for things already documented
- ✗ Launching a subagent for static code structure discovery (the docs
    already have module maps, pipeline diagrams, and data flows)

**Any of these means: Abort. Create issue, create proper branch, read relevant
doc, follow full workflow.**

---

## Invariants are executable, not prose

When you state an invariant — in a design note, a docstring, a comment — turn
it into a check **before** you write the code. A comment does not fail; a test
does.

- "Relations are derived, not curated" → a test that fails if a relation
  appears in a curated table.
- "No shared mutable state between specs" → a test that builds two specs and
  asserts isolation.
- "The output schema never leaks `x-mcp-*`" → a test that asserts the strip.

This is not ceremony. The recurring failure mode is stating the correct
principle and then violating it in the implementation — the comment becomes
the false assumption. If you cannot express the invariant as a check, you do
not yet understand it well enough to rely on it.

## Split at round two

A PR that needs a third review round is telling you something: it is still
surfacing structural decisions, not bugs. At that point, **stop iterating and
split the remaining work** into its own issue/PR.

- Round 1 finds output bugs. Fix them.
- Round 2 finds architecture issues. Fix them, and watch for a third.
- Round 3 means the design is still being discovered. Split it.

Distinguish a **bug** (fix it) from a **missing concept** (the design is
incomplete — pause and split). A finding that adds a new kind of thing (e.g.
"a flag is not an identity") is a missing concept, not a bug.

Large PRs are reviewed serially by layer, not in one pass: a reviewer holds
one layer's invariants at a time. Split by layer, not by size.

---

## Verification Checklist (before PR)

- [ ] Branch created from latest `main`
- [ ] Branch name follows `type/XX-description` format
- [ ] All changes on branch, not main
- [ ] Documentation, meta docs, docstrings, comments, instructions up to
    date. This check is NOT optional. Remember: you are intended audience
    of the documentation.
- [ ] No debug/console.log statements
- [ ] Tests pass (`make test`)
- [ ] PR body includes `Fixes #XX`
- [ ] Self-review completed

---

## When to commit and push

Only commit, push, or create PRs when explicitly asked by the user.
Do not push changes proactively.

---

## Bug hunts

This project dogfoods its own code. When you encounter strange tool
behaviour, notify the user. This could be a bug, and your task is
suboptimal with it in place.

One of two things should then happen:

- create a bug issue and continue the task (preferred, if possible)
- find the cause, create an issue, and fix the bug

---

## Common tasks

### How to start working on an issue

1. **Read `docs/INDEX.md` first** — do not skip this step. It navigates you
   to the right doc for your task.
2. Read the doc for your task (see the "Start here if…" column in INDEX.md)
3. Create a feature branch from `main`
4. Make your changes — test, update docs, update the skill if needed
5. Run `make test` — all tests must pass
6. Create a PR with `Fixes #<issue>` in the body

### How to update documentation

- Every doc in `docs/` has YAML frontmatter: `audience`, `type`, `covers`.
  Keep it accurate.
- `docs/INDEX.md` is the map. If you add, rename, or remove a doc, update
  it.
- `docs/DOCUMENTATION_STANDARDS.md` is the contract for the doc set. Read
  it before restructuring.
- The de-duplication invariant: each topic has one canonical home. If you
  add a new doc, ensure it does not duplicate content that belongs in an
  existing doc. Different angles are fine; redundant copies are not.
- The injected agent instructions live at
  `gitea_mcp_server/docs/agent_instructions.md`. See
  `docs/AGENT_INSTRUCTIONS_STANDARDS.md` before editing it.

### How to write tests

- See `docs/TESTING_STANDARDS.md` for layout, zones, fixtures, and mocking
  rules.
- Happy path + error path for every new tool or feature.
- Use existing tests as patterns.

### How to create an issue

- See `docs/ISSUE_STANDARDS.md` — the contract for problem class, Definition
  of Done (invariants, not design), non-goals, templates, labels, and the
  epic/phase model.

### How to review a PR

1. Verify the checklist in the PR body is complete
2. Check that all changes are on a branch, not `main`
3. Confirm `make test` passes
4. Check for stale documentation references
5. Verify the PR body includes `Fixes #<issue>`

---

## FastMCP documentation

This project uses FastMCP extensively. Always use
https://gofastmcp.com/llms.txt for current docs — training-memory FastMCP
will be stale.
