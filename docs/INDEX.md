# Documentation Index

This is the map. Start here when you do not know which doc to open.

The docs serve three audiences. Each doc below declares its audience and type
so you can pick the right one without reading everything.

## Audiences

| Audience   | Who                                                                 | Primary docs            |
|------------|---------------------------------------------------------------------|-------------------------|
| `agent`    | An LLM agent using the tools at runtime (instructions injected on connect) | `agent_instructions.md` |
| `developer`| A contributor to this codebase (human or agent)                      | `ARCHITECTURE`, `DEVELOPMENT`, `TESTING_STANDARDS`, `SCOPE_MODEL`, `TOOL_ANNOTATIONS`, `AGENT_INSTRUCTIONS_STANDARDS` |
| `enduser`  | The person installing and wiring the server into their agent software | `README.md`, `AGENTS.md` |

## How the docs fit together

`agent_instructions.md` is the only doc injected into agent context at
connection time. It is kept short on purpose: it teaches the naming grammar and
a few workflow skeletons, then points to discovery tools for the rest. The
developer docs explain the codebase itself. `SCOPE_MODEL` and `TOOL_ANNOTATIONS`
are reference material; `ARCHITECTURE` is explanation; `DEVELOPMENT` is how-to;
`TESTING_STANDARDS` is reference for the test suite.

Each topic has exactly one canonical home. Where another doc mentions it, that
mention is a one-line pointer, not a copy.

## Document map

| Doc | Audience | Type | Covers | Start here if... |
|-----|----------|------|--------|------------------|
| `gitea_mcp_server/docs/agent_instructions.md` | agent | reference + how-to | Tool/resource naming grammar, discovery flow, issue/PR workflow skeletons, output formats, annotations, troubleshooting | You are an agent about to call a tool |
| `README.md` | enduser | reference | Install, config env vars, transports (stdio/http/docker), quick start | You are installing or running the server |
| `AGENTS.md` | developer, enduser | reference | Project rules, red flags, branch/PR workflow, skill loading | You are about to change code in this repo |
| `docs/ARCHITECTURE.md` | developer | explanation | Pipeline (Swagger 2.0 -> FastMCP), module map, design decisions, content-type handling, runtime flows | You need to understand how the server is built |
| `docs/DEVELOPMENT.md` | developer | how-to | Env setup, running, adding customizations/resources, MCP extensions, exclusion config, OTEL | You are adding a feature or changing behavior |
| `docs/TESTING_STANDARDS.md` | developer | reference | Test layout, zones, fixtures, mocking rules, coverage targets | You are writing or reviewing tests |
| `docs/SCOPE_MODEL.md` | developer | reference | Token scope -> tool/resource visibility, virtual param gating, scope derivation | You need to know why a tool is hidden or how `sudo` appears |
| `docs/TOOL_ANNOTATIONS.md` | developer | reference | Annotation fields (title, tags, hints), how they are inferred | You need the full semantics of readOnly/destructive/idempotent/openWorld hints |
| `docs/AGENT_INSTRUCTIONS_STANDARDS.md` | developer | reference | The intent/contract for `agent_instructions.md`: voice, content rules, what the doc must not do | You are editing the injected agent instructions |
| `docs/DOCUMENTATION_STANDARDS.md` | developer | reference | How we treat documentation: audience split, the de-duplication invariant, the pragmatic Diátaxis view | You are adding, splitting, or trimming a doc |

## Topic ownership (canonical home)

When the same subject appears in more than one place, this is the source of
truth. Other mentions point here.

| Topic | Canonical home |
|-------|---------------|
| Tool naming / prefix / lazy loading | `agent_instructions.md` (grammar) + `ARCHITECTURE.md` (design decisions) |
| Tool annotations | `TOOL_ANNOTATIONS.md` |
| Module map | `ARCHITECTURE.md` |
| Transform execution order | `ARCHITECTURE.md` |
| Scope / permissions / `sudo` gating | `SCOPE_MODEL.md` |
| Pagination / `fetch_all` | `agent_instructions.md` (usage) + `ARCHITECTURE.md` (pipeline, data flow) + `DEVELOPMENT.md` (virtual params how-to) |
| OpenTelemetry | `DEVELOPMENT.md` |
| `x-*` stripping / content-type handling | `ARCHITECTURE.md` |
| Testing patterns | `TESTING_STANDARDS.md` |
| Agent instructions intent / editing rules | `AGENT_INSTRUCTIONS_STANDARDS.md` |
| Documentation-set principles (audience, de-dup, Diátaxis) | `DOCUMENTATION_STANDARDS.md` |
