---
audience: developer
type: how-to
covers: Env setup, running, adding customizations/resources, MCP extensions, exclusion config, OTEL
---

# Development Guide

## What this doc is NOT

This doc covers development tasks: env setup, adding features, customization.
If you need:

| Topic | See |
|-------|-----|
| Design rationale, the pipeline diagram, data flow, key design decisions | `docs/ARCHITECTURE.md` |
| Testing patterns, fixture conventions, mocking rules, coverage policy | `docs/TESTING_STANDARDS.md` |
| How token scopes gate tool visibility and how `sudo` appears | `docs/SCOPE_MODEL.md` |
| Project conventions, developer checklists, common tasks | `docs/SKILL.md` |

## Environment Setup

```bash
# Install mise (if not installed): https://mise.jdx.dev
mise install
mise trust
eval "$(mise activate bash)"

# Activate the project virtualenv
.venv/bin/activate  # or: mise exec -- ...

# Install dependencies
uv sync

# Copy and configure env
cp .env.example .env
# Edit .env: GITEA_URL, GITEA_TOKEN
```

**Key**: The `.venv` managed by `mise` must be active. The system Python
will not work -- dependencies are pinned via `uv.lock`.

---

## Running the Server

```bash
# Stdio transport (default)
uv run python -m gitea_mcp_server

# HTTP transport
TRANSPORT_TYPE=http uv run python -m gitea_mcp_server
```

---

## Running Tests

```bash
# All tests (sequential by default)
uv run pytest

# Parallel execution; live tests are worker-isolated and sequential per worker
uv run pytest -n auto

# Optional explicit namespace for concurrent CI runs
GITEA_LIVE_RUN_ID="ci-${CI_PIPELINE_ID:-local}" uv run pytest -n auto

# Specific area
uv run pytest tests/unit/openapi_converter/
uv run pytest tests/unit/test_tool_annotations.py -v

# With coverage
uv run pytest --cov=gitea_mcp_server

# Integration tests (respx-mocked, no external deps)
uv run pytest tests/integration/

# Live end-to-end tests (need real Gitea instance + .env.dev.local)
uv run pytest tests/live/

# Type-check test code (included in `make test`)
make test-types

# Lint + format (included in `make test`)
uv run ruff check gitea_mcp_server/
uv run ruff format --check .

# Apply ruff format to the whole tree
make format

See `docs/TESTING_STANDARDS.md` for full details.

---

## Code Organization Rules

### Public vs Private

- Functions that are **implementation details** should be prefixed with `_`.
- The module's `__all__` documents the intended public API.
- Tests may import private functions (they test internals), but production
  code should only import from `__all__`.

### File Responsibilities

| Directory | Contains |
|-----------|----------|
| `gitea_mcp_server/` | Core modules -- config, client, conversion, server assembly, exceptions, constants, `label_service`, `format` |
| `gitea_mcp_server/tools/` | **Runtime** tool customization -- customize, contract, schemas, errors, labels, examples, exclusion, search, virtual_params, namespace |
| `gitea_mcp_server/resources/` | **Runtime** resource system -- auto-generated, custom, format helpers, scope derivation, resource registration |
| `gitea_mcp_server/server_setup/` | **Startup-only** -- spec loading, MCP builder, extensions, resource orchestration, permissions |
| `gitea_mcp_server/docs/` | **Agent-facing** documentation (loaded as MCP server instructions) |
| `docs/` | **Developer-facing** documentation (this file, ARCHITECTURE.md, etc.) |
| `tests/` | Unit tests (`unit/`) and integration tests (`integration/`) |

### Keeping the Import-Smoke List in Sync

`tests/unit/test_module_imports.py` maintains an ``ALL_MODULES`` list that
every production module must be added to.  When you add a new ``.py`` file
to any subpackage under ``gitea_mcp_server/``, add its dotted module name
to ``ALL_MODULES``.  The list is the single source of truth for import-smoke
and ``__all__``-validation coverage.

---

## How to Add a Tool Customization

Tool customizations are organized under `gitea_mcp_server/tools/`:

| Module | Concern |
|--------|---------|
| `tools/contract.py` | Generic agent-facing contract spine — `build_transform_fn(tool, executor)`, shared by autogen and synthetic tools |
| `tools/customize.py` | Helpers: title/category generation, hint inference, invalidation |
| `tools/schemas.py` | Output schema derivation, `$ref` resolution |
| `tools/errors.py` | Error translation, argument validation runner |
| `tools/labels.py` | Label name→ID conversion, label schema updates |
| `tools/examples.py` | Schema→example generation, tool schema serialization |
| `tools/search.py` | Name-match + BM25 search + `TolerantSearchTransform`, synthetic tools |
| `tools/synthetic_contract.py` | Synthetic registration contract — wrap-me marker + executor registry, virtual-param allowlists, pagination envelope, page/limit bounds |
| `tools/type_info.py` | ``resolve_type`` tool + ``gitea://types/{typeName}`` resource — ``$ref:Type`` name resolution and cross-references |
| `tools/virtual_params.py` | Virtual parameter registry + lifecycle — generic mechanism for agent-facing params stripped before HTTP call. Registered entries: ``sudo``, ``fetch_all``, ``content_type``, ``detail``, ``format``. See the `virtual params how-to`_ below for adding new entries. |
| `tools/namespace.py` | `GiteaNamespace` transform (prefix tools, pass resources) |

Scope derivation — see `docs/SCOPE_MODEL.md` for the full scope model
(derivation, filtering, and virtual-param gating).

The customization pipeline has two phases:

1. **``_customize_metadata()``** in `server_setup/mcp_builder.py` — a thin
   orchestrator (25 lines) that delegates to four focused helpers, applied
   per-tool at startup via OpenAPIProvider's ``mcp_component_fn`` hook:

   - ``_apply_tool_identity()`` — title, annotations, hints, category,
     scope, cache invalidation
   - ``_detect_has_labels()`` (in `tools/customize.py`) — detect
     array-typed labels parameter (drives schema augmentation)
   - ``_compute_tool_schema()`` — pure: bundles six spec queries
     (schema derivation, text/binary/ContentsResponse classification,
     route identity) into a single ``_ComputedSchema`` NamedTuple.
     Followed by three focused mutation phases:
     * ``_apply_schema_postprocessing()`` — schema assignment,
       validation augmentation, label schema updates; orchestrates
       the two helpers below.
     * ``_apply_fallback_schemas()`` — text/plain and no-content
       conditional schemas when ``output_schema`` is ``None``.
     * ``_inject_response_metadata()`` — single-source metadata injection
       — ``x-fastmcp-wrap-result`` and pagination metadata injection.
   - ``_build_customization_meta()`` — the ``component.meta`` contract
     (``required_scope``, ``output_schema_raw``, ``ToolCustomization``) consumed
     by runtime transforms; stamps the ``_WRAP_ME`` ("wrap me") marker that
     opts the tool into the server-level contract transform

2. **``_ToolWrappingTransform``** (registered **server-level** via
   ``mcp.add_transform()`` in ``server.py`` — the first transform in the
   chain, so it wraps tools from every provider before the lazy-loading,
   namespace, and extension-metadata transforms see them).  ``_wrap()`` in
   `server_setup/mcp_builder.py` only touches tools stamped with the
   ``_WRAP_ME`` marker — autogenerated and synthetic tools alike (synthetic
   tools stamp it at registration, see ``tools/synthetic_contract.py``).
   The per-call ``transform_fn`` is built by the shared
   contract spine (``tools/contract.build_transform_fn(tool, executor)``);
   this module supplies the autogen executor.  The executor resolves the MCP
   ``Context`` via ``context_utils.resolve_current_context()`` (which catches
   ``RuntimeError`` from ``CurrentContext()`` outside an active session),
   then threads the ``ToolCustomization`` explicitly through
   ``_run_transform_pipeline(kwargs, tool, customization, ctx=ctx)``,
   ultimately reaching ``_pipeline_with_context()``.  Runtime
   wrapping (validation, label conversion, error handling, text wrapping,
   pagination) all receive ``ctx`` for ``ctx.info()`` logging and
   ``ctx.report_progress()`` calls at key stages, gracefully degraded to
   no-ops when ``ctx`` is ``None``.

Common customizations:

### 1. Schema augmentation (parameter constraints)

Add to `SCHEMA_CONSTRAINTS` in `validation.py`:

```python
SCHEMA_CONSTRAINTS: dict[str, dict[str, Any]] = {
    "owner": {"minLength": 1, "maxLength": 50, "pattern": OWNER_REPO_PATTERN},
    # ... add new parameter constraint
}
```

### 2. Custom annotation hints

Annotations are inferred from HTTP method in `add_inferred_hints()`.
To override for a specific tool, use `mcp_extensions.yaml`:

```yaml
tool_names:
  repo_delete:
    title: "Delete Repository"
    description: "Permanently deletes a repository..."
