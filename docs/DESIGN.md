---
audience: developer
type: explanation
covers: The design stance -- the north star, the build/buy/adapt ladder, the recurring patterns behind the codebase, and the anti-patterns the project refuses
---

# Design Principles

`ARCHITECTURE.md` describes what the system **is**. This document describes
**why it has that shape** and how to reach a shape that fits -- the values and
heuristics you reason with when you design new work. It is the layer above the
architecture: read it before you commit to a mechanism, not after.

It is present-tense by design. A choice that still steers is a **principle**
here, an **invariant** in `ARCHITECTURE.md`, or a **pitfall** in
`DEVELOPMENT.md`; a choice that no longer steers is history and lives in git.
This document is not a decision log and does not keep one -- see
`DOCUMENTATION_STANDARDS.md` for why.

## What this doc is NOT

This doc covers the design stance -- what we value and how to decide. If you
need:

| Topic | See |
|-------|-----|
| Pipeline, module map, contracts, runtime flows | `docs/ARCHITECTURE.md` |
| Step-by-step recipes (add a customization, resource, tool, formatter) | `docs/DEVELOPMENT.md` |
| Testing layout, zones, fixtures, mocking, live tests | `docs/TESTING_STANDARDS.md` |
| Issue shape, Definition of Done, epics and phases | `docs/ISSUE_STANDARDS.md` |
| Documentation structure (audience, de-dup, Diátaxis) | `docs/DOCUMENTATION_STANDARDS.md` |

---

## The north star: agents are first-class users

The primary consumers of this server are LLM agents. **Every design decision
optimises for agent clarity and token economy.** When two designs are otherwise
equal, the one that is cheaper and less ambiguous *from the agent's seat* wins.

That single value resolves most disputes without a rule:

- **Succinct by default.** Results are shaped for reading, not dumping. `detail`
  and `format` exist so an agent can ask for less; the default already is less.
- **Discover, don't enumerate.** ~400 tools are never listed upfront; they are
  found through search. Progressive disclosure is the norm, not a fallback.
- **Explicit edges.** When something is unusual -- an empty body, a binary
  response, a neutralised parameter -- say so in the output rather than leaving
  the agent to infer it from an empty string.

Everything below is downstream of this.

## The build/buy/adapt ladder

Before writing anything, walk down. Take the **highest** rung that solves the
problem; each rung down is more code you own and more code someone must later
delete.

| Rung | Reach for it when | In this codebase |
|------|-------------------|------------------|
| **Use the facility** | The framework or a dependency already does it | FastMCP transforms and middleware, `httpx`, the BM25 engine, Pydantic settings, `respx` |
| **Adapt behind a seam** | The facility is close, but the contract or the fit is off | The conversion/transform layers, the `x-*` extension metadata, `param_rename.py` |
| **Write your own** | The domain or the spec genuinely demands it, and nothing fits | `tools/result_pipeline.py`, `label_service.py`, `cache_invalidation.py` |

Two rules keep the ladder honest:

- **Prefer the seam that can be removed.** When adapting, isolate the deviation
  behind one module or transform, so it can be deleted the day the upstream
  facility catches up. This is what "work *with* FastMCP" means in practice.
- **Reuse before you grow.** The project already has facilities for validation,
  pagination, caching, search, formatting, scope, and registration. `ARCHITECTURE.md`'s
  Module Map is the inventory; scan it before you add a parallel mechanism. A
  second facility for a solved concern is the most expensive kind of new code.

## The recurring patterns

These are the shapes this codebase keeps taking. Each names the principle, where
it is embodied, and -- where one exists -- the guard that fails the build when
the principle is broken. The guard, not this prose, is the contract.

### 1. Derive, don't curate

**When a fact is latent in a source, compute it from that source.** Curate only
the irreducible residue, and validate that residue against the source so a stale
entry fails loudly instead of silently degrading.

