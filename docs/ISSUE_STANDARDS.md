---
audience: developer
type: reference
covers: Issue creation, AC vs design, epic/phase model, templates
---

# Issue Standards

This is the contract for creating and working with issues: what an issue must
carry, why acceptance criteria are not design, and how multi-phase work is
tracked. It exists because the recurring failure is rarely a wrong decision --
it is a wrong *artifact*: a refactor gets a task list where it needed
invariants, and every plan downstream inherits the mistake.

## What this doc is NOT

This doc covers issue policy and templates. If you need:

| Topic | See |
|-------|-----|
| Developer handbook, PR checklist, red flags | `docs/SKILL.md` |
| Documentation structure (audience, de-dup, Diátaxis) | `docs/DOCUMENTATION_STANDARDS.md` |
| Testing policy, coverage, zones | `docs/TESTING_STANDARDS.md` |
| Architecture, module map, design decisions | `docs/ARCHITECTURE.md` |
| Branch naming, PR mechanics, environment setup | `CONTRIBUTING.md` |

---

## 1. An issue is a problem, not a design

Every issue carries four things:

| Artifact | Holds | Lifetime |
|---|---|---|
| **Problem class** | the recurring seam, not one symptom | durable |
| **Definition of Done** | invariants expressible as a check, + evidence | durable |
| **Non-goals** | adjacent concerns this must not absorb | durable |
| **Design notes** | current best mechanism | mutable -- lives in the plan |

Issues do **not** carry files to rename, functions to delete, line numbers, or
"move X to Y". Those are design notes.

**Why:**
- The developer skill states it directly: treat issues as *symptoms, hints and
  wishes* and fix the underlying class. A mechanism-shaped issue contradicts
  that instruction -- and wins, because agents treat the issue as law.
- Mechanisms are guesses that rot on the first commit. An issue that pins them
  is stale before the branch is cut.
- A precise task list gives false confidence and closes the search for a simpler
  design. Non-goals bound scope *without* dictating mechanism.

**The durable-assertion test.** Can you state the Definition of Done without
naming the implementation?
- Yes → it is DoD.
- No → it is a design note. Put it in the plan.

Examples:

| Statement | Verdict | Where |
|---|---|---|
| No ad-hoc registration meta keys remain; `registration` is the only sanctioned key | DoD | issue |
| A test fails if any wrapped tool lacks a complete carrier | DoD | issue |
| Delete the `_customization` re-read at `mcp_builder:996` | design note | plan |
| Move the scope filter earlier in `server.py` | design note | plan |
| `docs/ARCHITECTURE.md` lines 110, 176, 303 … | design note | plan (grep method) |

---

## 2. Definition of Done, not "acceptance criteria"

The term *acceptance criteria* invites a checklist of the solution. For
refactors it is usually the wrong artifact. Use **Definition of Done**:

- Invariant(s) that must hold when the work is complete.
- Each invariant has a check (a test, a guard, a doc-map entry).
- `make test` green.

**Why:** an invariant survives a redesign of the implementation; a task list
does not. A phase's test becomes the contract between phases (see §6), which is
what makes later-phase redesign cheap.

---

## 3. Issue kinds

| Kind | Carries | Definition of Done shape |
|------|---------|--------------------------|
| `Kind/Bug`, `Kind/Fix` | reproduction (steps, expected/actual), evidence | the invariant that must hold after the fix |
| `Kind/Feature`, `Kind/Enhancement` | capability + agent-visible contract | contract behaviours + tests |
| `Kind/Cleanup` | problem class, invariants, non-goals, phases | invariants + guard |
| `Kind/Epic` | theme, end state, phase map, non-goals | end state measurable |
| `Kind/Planning` | roadmap or process meta-work | the artifact exists |
| `Kind/Research` | question, timebox, output artifact | the artifact exists |
| `Kind/Testing`, `Kind/Documentation`, `Kind/Security` | the property or gap | check + doc/security outcome |

---

## 4. The Epic model

Forgejo has milestones and issues, not epics. An epic is composed from them:

```
Milestone (a theme with a measurable end state)
└─ Epic issue (Kind/Epic) — index, invariants, non-goals
   ├─ Phase issue 1 — one invariant, one PR
   ├─ Phase issue 2 — one invariant, one PR   (depends on #1)
   └─ Phase issue 3 — one invariant, one PR   (depends on #2)
```

- **Milestone** -- the theme. Its description states the measurable outcome
  (e.g. "detector counts trend to zero").
- **Epic issue** -- the parent tracking number. Its body is an *index*, not a
  design.
- **Phase issue** -- one independently valuable increment, with its own
  Definition of Done. Normally one PR (`Fixes #<phase>`).