```

### 3. New validation function

The validation system has two layers. Most cases are handled automatically:

**Schema-driven enum validation** (automatic): If a parameter's resolved
JSON Schema defines an ``enum`` (either directly or inside an
``anyOf``/``oneOf`` branch), ``run_validation`` validates against that
enum — no code needed.  This is the primary path: the spec defines the
valid values, and validation follows.

**Description-to-enum inference** (automatic): When a spec type (like
``CommitStatusState``) has no machine-readable ``enum`` but lists valid
values in its description as quoted strings (e.g. ``"pending",
"success", ...``), ``augment_schema_with_validation`` parses the
description and injects a proper ``enum``.  Both validation and agent-
facing schemas then work correctly without hardcoded values.

Before inference, any ``$ref`` pointers in parameter schemas are resolved
against the tool's local ``$defs`` (see ``_resolve_local_refs`` docstring
in ``validation.py``).  After inference succeeds, the inferred ``enum``
is injected back into the ``$defs`` definition and the original ``$ref``
branch (see ``_inject_enum_into_defs`` docstring).

A Gitea spec gap — three definitions (``EditIssueOption``, …) with bare
``{type: string}`` state fields — is patched with fallback descriptions
during conversion (see ``_patch_missing_state_descriptions`` docstring in
``openapi_converter/core.py``).

**Structural validators** (explicit registration needed only for pattern/
length/type checks that the spec doesn't define):

1. Add validator in ``validation.py``
2. Add to ``SINGLE_VALIDATORS`` dict keyed by parameter name
3. The runtime pipeline ``_ToolWrappingTransform`` in
   ``server_setup/mcp_builder.py`` calls it **automatically** when the
   parameter has no schema-level ``enum`` (schema-driven validation
   takes priority over hardcoded validators).

### 4. Cache invalidation pattern

Add to `TOOL_INVALIDATION_PATTERNS` in `constants.py`:

```python
TOOL_INVALIDATION_PATTERNS: list[tuple[str, str | None, list[str]]] = [
    ("/repos/{owner}/{repo}/topics", None, [PATTERN_REPO]),
    # ...
]
```

### 5. Add a virtual parameter

Virtual parameters appear in the tool schema so agents know about them, but are
stripped from ``kwargs`` before the HTTP call and can transform the result after.
They are registered by appending to the ``_VIRTUAL_PARAMS`` dict in
``virtual_params.py``:

```python
# gitea_mcp_server/tools/virtual_params.py

