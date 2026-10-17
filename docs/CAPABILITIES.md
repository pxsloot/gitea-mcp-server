---
audience: developer
type: reference
covers: The capability view — cross-cutting facilities indexed by intent, the metadata channels, and where a new fact belongs
---

# Capability & Facility Map

**Read this before you design a new mechanism.**

`ARCHITECTURE.md` tells you *how the server is built* — the pipeline and the
modules. `DESIGN.md` tells you *how to shape a change* — the principles.
`DEVELOPMENT.md` tells you *how to do a task* — the recipes. This doc answers a
fourth question, the one each of those leaves to the reader:

> **Which existing facility already owns the concern I am about to build for —
> and where does the new fact belong?**

That question has a bad failure mode. Most concerns in this server are
**cross-cutting**: a fact is created in one layer and read in another, or an
agent-facing behaviour spans several modules. Such a facility has no single
module that announces it — it shows up as a pipeline stage, a one-line
module-map entry, and an invariant, three glances apart. A planner who does not
find it builds a *second* facility for a solved concern. The most expensive code
in this repo is a second facility for something that already has one.

This doc is the **capability view**: the codebase indexed by *intent*, alongside
the structure view (`ARCHITECTURE.md`), the principle view (`DESIGN.md`), and the
task view (`DEVELOPMENT.md`). Use it at design time. If your concern is in the
index, use that facility. If it is not, you are inventing a capability — say so
explicitly in the plan.

## How to use it

1. Name your concern in plain words — "mark a tool unusable and explain why",
   "attach a field an agent should see at runtime".
2. Find it, or a **synonym**, in the **Capability index** below. Use the
   facility in the *Canonical home* column; do not add a parallel one.
3. If the concern is "where does this fact live?", use **Metadata channels**
   and the **Where does a new fact go?** rules.
4. If nothing matches, the plan states *"no existing facility; new mechanism
   because …"* — and the reviewer checks that claim.

## Capability index

One row per cross-cutting concern. **Canonical home** is the source of truth;
this table is a pointer, never a copy. Synonyms are listed so a search for the
symptom's wording still lands here.

| Concern — "I need to…" (synonyms) | Facility | Canonical home |
|---|---|---|
| Hide or disable a tool/resource, or block a call and explain why (unavailable, deprecated, excluded, out of scope, "degraded", filtered) | Spec-level filtering + the availability reason model | `tools/filter_info.py`, `server_setup/spec_loader.py` |
| Carry a fact from conversion to registration/runtime (cross-stage) | Operation/schema `x-*` extension | `openapi_converter/` |
| Attach per-tool runtime or exposure metadata | Tool registration record (`meta["registration"]`) | `registration.py` |
| Attach per-resource exposure metadata | Resource registration record | `registration.py` |
| Attach read-time display metadata or formatter context | Resource content meta (`ResourceContent.meta`) | `registration.py`, `resources/factory.py` |
| Expose a per-call agent input stripped before the HTTP call | Virtual parameter | `tools/virtual_params.py` |
| Shape, paginate, or format a result | Single result pipeline | `tools/result_pipeline.py` |
| Validate an argument, or augment a tool schema with constraints/enums | Argument validation (one spine-invoked entry, `validate_call`) + schema augmentation | `validation.py` |
| Render a markdown reading view | Formatters + schema-anchored view + display hints | `format.py`, `tools/display.py`, `openapi_converter/display_hints.py` |
| Invalidate cached resources after a write | Derived invalidation (resource surface + type refs) | `cache_invalidation.py`, `resources/surface.py` |
| Cache a resource read, or serve a cached read (TTL) | Response cache + resource surface | `response_cache.py`, `resources/surface.py` |
| Decide who can see a tool/resource (token scope) | Scope model | `scope.py` |
| Map a renamed parameter back to its wire name | `x-param-rename` contract | `param_rename.py` |
| Derive a resource URI, name, or description | Resource factory + auto derivation | `resources/factory.py`, `resources/auto.py` |
| Override a tool's description/title/params by config | MCP extensions YAML | `server_setup/mcp_extensions.py` |
| Derive tool annotations or hints | Annotation inference + extensions | `tools/customize.py` |
| Search and discover tools, docs, resources | Search + discovery transforms | `tools/search.py`, `tools/unified_search.py` |
| Translate an error into an agent-facing shape | Error translation + filtered-tool messages | `tools/errors.py`, `tools/filter_info.py`, `resources/factory.py` |
| Resolve a payload `$ref` chain | Shared ref resolver | `ref_resolver.py` |
| Emit progress or log context to the client | Context helpers (spine-owned) | `context_utils.py`, `tools/contract.py` |

## Metadata channels

The "attach early, read late" channels. They are **distinct containers with
distinct lifecycles** — "meta" in this codebase means several different things,
and choosing the wrong one is how a fact ends up invisible where it is needed.

| Channel | Attached to | Produced by | Read by | On the wire? | Use for |
|---|---|---|---|---|---|
| Operation/schema `x-*` (`x-param-rename`, `x-response-type`, `x-resource-types`, `x-modifies-type`, `x-response-transform`, `x-original-content-types`, `x-wildcard-path-param`) | a spec node | converter (`openapi_converter/`) | registration + runtime | no (in-memory spec) | a fact derived at conversion, read later |
| Tool registration record | `tool.meta["registration"]` | `_customize_metadata`, synthetic registration | transforms, executors, serializers | yes (`_meta.registration`) | per-tool exposure/runtime metadata |
| Resource registration record | `resource.meta["registration"]` | resource registration | `list_resources`, resource surface | yes | per-resource exposure metadata |
| Resource content meta | `ResourceContent.meta` | `resources/factory.py`, handlers | `read_resource` executor, formatters | read-time | display metadata + formatter context |
| Virtual params | the tool schema + registry | `tools/virtual_params.py` | contract spine, result pipeline | as parameters | a per-call input the agent supplies |
| `mcp_extensions.yaml` | spec + annotations | config file | spec-prep | no | curated overrides of spec content |

## Where does a new fact go?

The recurring decisions, resolved once:

- An **agent-visible per-tool fact** → the tool registration record; it rides
  `_meta` on the wire.
- A **cross-stage fact** derived at conversion → an `x-*` extension on the spec
  node; never a parallel in-memory map.
- A **read-only display hint** → resource content meta (resources) or the
  registration record's `view_hints` (tools).
- A **per-call agent input** → a virtual parameter.
- A **curated override of spec content** → `mcp_extensions.yaml`.
- A tool that **cannot be served** → the availability reason model
  (`tools/filter_info.py`), not a new record field.

## Relationship to other docs

- `docs/ARCHITECTURE.md` — the structure view (pipeline, Module Map, Contracts
  & Invariants). This doc is its capability-shaped companion: the same system,
  keyed by concern instead of by module or stage.
- `docs/DESIGN.md` — the principle view; its build/buy/adapt ladder routes here
  for the facility inventory.
- `docs/DEVELOPMENT.md` — the task view; every capability here has its how-to
  there.
- `docs/SCOPE_MODEL.md` / `docs/TOOL_ANNOTATIONS.md` — deep references for two
  capabilities this index points at.
- `docs/INDEX.md` — the map of docs; the capabilities above also appear as
  topic rows there.

The map is guarded: `tests/unit/test_capabilities_doc.py` fails the build when a
named canonical home no longer exists, so a stale pointer cannot send the next
planner down the wrong path.