- Embodied: the generic collection view derives relations from the response
  schema, so an unknown type compacts its relations for free
  (`format.py`); the converter keeps only *deficiency* tables and validates them
  against the component schemas at conversion (`openapi_converter/display_hints.py`);
  resource URIs, names, and descriptions derive from the spec
  (`resources/factory.py`); annotations infer from the HTTP method
  (`tools/customize.py`).
- Guard: a stale display hint fails conversion; the module map is executable
  (`tests/unit/test_architecture_doc.py`).

### 2. One source of truth; one writer per channel

**A fact lives in exactly one structure that every consumer reads, and a channel
is written by exactly one place.** Two homes for one fact is a bug waiting for a
refactor to expose it.

- Embodied: `filtered_tools_info` is the one filtering decision, driving tool
  registration, resource visibility, and agent-facing error messages
  (`server_setup/spec_loader.py`); the registration record is the one metadata
  contract, read only through sanctioned accessors (`registration.py`);
  `tools/result_pipeline.render` is the single writer of both output channels;
  `resources/surface.py` is the single source of truth for cache-invalidation
  targets and per-resource TTLs; `pagination.py` owns the paging contract.

### 3. Mirror the source; normalize only the misleading, surgically

**The spec is the source of truth.** We mirror the API one-to-one and reshape
only the quirks that would actively mislead an agent. Every normalization is a
narrow rule with a named trigger, never a broad rewrite.

- Embodied: only agent-misleading spec quirks are normalized, everything else
  is mirrored (`openapi_converter/normalize.py`); the `x-*` strip is surgical,
  schema-level only -- operation-level extensions carry meaning and survive
  (`openapi_converter/core.py`); descriptions default to the spec summary, with
  per-tool overrides kept explicit in `mcp_extensions.yaml`; parameter spelling
  is normalized once and mapped back at the edge (`param_rename.py`).
- Guard: `x-*` preservation tests; the converter's drift guards for the two
  source-driven exceptions.

### 4. Deviations are removable deltas

**Work with the framework, not around it.** When FastMCP lacks something, add a
thin conversion or transform layer *beside* it -- never a hack into its
internals, never a fork. The delta stays visible so it can be lifted out.

- Embodied: the transform chain (`_ToolWrappingTransform`, `TolerantSearchTransform`,
  `GiteaNamespace`, `ExtensionMetadataTransform`), the OpenAPI conversion layer,
  and the `x-*` anomalies we stamp on the spec all live outside FastMCP and can
  be deleted without touching it.
- This is judgement, not a check: the proof is that the layer is one removable
  seam, not a scatter of special cases.

### 5. Many producers, one spine

**Diversity of producers is an executor detail; the agent-facing contract is
one shared path.** An autogenerated tool and a synthetic tool differ only in how
they fetch their data.

- Embodied: `tools/contract.build_transform_fn` is the shared contract spine;
  executors return raw data only (an `ExecutionResult`); `read_resource` is an
  ordinary synthetic tool rendered by the same pipeline; synthetic tools carry
  the same registration record (`tools/synthetic_contract.py`).
- Guard: the "One result pipeline" invariant in `ARCHITECTURE.md`; the
  registration-contract test.

### 6. Filter once, at the earliest point

**Decide visibility at spec-prep time, before tools and resources are built.**
One exclusion decision feeds every downstream surface, so they cannot disagree.

- Embodied: `route_map_fn` drops excluded operations before FastMCP sees them;
  the same `filtered_tools_info` gates auto-generated resources; custom
  resources are gated by `available_scopes`; virtual params are gated by
  `apply_scope_filter`.
- Guard: the scope model tests; the spec-level-filtering invariant.

### 7. Invariants are executable, not prose

**State a rule as a test before you rely on it.** A comment does not fail; a
test does. This is how this project keeps prose from becoming a false
assumption.