_VIRTUAL_PARAMS["verbose"] = VirtualParam(
    schema={"type": "boolean"},
    default=False,
    description="Enable verbose output.",
    # Optional: pre-hook runs after extraction, before the HTTP call.
    # Receives (value, kwargs) — may mutate kwargs for the API call.
    pre_hook=_prepare_verbose,
    # Optional: post-hook transforms the result after the API call.
    # Receives (result, value, all_extracted) — all_extracted lets the
    # hook read other virtual params (e.g. format reads detail).
    post_hook=_apply_verbose,
)
```

The lifecycle functions are called automatically in the transform pipeline:

1. ``inject_into(tool.parameters, tool=tool)`` — adds the param to every tool's
   schema.  The ``tool`` argument enables ``tool_predicate`` gating.  Returns
   the injected set, which the caller stamps into
   ``tool.meta["_virtual_params"]`` so extraction matches injection.
2. ``extract_from(kwargs, only=tool.meta["_virtual_params"])`` — pops it from
   kwargs before the HTTP request; params not injected (e.g. ``fetch_all`` on
   an autogen tool) stay in kwargs and are rejected as unknown
3. ``apply_pre_hooks(extracted, kwargs)`` — runs pre-hooks; hooks receive
   ``(value, kwargs)`` and may mutate kwargs (e.g. content encoding)
4. ``executor(kwargs, extracted, ctx)`` — backend execution (HTTP pipeline or
   synthetic impl) with ``ctx`` for progress reporting and logging
5. ``apply_to(result, extracted)`` — runs post-hooks; hooks receive
   ``(result, value, all_extracted)`` for output formatting and cleanup

.. note::

    ``fetch_all`` is **synthetic-only** (the API loop machinery was removed
    in #724; an executor-internal loop is a post-milestone follow-up).  It is
    injected only into synthetic tools via a ``tool_predicate`` on the
    ``_synthetic`` meta marker — autogen tools no longer expose it.

    **Synthetic tools** (``search_tools``, ``search_resources``,
    ``search_docs``, ``search``, ``list_resources``):
    ``fetch_all`` is a virtual parameter injected from the registry
    (via the tool's ``_virtual_params`` allowlist) and popped by the shared
    spine before the executor runs.  Since all data is already in memory
    (tool catalog, doc index, resource list), ``fetch_all`` simply skips the
    page/limit slice and returns all results through the shared
    :func:`~gitea_mcp_server.format.format_paginated_result` utility — no
    loop needed.  These tools are registered declaratively via
    :func:`~gitea_mcp_server.tools.synthetic_contract.register_all_synthetic_tools`
    (``SyntheticToolSpec`` lists); the underlying primitive
    :func:`~gitea_mcp_server.tools.synthetic_contract.register_synthetic_tool`
    stamps the wrap-me marker + executor registry entry, injects the
    virtual-param allowlist, validates ``page``/``limit`` (in the executor),
    and declares the pagination metadata in their output schemas.  Pass
    ``limit_max`` to raise the ``limit`` upper bound beyond the default
    ``PAGE_SIZE_MAX`` (e.g. ``read_doc`` paginates guide lines and allows
    200); the bound is declared as a JSON Schema ``maximum`` on the
    ``limit`` parameter so agents discover it via ``tool_info``.

    Virtual-param **extraction respects each tool's allowlist**: only
    allowlisted registry params are popped from kwargs before validation.
    Both tool families carry the allowlist in ``tool.meta["_virtual_params"]``
    — synthetic tools stamp it at registration, autogen tools have it
    stamped with the actually-injected set (visible + predicate-passing).
    An off-profile registry-name key (e.g. ``detail`` or ``fetch_all`` on a
    format-only tool like ``read_doc``, or ``fetch_all`` on an autogen tool)
    stays in kwargs and is rejected with the shared "Unknown parameter(s)"
    error rather than being silently dropped.

**Scope-gating**: Virtual parameters can be gated behind token scopes.
The mechanism (how `apply_scope_filter` toggles `.visible`, and how a single
`required_scope=` on a `VirtualParam` is picked up automatically) is the
canonical reference in `docs/SCOPE_MODEL.md` → "Virtual Parameter Scope Gating".
From this doc's how-to angle: to add a new scope-gated param, set
`required_scope=` on the `VirtualParam` and nothing else changes.

All parameters — including ``format``, ``detail``, and ``content_type`` — follow
the standard :ref:`virtual params lifecycle <virtual-params-lifecycle>`:
``inject_into`` (schema) → ``extract_from`` (pop from kwargs) →
``apply_pre_hooks`` (mutate kwargs, e.g. content encoding) →
HTTP call → ``apply_to`` (post-hooks: formatting, sudo cleanup).

.. note::

    ``format``'s default is the server-wide ``response_format`` config.
    The VirtualParam registry carries a static default (``"markdown"``);
    callers pass the live config value via ``default_overrides`` on
    :func:`inject_into`.  This keeps the registry static and the override
    explicit — a named parameter, not a hidden post-patch.

### 6. Add a tool-specific parameter

Use :class:`VirtualParam` with a ``tool_predicate`` to gate injection
to specific tools.  Everything lives in the registry — no special-casing
in ``_inject_params`` or ``_make_transform_fn``.

**Step 1 — Define the hook and register the param** in ``virtual_params.py``:

.. code-block:: python

    def _content_type_pre_hook(value: Any, kwargs: dict[str, Any]) -> None:
        """Base64-encode ``content`` when content_type='text'."""
        if value == "text" and "content" in kwargs:
            raw = kwargs.get("content")
            if isinstance(raw, str):
                kwargs["content"] = base64.b64encode(raw.encode()).decode()

    _VIRTUAL_PARAMS["content_type"] = VirtualParam(
        schema={"type": "string", "enum": ["base64", "text"]},
        default="base64",
        description="How the ``content`` parameter is interpreted…",
        tool_predicate=lambda t: "content" in (t.parameters.get("properties", {})),
        pre_hook=_content_type_pre_hook,
    )

**Step 2 — For output-layer params**, use a ``post_hook`` instead.  The hook
receives ``(result, value, all_extracted)`` — the third arg lets it read
other virtual params (e.g. ``format`` reads ``detail``):

.. code-block:: python

    def _format_post_hook(result, value, all_extracted):
        if value == "raw":
            return result
        detail = all_extracted.get("detail", "full")
        raw_schema = all_extracted.get("_raw_schema")
        data = result.structured_content.get("result") if result.structured_content else None
        if data is None:
            return result
        formatted = apply_format(data, value, detail=detail, schema=raw_schema)
        formatted.structured_content = result.structured_content
        formatted.meta = result.meta
        return formatted

    _VIRTUAL_PARAMS["format"] = VirtualParam(
        schema={"type": "string", "enum": ["json", "markdown", "raw"]},
        default="markdown",
        description="Response format control…",
        post_hook=_format_post_hook,
    )

**Step 3 — If a default is dynamic** (e.g. coming from server config),
pass it via :func:`inject_into`'s ``default_overrides`` parameter:

.. code-block:: python

    # In _ToolWrappingTransform._inject_params()
    inject_into(
        tool.parameters,
        tool=tool,
        default_overrides={"format": self._response_format},
    )

**tool_predicate** inspects any :class:`~fastmcp.tools.base.Tool` attribute —
``parameters``, ``tags``, ``name``, ``meta``.  Common patterns:

.. code-block:: python

    # By parameter presence
    tool_predicate=lambda t: "content" in (t.parameters.get("properties", {}))

    # By tag
    tool_predicate=lambda t: "issue" in t.tags

    # By name prefix
    tool_predicate=lambda t: t.name.startswith("repo_")

No hook signature needed?  ``pre_hook=None``, ``post_hook=None`` — the param
is still injected, extracted, and available in ``all_extracted`` for other
hooks to read.

### 7. Add a response content transform

For endpoints whose raw API response shape does not serve agents well
(e.g. base64-encoded JSON that agents want as plain text), patch the
OpenAPI spec during conversion rather than adding pipeline detection logic:

1. In ``openapi_converter/core.py``, detect the endpoint (e.g. by response
   schema ``$ref``) and patch ``produces`` to a content type that triggers
   the correct handling path.

2. Set ``x-response-transform`` on the operation to name the transform
   (e.g. ``"base64-decode"``).

3. In ``_compute_tool_schema``, read the annotation via
   ``_read_response_transform`` and it is stored in the ``_customization``
   contract by ``_build_customization_meta``.

4. In ``_pipeline_with_context``, add a branch that checks
   ``_customization.response_transform`` and applies the transform.

This way both tools and resources (which are auto-generated from the same
spec) pick up the behaviour automatically — no special-case code in
resource registration.

For binary content types (application/zip, application/octet-stream), the
spec already carries ``x-original-content-types`` with the actual MIME type.
Use ``_response_is_binary()`` to detect these and return structured
``content_info`` metadata instead of raw bytes.  Agents get guidance to use
``format="raw"`` for direct access.

---

## How to Add a Custom Resource

### Preferred: Use the factory (``make_api_resource``)

For most API-backed resources, use the factory in `resources/factory.py`.
It auto-derives the response schema from the OpenAPI spec, handles
``str`` vs JSON branching, and registers the resource in one call -- no
manual ``get_success_schema`` / ``unwrap_result_schema`` boilerplate.

1. **Add a display formatter** (if needed) in `tools/display.py`:
   ```python
   @register_formatter("my_type")
   def _format_my_type(data: dict, *, detail: str = "full") -> str:
       ...
   ```

2. **Add a factory call** in `register_custom_resources()` in
   `resources/custom.py`:
   ```python
   from gitea_mcp_server.resources.factory import make_api_resource

   make_api_resource(
       mcp, gitea_client, openapi_spec,
       uri="gitea://my/{param}",
       api_path="/api/path/{param}",
       method="GET",
       format_hint="my_type",
       scope="read:repository",
       cache_ttl=300,
       tags={"my_tag"},
       error_message="My resource '{param}' not found.",
       available_scopes=available_scopes,
   )
   ```

The factory:
- Derives the response schema automatically from ``openapi_spec[api_path][method]``
- Generates a handler closure that calls ``gitea_client.request``
- Handles ``isinstance(data, str)`` branching (text/plain vs application/json)
- Attaches the schema and ``format_hint`` in ``ResourceContent.meta``
- Registers via ``mcp.resource()`` and adds the URI to a caller-owned
  ``tracking_set`` when provided.  ``register_custom_resources()`` creates
  a local set, passes it to every factory call, and returns it so the
  orchestrator can pass it as ``skip_uris`` to
  ``register_auto_generated_resources()``.
- Skips registration when the token's scopes are insufficient
- Returns ``None`` if scope-filtered, the handler otherwise

**Text/plain resources via ``handler_hook``**: For resources that serve
plain text derived from a JSON API response, pass a ``handler_hook`` callback.
The hook receives the raw API response and returns a string; the factory
skips schema derivation, registers with ``mime_type="text/plain"``, and
wraps the hook's result directly:

.. code-block:: python

    async def _my_hook(response: Any) -> str:
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            return response.get("summary", str(response))
        return str(response)

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://my/{param}/content",
        api_path="/api/path/{param}/content",
        method="GET",
        scope="read:repository",
        tags={"my_tag"},
        error_message="Content '{param}' not found.",
        handler_hook=_my_hook,
        available_scopes=available_scopes,
    )

When ``handler_hook`` is set:
- Schema derivation is skipped (no ``response_schema`` in meta)
- The resource is registered as ``text/plain`` (``format_hint`` is ignored)
- The hook is called for every response, including strings
- Query parameters work as usual via ``param_config.query_params``
- Context parameters via ``param_config.context_params`` —
  validated, forwarded to formatters via ``param_config.context_meta_keys``,
  but **never sent to the underlying API**.  Reserved for future use —
  see note below.

All parameter-routing configuration is grouped into a ``ResourceParamConfig``
dataclass.  Key fields:

- ``query_params`` — optional kwargs extracted into the API call's
  ``params`` dict (e.g. ``["state", "type"]``).  Never substituted into the path.
- ``query_param_validators`` — allowed values per query param.  Raises
  ``ResourceError`` on invalid input.
- ``context_params`` — validated kwargs that appear in the URI template
  but are **never** sent to the API.  **Currently unused** — see note below.
- ``context_param_validators`` — allowed values per context param.  Also
  currently unused.
- ``optional_params`` — discovery metadata for ``list_resources`` output.
- ``context_meta_keys`` — handler kwargs forwarded into
  ``ResourceContent.meta`` as display context for formatters.  Works for
  path params, query params, and context params alike.

A param cannot appear in both ``query_params`` and ``context_params``.

> **Note on ``context_params``**: The Gitea/Forgejo API spec describes real
> query parameters — every ``in: query`` param from the Swagger spec is
> accepted and processed by the server.  There is no Gitea API parameter
> that is genuinely display-only.  The ``context_params`` mechanism is kept
> as a clean abstraction for future use (e.g. a non-Gitea backend with
> display-only URL params), but it is not currently consumed by any resource.
> See issue #540 for the full research.

Two formatters currently use context metadata:

- **Issues**: reads ``type`` (query param: ``issues`` / ``pulls``) via
  ``context_meta_keys`` for the resource title.  ``type`` is a real API
  query parameter — it is sent to the API and forwarded to the formatter.
- **Labels**: reads ``owner`` and ``repo`` (path params forwarded via
  ``context_meta_keys``) for the heading (``# Labels for {owner}/{repo}``).

See the factory calls in ``custom.py`` for complete examples.  The issues
resource (query params with context forwarding)::

    from gitea_mcp_server.resources.factory import ResourceParamConfig

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/issues{?state,type}",
        api_path="/repos/{owner}/{repo}/issues",
        format_hint="issues",
        resource_type="issues",
        scope="read:repository",
        tags={"wrapper", "issues"},
        error_message="Repository '{owner}/{repo}' not found.",
        param_config=ResourceParamConfig(
            query_params=["state", "type"],
            query_param_validators={"state": ["open", "closed"], "type": ["issues", "pulls"]},
            optional_params=[
                {"name": "state", "type": "string", "values": ["open", "closed"]},
                {"name": "type", "type": "string", "values": ["issues", "pulls"],
                 "description": "Filter by type (issues / pulls)"},
            ],
            context_meta_keys=["type"],
        ),
        available_scopes=available_scopes,
    )

The error response carries a ``resource_type`` field with the raw API type
value (``"issues"`` / ``"pulls"``).  Human-readable entity names (e.g. "pull
requests") are a display concern for the read_resource layer, not the
resource itself.

And the labels resource (path-param forwarding)::

    from gitea_mcp_server.resources.factory import ResourceParamConfig

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/labels",
        api_path="/repos/{owner}/{repo}/labels",
        format_hint="labels",
        scope="read:issue",
        tags={"wrapper", "labels"},
        error_message="Labels not found for repository '{owner}/{repo}'.",
        param_config=ResourceParamConfig(
            context_meta_keys=["owner", "repo"],
        ),
        available_scopes=available_scopes,
    )

    The ``{?state,type}`` suffix in the URI template is required so FastMCP
    routes ``?state=...`` and ``?type=...`` query strings to the handler.
    The display layer (``clean_resource_uri``) strips the ``{?...}`` suffix
    from ``list_resources`` output, so agents see a clean
    ``gitea://repos/{owner}/{repo}/issues`` URI and discover available params
    via ``optional_params`` metadata.

See the module docstring in ``gitea_mcp_server/resources/factory.py`` for a
complete parameter reference table.

### URI template and param routing

The ``{?param}`` suffix in the URI template serves double duty:

1. **Agent discovery** — FastMCP exposes ``{?param}`` as optional URI parameters.  The display layer (``clean_resource_uri``) strips them from ``list_resources`` output so agents see clean URIs and discover available params via ``optional_params`` metadata.

2. **Signature validation** — FastMCP validates that every ``{?param}`` in the URI template has a matching optional function parameter.  The factory adds param names from ``param_config`` (both ``query_params`` and ``context_params``) to the handler's ``__signature__`` as ``KEYWORD_ONLY`` params with ``default=None``, satisfying this constraint.

A param **cannot** be declared in both ``query_params`` and ``context_params`` — the factory raises ``ValueError`` at registration time if you do.

### Decision guide: which param category?

| Your kwarg... | Use | Because |
|--------------|-----|---------|
| Is a filter the Gitea API understands (``state``, ``type``, ``draft``, ``q``) | ``query_params`` | Sent to the API as ``?key=value`` |
| Is a display hint only (keep this row for when a case appears) | ``context_params`` | Validated and forwarded to formatters, never sent; currently unused |
| Is a path segment (``owner``, ``repo``) | (automatic) | Substituted into ``api_path`` from the URI template |
| Needs post-processing (base64 decode, string transform) | ``handler_hook`` | Receives raw API response, returns a string |

### Complete example

The most fully-featured factory resource (issues) illustrates all categories
working together::

    from gitea_mcp_server.resources.factory import ResourceParamConfig

    make_api_resource(
        mcp, gitea_client, openapi_spec,
        uri="gitea://repos/{owner}/{repo}/issues{?state,type}",
        api_path="/repos/{owner}/{repo}/issues",
        format_hint="issues",
        resource_type="issues",
        scope="read:repository",
        tags={"wrapper", "issues"},
        error_message="Repository '{owner}/{repo}' not found.",
        param_config=ResourceParamConfig(
            query_params=["state", "type"],       # → API as ?state=&type=
            query_param_validators={"state": ["open", "closed"], "type": ["issues", "pulls"]},
            optional_params=[
                {"name": "state", "type": "string", "values": ["open", "closed"]},
                {"name": "type", "type": "string", "values": ["issues", "pulls"],
                 "description": "Filter by type (issues / pulls)"},
            ],
            context_meta_keys=["type"],            # → forwarded to formatter too
        ),
        available_scopes=available_scopes,
    )

No manual skip-URI maintenance is needed — ``register_custom_resources()``
returns the set of URIs registered via ``make_api_resource()`` and
``resource_setup.py`` passes it directly as ``skip_uris`` to
``register_auto_generated_resources()`` — a fresh caller-owned set,
no defensive copy needed.

**Note**: If future patterns repeat (many list resources sharing the same
structure), consider extracting higher-level wrappers like
``make_list_resource()`` that compose ``make_api_resource`` with common
defaults.  The current approach adds params directly to the factory
(``Option A``) — straightforward and zero-impact on existing consumers.

### Static resources (direct ``mcp.resource()``)

For resources with special logic (base64 decoding, static pre-computed data,
non-GET methods) that don't fit the factory pattern, register directly with
``mcp.resource()``:

1. **Add a display formatter** (if needed) in `tools/display.py`.
2. **Write the resource function** in ``resources/custom.py``.
3. **Register** with a direct ``mcp.resource()`` call — no decorator needed.
   Add a scope guard inline when the resource requires a token scope.
4. **No skip-URI update needed** — factory resources are auto-tracked via
   the ``tracking_set`` parameter and skipped by the auto-generation loop.

### Pre-computed static resources

For resources whose data is static for the server session (server version,
token scopes, server info), pre-fetch the data at startup in
``create_mcp_server()`` and pass it as a parameter to ``register_custom_resources()``.
The handler becomes a simple closure over the cached value — no API calls on read.

```python
# In server.py: pre-fetch at startup (async context available)
version_str: str = "Unknown"
try:
    version_data = await gitea_client.request("GET", "/version")
    if isinstance(version_data, dict):
        version_str = str(version_data.get("version", "Unknown"))