- **PR** -- one phase. Small enough to review by layer.

**Why:** a single issue carrying many undisciplined PRs has no acceptance
surface until the end; phases give each increment a verdict. The parent keeps
one tracking number for the theme without pretending the design is known.

**Relations.** Use `depends on` to encode *ordering between phases* (phase N
depends on N−1). For epic → child, prefer a task list in the epic body, because
`depends on` marks the epic *blocked* until every child closes -- a status, not
containment. If you do want the epic blocked-by-children, `depends on` is
correct; just know that is what it means.

---

## 5. Templates

### 5.1 Refactor / architecture (phased)

```markdown
## Problem class
<the recurring seam, one or two sentences; not the symptom>

## Why now
<cost of leaving it>

## Definition of Done (invariants)
- [ ] <assertable without naming the implementation>
- [ ] A test fails when the invariant is violated
- [ ] make test green

## Non-goals
- <adjacent work this must not absorb>

## Phases (directional, non-binding -- revised as we learn)
1. <seam removed / invariant locked> -- independently valuable
2. ...
```

### 5.2 Bug

```markdown
## Steps to reproduce
1. ...
## Expected
## Actual
## Environment
## Invariant after the fix
<what must be true, assertable>
```

### 5.3 Feature / enhancement

```markdown
## Capability
## Contract (agent-visible behaviour)
## Non-goals
## Definition of Done
- [ ] ...
```

### 5.4 Epic parent

```markdown
## Theme
## End state (measurable)
<the count / guard / invariant that proves the theme done>
## Phases
- [ ] #N <phase>
- [ ] #M <phase>
## Non-goals
```

---

## 6. Epic phasing rules

1. **A phase ends when a durable invariant is green.** That invariant is the
   contract the next phase must keep passing.
   **Why:** it localizes churn. Later phases can be redesigned without
   unwinding earlier work. Without it, each merged diff *is* the contract and
   late-phase redesign becomes archaeology.
2. **Detail only the next phase.** Later phases are direction + constraints,
   not designs.
   **Why:** designing phases 2..N before phase 1 lands is precisely how later
   phases end up in flux. The plan is a living document, not a spec.
3. **Separate behavior-preserving from behavior-changing phases, and land the
   safe one first.**
   **Why:** the risky change stays reviewable in isolation.
4. **Split at round two, per phase.** A phase PR reaching a third review round
   means the *phase boundary* is wrong, not that it needs more iteration.
   Split the phase.
   **Why:** `docs/SKILL.md` -- "Split at round two." A third round means the
   design is still being discovered.
5. **Record the phase outcome.** Tick the epic task list, comment the revised
   direction on the phase issue, keep a decisions log at the top of the plan.
   **Why:** the tracker must reflect reality, or the next reader plans against
   a stale map.

---

## 7. The plan

The detailed design lives in a local scratch plan (`scratch/`), never pasted
into the issue body (PR body is used to explain the implementation)

- Starts with a **decisions log**: Rev N, what changed, why, what it
  invalidates.
- **Reviewed before coding** (plan review), then the invariants are locked as
  tests *before* the migration -- the milestone principle.
- Plan Rev 2 is healthy. A third revision is a split signal, not a reason to
  keep editing.

---

## 8. Labels

| Label | Use |
|-------|-----|
| `Kind/Epic` | epic parent; body is the index and phase map |
| `Kind/Planning` | roadmap or process meta-work (not an epic with phases) |
| `Kind/Cleanup` | refactor, removal, code quality |
| `Kind/Bug` / `Kind/Fix` | broken / needs fixing |
| `Kind/Feature` / `Kind/Enhancement` | new / improved capability |
| `Kind/Research` | exploratory, timeboxed |
| `Priority/*` | required on every epic and phase |
| `Status/Blocked` | blocked by an external dependency |
| `Reviewed/Confirmed` | triaged |

---

## 9. Relationship to other docs

- This doc is the **canonical home** for issue policy, the epic/phase model,
  and templates.
- `docs/SKILL.md` keeps its short "start working on an issue" checklist and
  points here for the policy.
- `CONTRIBUTING.md` keeps branch/PR mechanics and points here for issue
  creation.
- `docs/INDEX.md` lists this doc under the developer/reference set.

## Why this works

An issue states the *problem* and the *invariants*; the plan holds the
mechanism; phases are bounded by invariants so the test, not the diff, is the
contract between them. The agent keeps its overview (free to find a better
systemic fix), scope stays bounded (non-goals), and progress stays measurable
(invariant checks) -- the milestone principle applied to the issue body, not
just to the implementation.