- Embodied: the layer contract (`tests/unit/test_layer_contract.py`), the
  executable module map and cross-reference sweep
  (`tests/unit/test_architecture_doc.py`), the agent-instruction line budget,
  and the drift guards on curated tables.
- Guard: the guards *are* the pattern -- a principle without one is marked as
  judgement and watched.

### 8. Discovery over enumeration

**Assume the agent does not know the name.** Make the surface searchable, keep
the default list small, and let depth be opt-in.

- Embodied: lazy loading with pinned synthetics (`tools/search.py`); `search`
  merges tools, docs, and resources under one query with a `type` discriminator
  (`tools/unified_search.py`); resources publish `size_hint` and
  `default_detail` so the agent can budget before reading.
- This is judgement, tested by usage rather than by a unit assertion.

### 9. Build for the agent; dogfood the result

**This server is the tool we use to build itself.** Its quality directly shapes
our own productivity, and the agent-visible promise is verified as a promise,
not merely as API mechanics.

- Embodied: the tool-naming grammar, concise output, tool annotations, workflow
  guides, and the discovery tools all exist for the agent consumer.
- Guard: live tests verify the agent-facing result and UX, not Gitea itself --
  set up with the server's own tools, so the setup is a test; a tool tested once
  is thereafter "just a tool" (`docs/testing/LIVE.md`).

## Applying it: a design checklist

When you shape a change, ask in order:

1. **Whose problem is it?** If the answer is not "the agent's," stop.
2. **Where on the ladder?** Is there a facility, or a seam to adapt? Only write
   new mechanism when nothing fits.
3. **One home?** Does the fact already have a canonical structure? Read it; do
   not add a second.
4. **Derived or curated?** Can this be computed from the spec/schema/registry?
   If it must be curated, validate it against the source and leave a guard.
5. **One writer?** If it writes an agent-facing channel, route it through the
   single pipeline.
6. **Executable?** Can the invariant be a test? If yes, write the test with the
   design, not after.
7. **Can it be removed?** Is the deviation one seam that disappears when the
   framework catches up?

If a step has no answer, the design is not yet understood -- say so and resolve
it before writing code.

## Anti-patterns the project refuses

These are the moves that recur in agent-authored work and that this project
deliberately rejects. Each is the shadow of a pattern above.

- **Ad-hoc registration keys.** Metadata rides the registration record; no
  bespoke `meta[...]` keys.
- **Hand-written relations or formatting per response type.** If the schema can
  express it, derive it.
- **A broad `x-*` strip.** Strip schema-level Go leaks only; operation-level
  extensions carry meaning.
- **Hacking or forking FastMCP internals.** Adapt beside it.
- **Display logic in an executor.** Executors return raw data; the pipeline
  renders.
- **Hardcoded cache-invalidation targets.** Derive them from the spec and the
  registered resource surface.
- **A second source of truth.** Scope, pagination, and the output contract each
  have exactly one.
- **Curating what the source already says.** Descriptions come from the spec;
  overrides are explicit.
- **Enumerating the catalog.** Discovery, not listing; succinct, not exhaustive.
- **A standing decision list.** Chronology goes to git; a live choice becomes a
  principle, an invariant, or a pitfall.
- **"MVP" / "minimal diff" as a design goal.** Quality is the default; a
  refactor opportunity is taken, not deferred.
- **Backward-compatibility shims.** The code is clean and forward; only the
  *now* counts.

## Relationship to other docs

- `docs/ARCHITECTURE.md` -- the mechanism and the executable contracts that
  embody these principles. When a principle here names an invariant, that doc
  holds it.
- `docs/DEVELOPMENT.md` -- the recipes for applying the patterns; its Common
  Pitfalls are the concrete form of the anti-patterns above.
- `docs/DOCUMENTATION_STANDARDS.md` -- the residue model: a decision that still
  steers becomes a principle, invariant, or pitfall; a decision that no longer
  steers is history.
- `docs/INDEX.md` -- this document is the canonical home for design principles.