except GiteaAPIError:
    pass

# Pass through the registration chain
register_all_resources(..., version_str=version_str, ...)
```

```python
# In custom.py: handler is a closure — no API call on read
# Direct mcp.resource() call — no decorator needed.
async def get_version() -> ResourceResult:
    """Get server application version."""
    return ResourceResult(contents=[
        ResourceContent(content=version_str, mime_type="text/plain"),
    ])

mcp.resource(
    "gitea://version", mime_type="text/plain",
    tags={"wrapper", "server"},
    meta=ResourceMeta(required_scope=None, size_hint="tiny", default_detail="full").to_dict(),
)(get_version)
```

See ``register_custom_resources()`` for the available pre-computed parameters
(``version_str``, ``server_info_md``, and ``available_scopes`` for token scopes).

---

## How to Add a Synthetic Tool (and Optional Resource)

Synthetic tools and resources are hand-written (not auto-generated from the
OpenAPI spec). They live in the same codebase and register themselves via
``register_all_synthetic_tools`` (declarative ``SyntheticToolSpec`` lists) /
``mcp.resource()``. Examples: ``resolve_type``,
``search_tools``, ``tool_info``, ``gitea://types/{typeName}{?detail}``.

### Pattern

1. **Create a module** in ``gitea_mcp_server/tools/`` (e.g. ``tools/type_info.py``).

2. **Core logic** goes in pure functions that accept typed inputs and return
   plain dicts/lists — easy to unit test without mocking FastMCP.

3. **Registration closure** is a ``register_*`` function that takes ``mcp: FastMCP``
   (and any deps like ``openapi_spec``) and calls
   ``register_all_synthetic_tools`` / ``mcp.resource()``:

   ```python
   def register_my_tool(
       mcp: FastMCP,
       openapi_spec: OpenAPISpec | None = None,
   ) -> None:
       # Build index / cache at registration time
       my_data = build_my_data(openapi_spec)

       async def _my_tool_impl(
           param: str,
           ctx: Context,
           format: str = "markdown",
       ) -> ToolResult:
           """Description for agents."""
           if not my_data:
               raise_value_error("Not available")
           await ctx.info(f"Processing '{param}'", ...)
           result = do_the_work(my_data, param)
           await ctx.report_progress(progress=1.0)
           return apply_format(result, format)

       register_all_synthetic_tools(mcp, [
           SyntheticToolSpec(
               impl=_my_tool_impl,
               name="my_tool",
               description="...",
               tags={"synthetic", "my-domain"},
               annotations=synthetic_annotations(read_only=True, open_world=False),
               output_schema={...},
           ),
       ])

       # Optional companion resource
       async def _my_resource(
           param: str,
           ctx: Context,
           detail: str = "full",
       ) -> str:
           """Description."""
           await ctx.info(...)
           info = do_the_work(my_data, param)
           return json.dumps(info, indent=2)

        mcp.resource(
            uri="gitea://my/{param}",
            mime_type="application/json",
            annotations={"readOnlyHint": True, "idempotentHint": True},
            meta=ResourceMeta(required_scope=scope, size_hint="medium").to_dict(),
            tags={"synthetic", "my-domain"},
        )(_my_resource)
   ```

   ``register_all_synthetic_tools`` processes the declarative spec list:
   each ``SyntheticToolSpec`` stamps the wrap-me marker (``_contract_wrap``)
   and registers its executor so the **server-level contract transform**
   wraps the tool exactly like an autogenerated one: virtual params
   (``format``/``detail``/``fetch_all`` — from the registry, not hand-declared)
   are injected into the schema, popped before the executor runs, and
   re-supplied to the impl by ``make_impl_executor``.  The executor validates
   ``page``/``limit`` for paginated tools and marks the result ``_formatted``
   so the shared format post-hook does not re-render the impl's own output.
   Set ``wrap=False`` on proxy tools (``call_tool``) that must pass arguments
   through untouched.  The transform wraps synthetic executors with the same
   ``run_validation`` surface as autogen tools (missing/unknown/enum checks);
   pagination is one rule (``validate_pagination``) with ``page_size_name`` /
   ``page_size_max`` adapting ``limit`` (synthetic) vs ``per_page`` (API).

   For factory-migrated resources, use ``make_api_resource()`` which auto-derives
   ``size_hint`` from the response schema via ``ResourceMeta.for_schema()``.

4. **Wire into ``server.py``** by importing and calling `register_*` in
   ``create_mcp_server()`` — see lines 330–332 for the canonical placement.

5. **Export ``__all__``** with all functions (public and ``_``-prefixed helpers).

### Key conventions

| Concern | Convention |
|---------|-----------|
| Function injection | FastMCP auto-injects ``ctx: Context`` via type annotation — declare it in the handler signature |
| Observability | Use ``ctx.info()`` before/after work and ``ctx.report_progress()`` for long ops — agents rely on this |
| Registration | Use ``register_all_synthetic_tools(mcp, [SyntheticToolSpec(...), ...])`` — one declarative spec per tool (impl, name/description/tags/annotations/output_schema, paginated, limit_max, virtual_params, required_scope, wrap). The loop builds the executor, stamps the wrap marker, and registers |
| Virtual params | Declare ``format``/``detail``/``fetch_all`` in the impl signature as usual; the registry supplies the agent-facing schema (descriptions/enums/defaults) via the tool's ``virtual_params`` allowlist (default ``{"format","detail","fetch_all"}`` for paginated tools, ``{"format","detail"}`` otherwise; pass a custom set e.g. ``read_doc`` → ``{"format"}``; ``sudo`` is opt-in). Only allowlisted params are popped from kwargs — an off-profile registry-name key stays in kwargs and is rejected with "Unknown parameter(s)" rather than silently dropped. The executor re-supplies the popped values to the impl and marks results ``_formatted`` |
| ``format`` param | Accept it with default ``"markdown"``, dispatch via ``apply_format()``. For paginated list results, prefer ``format_paginated_result()`` which handles slicing, ``fetch_all``, and pagination metadata in one call. For the empty-result / out-of-range early returns, use ``empty_paginated_result()`` (also in ``format.py``) so the pagination envelope is still emitted and schema/runtime never disagree. |
| ``detail`` param | Optional: ``"full"`` (default) or ``"concise"`` — controls data shaping: ``"concise"`` collapses nested ``$ref``-backed objects to ``$ref:TypeName`` labels at depth >= 1. Affects both ``json`` and ``markdown`` output. |
| Annotations | Use ``synthetic_annotations(read_only=True, open_world=False)`` for tools; annotate resources inline |
| ``meta`` / scope | Use ``ResourceMeta(required_scope=scope, ...).to_dict()`` or ``ResourceMeta.for_schema(schema, ...).to_dict()`` for typed, discoverable metadata including ``size_hint`` and ``default_detail``. |
| ``openapi_spec`` parameter | Pass as ``OpenAPISpec \| None`` — handle ``None`` with a helpful error message |
| URI templates / metadata | Agents discover resource metadata (``size_hint``, ``default_detail``, ``optional_params``) via ``list_resources`` output. For factory resources, set these via ``ResourceMeta.for_schema()`` (auto-derives ``size_hint``) or ``ResourceMeta(..., size_hint=..., optional_params=...).to_dict()``. For hand-written resources, include ``{?param}`` in the URI template for query params. The display layer (``clean_resource_uri``) strips ``{?...}`` from displayed URIs. When using ``make_api_resource()`` with ``param_config``, the factory auto-adds param names to the handler's ``__signature__``. |
| Import pattern | ``from fastmcp.server.context import Context`` (not ``from fastmcp import Context`` — triggers ruff TC002). Import ``OpenAPISpec`` at module level (no circular risk). **Never** use ``from __future__ import annotations`` in registration modules — FastMCP's pydantic introspection resolves type hints at registration time and will ``NameError`` on types under ``TYPE_CHECKING`` |
| Error handling | ``raise_value_error(msg)`` raises ``ValueError``; FastMCP catches it and re-raises as ``ToolError`` (tool calls) or ``ResourceError`` (resource reads). Unit test the ``ValueError``; integration test the ``ToolError`` / ``ResourceError`` |
| Test pattern | Unit test the core logic; integration test the registration wiring. ``mcp.call_tool()`` returns ``ToolResult`` — access data via ``get_structured(result)["result"]``. ``mcp.read_resource()`` returns ``ReadResourceResult`` — access text via ``extract_resource_text(result)``. Catch ``ToolError`` / ``ResourceError`` from FastMCP, not raw ``ValueError`` |

### When to choose a synthetic tool vs. customizing an auto-generated one

| Situation | Approach |
|-----------|----------|
| Wraps an existing API endpoint with formatting | Customize via ``_customize_metadata`` (see above) |
| Computes new data from the spec / index | Synthetic tool |
| Combines multiple API calls into one result | Synthetic tool |
| Exposes server metadata or configuration | Synthetic tool + resource |
| Adds a convenience alias for an existing endpoint | ``mcp_extensions.yaml`` or synthetic proxy |

---



## Shared Formatters (`format.py`)

General-purpose schema-aware formatting lives in `gitea_mcp_server/format.py`.
This module is shared by both `tools/` and `resources/` -- never import
formatting utilities from one domain into the other.

Add a utility formatter there if multiple consumers need it:

```python
# gitea_mcp_server/format.py
def _format_custom_type(data: dict) -> str:
    ...
```

Domain-specific resource formatters are registered in `tools/display.py` via the ``@register_formatter`` decorator. See "How to Add a Custom Resource" above.

---

## MCP Extensions (YAML)

The `mcp_extensions.yaml` file at project root lets you override tool titles,
descriptions, and parameter docs without touching Python code.

```yaml
tool_names:
  operation_id_name:
    title: "Human-Readable Title"
    description: |
      Detailed description of what this tool does.
      Supports multi-line.
    parameters:
      - name: param_name
        description: "Override parameter description"
```

Set `MCP_EXTENSIONS_PATH` env var to use a different file location.

---

## Tool/Resource Exclusion Config

The server supports excluding or including specific tools, resources, and
resource templates via a YAML config file.  This is useful for fine-grained
control beyond token-scope filtering — e.g., hiding destructive operations
or admin tools.

### Setup

Set the `EXCLUDE_CONFIG_PATH` env var to point to your YAML config:

```bash
EXCLUDE_CONFIG_PATH=/path/to/disable.yaml uv run python -m gitea_mcp_server
```

### Config format

```yaml
# disable.yaml
exclude:
  - "repo_delete"           # exact name match (operationId)
  - "admin_*"               # fnmatch glob on component name
  - "tag:admin"             # tag-based (all tools with 'admin' tag)
include:
  - "admin_get_server_version"   # override: re-allow within excluded group
```

### How it works

- Patterns match against both unprefixed (operationId) and prefixed
  (`gitea_`-prefixed) component names, so both forms work in patterns.
- `include` overrides `exclude`: if a component matches any include pattern,
  it passes through regardless of exclude matches.
- `include` without `exclude` is a no-op.
- Token scope filter runs **before** exclusion config: a tool filtered by
  scope cannot be re-added via include.
- The exclusion is applied as a **server-level transform**, covering tools,
  resources, and resource templates from all providers.

### Startup customization order

This is the *startup* axis: the sequence in which customization is wired into
the server before it serves requests. Tool/resource visibility filtering now
happens at spec-prep time via `route_map_fn` (see `docs/SCOPE_MODEL.md` and
`docs/ARCHITECTURE.md`), so it is no longer part of the query-time transform
chain (TolerantSearch → GiteaNamespace → ExtensionMetadata). The startup order:

1. Spec-prep filtering (`spec_loader.py`) — computes excluded routes (deprecated + scope + config-excluded) applied via `route_map_fn`
2. Runtime wrapping (`_ToolWrappingTransform`) — validation, labels, error handling, context logging, progress reporting

---

## Common Pitfalls

1. **Don't edit on `main`** -- Always create a feature branch first.
2. **Don't import from outside `__all__`** in production code.  Internal
   functions may be renamed/refactored without notice.
3. **Resource URIs conflict** — When adding a custom resource that shadows
   a GET endpoint, use ``make_api_resource()`` (factory auto-tracks URIs
   via the ``tracking_set`` parameter and ``register_custom_resources()``
   returns them).
   For static resources registered via direct ``mcp.resource()`` calls,
   add the URI to the ``skip_uris`` set that the orchestrator passes to
   ``register_auto_generated_resources()``.
4. **Tests that make HTTP calls** -- Use `respx` to mock the Gitea API.
   Integration tests need a real `.env` with credentials.
5. **Cache confusion** -- Resource reads are cached.  If your changes don't
   appear, check cache TTL or invalidate manually.
   6. **Schema changes** -- The `openapi_converter.py` transforms Swagger 2.0 → 3.1.
    If you add a new schema feature, ensure the converter preserves it.  Note:
    the converter *intentionally* strips all `x-*` vendor extensions from
    schema objects (Gitea leaks `x-go-name`/`x-go-package` Go internals) -- this
    is by design, not a bug.  The surgical scope of that strip (schema-level only,
    operation-level `x-*` preserved) and the rationale are in
    `docs/ARCHITECTURE.md` → "Vendor extension (`x-*`) stripping in the converter".

---

## OpenTelemetry Observability

FastMCP 3.x includes native OpenTelemetry instrumentation. The server emits
auto-generated spans for all tool calls, resource reads, and prompt renders
with no code changes.

### Span Hierarchy (auto-generated + custom)

```
tools/call gitea_issue_create_issue          (auto, by FastMCP)
├── gitea_issue_create_issue.validate        (custom, validation)
├── gitea_issue_create_issue.validate_labels  (custom, label conversion)
└── gitea_issue_create_issue.execute         (custom, HTTP execution)
```

### Quick Start (local trace visualization)

```bash
# Terminal 1: Start otel-desktop-viewer (UI at http://localhost:8000)
brew install nico-barbas/brew/otel-desktop-viewer
otel-desktop-viewer

# Terminal 2: Run server with tracing
opentelemetry-instrument \
  --service_name gitea-mcp-server \
  fastmcp run python -m gitea_mcp_server
```

### Production Configuration

```bash
# Install the OTLP exporter
uv add opentelemetry-exporter-otlp

# Run with tracing
opentelemetry-instrument \
  --service_name gitea-mcp-server \
  --exporter_otlp_endpoint http://localhost:4317 \
  fastmcp run python -m gitea_mcp_server
```

Or configure via environment variables:

```bash
export OTEL_SERVICE_NAME=gitea-mcp-server
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
opentelemetry-instrument fastmcp run python -m gitea_mcp_server
```

### Testing Telemetry

Tests use ``InMemorySpanExporter`` from ``opentelemetry-sdk``. See
``tests/unit/test_mcp_builder.py::TestToolWrappingTransformTelemetry``
for the fixture pattern.

### Key Reference

- [FastMCP Telemetry Docs](https://gofastmcp.com/servers/telemetry.md)
- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/languages/python/)

---

## FastMCP Reference

This project uses FastMCP 3.x.  Key APIs:

- `OpenAPIProvider(spec, client)` -- auto-generates tools from OpenAPI spec
- `ResponseCachingMiddleware` -- TTL-based resource caching
- `BM25SearchTransform` -- lazy loading with name-match + BM25 search
- `Transform` -- modify tool lists, intercept tool lookups
- `Tool.from_tool(existing, transform_fn=...)` -- wrap existing tools with new behavior
- `FastMCP(name=..., lifespan=lifespan)` -- async context manager for resource lifecycle (startup/teardown)
- `CurrentContext()` -- async context manager that resolves the current MCP ``Context`` inside a request scope
- `ctx.info()` / `ctx.warning()` / `ctx.error()` / `ctx.debug()` -- client-side structured logging
- `ctx.report_progress(progress=..., total=...)` -- send progress updates to the agent host (both floats)

For up-to-date FastMCP docs: https://gofastmcp.com/llms.txt
