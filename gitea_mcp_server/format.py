"""General-purpose schema-aware formatters for tools and resources.

Shared formatting utilities used across tools/ and resources/.
Kept at the flat level so neither domain depends on the other.

Public functions:
    build_server_info_markdown - build server info markdown from
        the OpenAPI spec info block (not a registered domain formatter).
    collapse_data - shape data for ``detail="concise"``: keep the payload
        (the root object / root-list items), replace ``$ref``-backed
        *relations* with the canonical agent-facing ``$ref`` marker
        (``{"$ref": "TypeName"}``; ``{"$ref": "TypeName", "count": N}`` for a
        collapsed list).  A payload ``$ref`` — the root object's schema, or a
        root list's item schema — is resolved one level via the OpenAPI spec
        so the payload is summarized (#759); an unresolvable payload type
        leaves the data unchanged — never a content-free marker (#763).  Used
        to shape data before formatting so any formatter (json or markdown)
        receives already-collapsed data.
    decode_base64_content - decode base64 file content from a Gitea
    ContentsResponse (shared by tools and resources).
    format_tool_info_markdown - format a ToolSchemaResult as parseable markdown.
    _format_parameter_table - render a JSON Schema parameter table.
    _format_annotations_table - render an annotations table.
    _format_json_section - render a JSON code block section.

Formatter contract and registry:
    MarkdownFormatter - the display pipeline's markdown formatter type.  This
        module is its canonical home; other modules point here rather than
        restating the signature.  ``call_markdown_formatter`` is the single
        dispatch point.
    register_formatter / get_formatter / get_formatter_for_type - the formatter
        registry.  Bespoke formatters live in ``tools/display.py`` and register
        here; the result pipeline resolves through :func:`resolve_formatter`
        without importing the domain module (Result → Format, never Result →
        Display).
    resolve_formatter - the three-tier formatter choice (explicit per-result →
        bespoke type-bound formatter → generic schema-anchored view), the
        single dispatch policy shared by every tool and resource.
    _generic_collection_view - the generic, schema-anchored markdown view
        (#771): the bound type's properties in declaration order, scalars as
        table rows, ``$ref``-backed relations compacted to an identity.  The
        only curated knowledge is the converter's deficiency tables
        (``openapi_converter/display_hints.py``), resolved once at
        registration and passed in as a ``ViewHints`` value (#775) — the
        format layer never reads hints off the spec.

The single result pipeline for tools and resources lives in
``tools/result_pipeline.py``; this module provides the shared formatting
primitives it builds on.

Output contract:
    The canonical statement of what each channel carries lives in the
    virtual-param registry's module docstring
    (:mod:`gitea_mcp_server.tools.virtual_params`) — ``json`` / ``raw``
    are the complete machine contract, ``markdown`` is a schema-derived
    reading view.  This module is the renderer; it does not restate the
    contract.  See :func:`_generic_collection_view` for the view and
    :func:`~gitea_mcp_server.openapi_converter.display_hints.view_hints_for`
    for the curated omissions.
"""

from __future__ import annotations

import base64
import functools
import inspect
import json as json_module
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.constants import DEFAULT_DETAIL
from gitea_mcp_server.marker import is_ref_marker, ref_marker, ref_marker_label
from gitea_mcp_server.ref_resolver import resolve_ref_chain
from gitea_mcp_server.schema_utils import (
    extract_type_name,
    extract_type_ref,
    get_schema_type,
)

if TYPE_CHECKING:
    from gitea_mcp_server.models import ToolSchemaResult, ViewHints
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)

# Length bounds for auto-detecting ISO datetime strings without schema hint

# ---------------------------------------------------------------------------
# Shared formatter dispatch
# ---------------------------------------------------------------------------

# The display pipeline's markdown formatter contract — the canonical home for
# this statement; other modules point here rather than restating it.
#
# A formatter is a *pure renderer*: it takes already-shaped data and returns
# markdown.  It may declare a keyword-only ``extra`` parameter (with a
# default) for formatter context; ``call_markdown_formatter`` inspects each
# signature and forwards ``extra`` only to formatters that declare it.  No
# other keyword-only parameter is dispatched.
#
# The type is deliberately ``Callable[..., str]`` — not a tighter
# ``Callable[[Any], str]`` or a ``Protocol``: formatters have heterogeneous
# signatures (``data`` positional, optional keyword-only ``extra``), and
# Python's type system cannot express "may declare this optional keyword"
# without rejecting the common ``def f(data)`` form.  The shape of the
# contract is therefore enforced at runtime by ``call_markdown_formatter``
# and locked by tests, not by the type itself.
#
# ``detail`` is deliberately NOT part of the contract: when ``detail="concise"``
# and a schema is available the pipeline pre-collapses the page, so formatters
# receive already-collapsed data.  Collapsed *fields* arrive as the canonical
# ``$ref`` marker (``{"$ref": "TypeName"}``, or with ``count`` for a collapsed
# list) and render through ``is_ref_marker``/``ref_marker_label``.  Root-list
# items are summarized, not label-replaced (dicts with scalars intact and
# nested refs marked, #759), so formatters generally do not branch on
# item-level shapes.  The one exception is a root list whose items are
# themselves collapsed list relations (a list of lists): those arrive as count
# markers and ``_format_list_as_markdown`` renders them as compact labels.
MarkdownFormatter = Callable[..., str]


def _accepted_kwargs(fn: MarkdownFormatter) -> frozenset[str]:
    """Keyword-only params a formatter callable accepts.

    Formatters are pure renderers with heterogeneous signatures: most take
    only ``data``, some take ``extra`` (formatter context) — the contract is
    stated canonically on :data:`MarkdownFormatter`.

    The signature is inspected per call — ``inspect.signature`` is cheap
    (microseconds) next to the HTTP call and the formatting walk, and the
    callables handed to the pipeline are often fresh per call (lambdas,
    ``functools.partial``), so a cache keyed on the callable would never
    hit and would grow without bound on a long-lived server.
    """
    return frozenset(
        name
        for name, param in inspect.signature(fn).parameters.items()
        if param.kind == inspect.Parameter.KEYWORD_ONLY
    )


def call_markdown_formatter(
    fn: MarkdownFormatter,
    data: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """Call a markdown formatter with only the kwargs it accepts.

    The single dispatch point for the display pipeline's ``markdown_formatter``
    contract.  Formatters declare only the keyword params they use — most take
    just ``data``, some take ``extra`` (formatter context) — and this helper
    inspects each signature to pass exactly the accepted kwargs.
    This keeps the pipeline's call site uniform while letting formatters drop
    dead params.  The contract itself — including why ``detail`` is not
    dispatched (the pipeline pre-collapses and formatters detect collapsed
    items by shape) — is stated canonically on :data:`MarkdownFormatter`.

    Only ``extra`` is dispatched among keyword-only params, so formatters
    should declare no other keyword-only param (and ``extra`` with a
    default) — a required keyword-only param other than ``extra`` would
    raise ``TypeError``.

    Args:
        fn: A :data:`MarkdownFormatter` — ``(data, *, extra?) -> str``.
        data: The (already-collapsed) data to render.
        extra: Formatter context; passed only if ``fn`` declares it.

    Returns:
        The rendered markdown string.
    """
    kwargs: dict[str, Any] = {}
    accepted = _accepted_kwargs(fn)
    if "extra" in accepted:
        kwargs["extra"] = extra
    return fn(data, **kwargs)


# ---------------------------------------------------------------------------
# Formatter registry and resolution
# ---------------------------------------------------------------------------
#
# The registry lives in the format layer (not in ``tools/display.py``) so the
# result pipeline can resolve a formatter without importing the domain
# formatter module: Result → Format → Display.  Domain formatters
# (``tools/display.py``) are pure plugins — they register here and hold no
# registry state.

_FORMATTERS: dict[str, MarkdownFormatter] = {}

# Response-schema type name -> formatter name.  Populated
# by ``register_formatter(types=...)``; consulted by :func:`resolve_formatter`
# as the middle tier between an explicit per-result formatter and the generic
# fallback.
_TYPE_FORMATTERS: dict[str, str] = {}


def register_formatter(
    name: str,
    *,
    types: Sequence[str] = (),
) -> Callable[[MarkdownFormatter], MarkdownFormatter]:
    """Decorator that registers a domain-specific markdown formatter.

    Domain formatters live in ``tools/display.py``; the registry itself lives
    here so the result pipeline depends on the format layer only.

    Args:
        name: Unique name used as ``format_hint`` in resource metadata.
        types: Response type names (e.g. ``"Issue"``) this formatter renders
            on the tool side.  The result pipeline binds a tool's markdown
            output to this formatter when the result's ``response_type``
            matches one of them.

    Usage::

        @register_formatter("repository", types=["Repository"])
        def _format_repo_markdown(data): ...
    """

    def deco(fn: MarkdownFormatter) -> MarkdownFormatter:
        _FORMATTERS[name] = fn
        for type_name in types:
            existing = _TYPE_FORMATTERS.get(type_name)
            if existing is not None and existing != name:
                logger.warning(
                    "Response type %r already bound to formatter %r; rebinding to %r",
                    type_name,
                    existing,
                    name,
                )
            _TYPE_FORMATTERS[type_name] = name
        return fn

    return deco


def get_formatter(name: str) -> MarkdownFormatter | None:
    """Look up a registered formatter by name.  Returns ``None`` if not found."""
    return _FORMATTERS.get(name)


def get_formatter_for_type(type_name: str) -> MarkdownFormatter | None:
    """Look up the formatter bound to a response type name (``types=``).

    Returns ``None`` for unbound types — callers fall back to the generic
    renderer (the pipeline's third tier, see :func:`resolve_formatter`).
    """
    formatter_name = _TYPE_FORMATTERS.get(type_name)
    if formatter_name is None:
        return None
    return _FORMATTERS.get(formatter_name)


def _type_bound_formatter(response_type: str | None) -> MarkdownFormatter | None:
    """Return the domain formatter bound to a response type name.

    The binding key is the response schema's root type, carried as
    first-class metadata rather than read back out of the schema:

    - the converter stamps the operation-level ``x-response-type`` *before*
      response-schema wrapping inlines the root ``$ref`` (which would erase
      it) — see ``openapi_converter/type_references.py``;
    - the tool/resource registration layers propagate it into
      ``tool.meta["response_type"]`` / resource content meta;
    - the contract spine and the ``read_resource`` executor put it on
      ``ExecutionResult.response_type``.

    It covers array and object responses uniformly (the stamp is the element
    or root type).  Returns ``None`` — and the caller falls back to the
    generic renderer — when there is no type or no formatter registered for
    the type, so unknown types keep today's behavior exactly.
    """
    if not response_type:
        return None
    return get_formatter_for_type(response_type)


# ---------------------------------------------------------------------------
# Generic schema-anchored collection view
# ---------------------------------------------------------------------------
#
# The collection view is derived from the response schema, not from a
# hand-written per-type field list (#771).  The bound type's properties, in
# declaration order, become the view; scalars render as table rows and
# ``$ref``-backed relations compact to an identity.  The only curated
# knowledge is the converter's deficiency tables, resolved at registration
# and handed in as a ``ViewHints`` value (#775) — so a new or renamed schema
# field appears automatically and a stale hint fails loudly at startup
# (``openapi_converter/display_hints.py``).

#: Identity fields tried in order when compacting a ``$ref``-backed object.
_IDENTITY_KEYS: tuple[str, ...] = ("login", "username", "name", "full_name", "id")


def _identity(value: Any, key: str | None = None) -> str:
    """Render a ``$ref``-backed object as its identity.

    *key* is the converter-curated identity field for this relation (e.g.
    ``base`` → ``ref``); ``None`` uses the generic policy (``login`` →
    ``username`` → ``name`` → ``full_name`` → ``id``).  A collapsed relation
    marker renders as its label.  A dict with no usable identity field
    renders as a compact ``key=value`` summary — never a Python repr, which
    would leak quotes and braces into agent-facing markdown.
    """
    if is_ref_marker(value):
        return ref_marker_label(value)
    if isinstance(value, dict):
        keys = (key,) if key else _IDENTITY_KEYS
        for candidate_key in keys:
            candidate = value.get(candidate_key)
            if candidate is not None and not isinstance(candidate, (dict, list)):
                return str(candidate)
        # No identity field: render a compact, quote-free summary of the
        # scalar fields rather than ``str(dict)``.
        scalars = [
            f"{k}={v}"
            for k, v in value.items()
            if v is not None and not isinstance(v, (dict, list))
        ]
        return ", ".join(scalars) if scalars else "—"
    return str(value)


def _identity_list(value: list[Any], key: str | None = None) -> str:
    """Render a list of ``$ref``-backed objects as comma-joined identities."""
    return ", ".join(_identity(item, key) for item in value)


def _ref_target(schema: Any, openapi_spec: OpenAPISpec | None) -> str | None:
    """Return the type name a property schema references, or ``None``.

    Handles a bare ``$ref``, a combinator (``anyOf``/``oneOf``/``allOf``)
    wrapping a ``$ref``, and an array whose ``items`` is a ``$ref``.  The
    referenced type must resolve to an **object** schema — a combinator
    wrapping a scalar alias (e.g. ``Issue.state`` → ``StateType``, a string)
    is not a relation.
    """
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return _object_type_name(ref, openapi_spec)
    for key in ("anyOf", "oneOf", "allOf"):
        members = schema.get(key)
        if isinstance(members, list):
            for member in members:
                if isinstance(member, dict) and isinstance(member.get("$ref"), str):
                    name = _object_type_name(member["$ref"], openapi_spec)
                    if name:
                        return name
    if schema.get("type") == "array" or (
        isinstance(schema.get("type"), list) and "array" in schema["type"]
    ):
        return _ref_target(schema.get("items"), openapi_spec)
    return None


def _object_type_name(ref: str, openapi_spec: OpenAPISpec | None) -> str | None:
    """Return the type name for *ref* if it resolves to an object schema.

    An unresolvable ``$ref`` (no spec, or a type absent from the spec) still
    returns the ref's name — it is treated as a relation and compacts to a
    ``key=value`` summary rather than expanding.  That is the safe default:
    a missing schema should not dump a nested object into a list view.
    """
    name = ref.rsplit("/", 1)[-1]
    if openapi_spec is None:
        return name
    schema = _type_schema(openapi_spec, name)
    if schema is None:
        return name
    schema_type = schema.get("type")
    if schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type):
        return name
    return None


def _type_schema(
    openapi_spec: OpenAPISpec | None, response_type: str | None
) -> dict[str, Any] | None:
    """Return the named component schema for a response type, or ``None``."""
    if openapi_spec is None or not response_type:
        return None
    components = openapi_spec.get("components", {})
    schemas = components.get("schemas", {})
    if not isinstance(schemas, dict):
        return None
    schema = schemas.get(response_type)
    return schema if isinstance(schema, dict) else None


def _build_field_filter(
    properties: dict[str, Any],
    view_hints: ViewHints | None,
    openapi_spec: OpenAPISpec | None,
) -> dict[str, dict]:
    """Build the collection view's field filter from the schema.

    A property is a **relation** when its schema references an object type
    (``$ref``, a combinator wrapping one, or an array of one) — derived from
    the schema, so a new or unknown type compacts its relations for free.
    The converter-curated ``view_hints`` (resolved at registration, #775)
    refine that:

    - ``compact`` supplies identity-field *overrides* for relations whose
      object has no conventional identity (``base`` → ``ref``, ``milestone``
      → ``title``).
    - ``flag`` marks a relation that is a boolean flag, not an identity
      (``pull_request``) — rendered ``Yes``/``No``.
    - ``omit`` drops noise fields.
    """
    omit: Sequence[str] = view_hints.get("omit", ()) if view_hints else ()
    compact: dict[str, str | None] = view_hints.get("compact", {}) if view_hints else {}
    flag: Sequence[str] = view_hints.get("flag", ()) if view_hints else ()
    field_filter: dict[str, dict] = {}
    for prop_name, prop_schema in properties.items():
        if prop_name in omit:
            continue
        if prop_name in flag:
            field_filter[prop_name] = {"render": "badge"}
        elif _ref_target(prop_schema, openapi_spec) is not None:
            field_filter[prop_name] = {
                "render": "compact_identity",
                "identity_key": compact.get(prop_name),
            }
        else:
            field_filter[prop_name] = {}
    return field_filter


def _generic_collection_view(
    data: Any,
    *,
    response_type: str | None,
    openapi_spec: OpenAPISpec | None,
    view_hints: ViewHints | None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Render API objects as a schema-anchored view.

    A **list** renders the collection view: the bound type's schema
    properties in declaration order, minus the converter-curated omissions,
    with ``$ref``-backed relations compacted to an identity.  A **dict**
    (a single-resource read) renders the full payload dynamically — a detail
    read must never drop a field.  Falls back to the generic renderer when
    the type schema is unavailable, so an unknown type keeps today's
    behavior.

    *view_hints* is the registration-resolved :class:`ViewHints` for
    *response_type*, or ``None`` for a type with no curated deficiency.  The
    parameter is keyword-only and required so each direct call site makes an
    explicit decision; the result pipeline passes ``None`` when an entity
    registered no hints, which renders the plain schema-derived view.

    *extra* carries the call context (``type`` for the issue/pull title);
    the formatter declares it so ``call_markdown_formatter`` forwards it.
    """
    schema = _type_schema(openapi_spec, response_type)
    if schema is None:
        return format_as_markdown(data, title=_fallback_title(data, response_type))

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return format_as_markdown(data, title=_fallback_title(data, response_type))

    if isinstance(data, dict):
        # Detail view: the full payload, every field present in the data.
        return format_as_markdown(data, title=_detail_title(data, response_type, extra))

    field_filter = _build_field_filter(properties, view_hints, openapi_spec)

    title = _collection_title(response_type, len(data), extra, data)
    return format_as_markdown(
        data,
        title=title,
        field_filter=field_filter,
        item_title_key=_item_title_key(properties, response_type),
    )


def _detail_title(data: Any, response_type: str | None, extra: dict[str, Any] | None = None) -> str:
    """Heading for a single-object read (``Repository``, ``Issue #1``).

    An Issue/PullRequest detail read gets an ``Issue #N: title`` heading; the
    ``type`` filter (``pulls``) or a set ``pull_request`` field selects the
    PR label.  Other types use their type-appropriate identity field.
    """
    if isinstance(data, dict):
        if response_type in ("Issue", "PullRequest"):
            is_pr = response_type == "PullRequest" or bool(data.get("pull_request"))
            is_pr = is_pr or (extra or {}).get("type") == "pulls"
            label = "Pull Request" if is_pr else "Issue"
            number = data.get("number", "?")
            title = data.get("title")
            return f"{label} #{number}: {title}" if title else f"{label} #{number}"
        for key in _title_keys(response_type):
            value = data.get(key)
            if value:
                if response_type == "Release":
                    return f"Release {value}"
                return str(value)
    return response_type or "Result"


def _title_keys(response_type: str | None) -> tuple[str, ...]:
    """Identity-field candidates for a type's heading, most specific first.

    Type-aware so an empty ``full_name`` falls through to ``login`` (User) or
    ``username`` (Organization), and a Release prefers ``tag_name`` over an
    empty ``name``.
    """
    if response_type == "User":
        return ("login", "full_name", "username", "name")
    if response_type == "Organization":
        return ("username", "name", "full_name")
    if response_type == "Release":
        return ("tag_name", "name")
    return ("full_name", "title", "name", "username", "login", "tag_name")


def _fallback_title(data: Any, response_type: str | None) -> str:
    """Heading when the type schema is unavailable: collection or detail."""
    if isinstance(data, list):
        return _collection_title(response_type, len(data), data=data)
    return _detail_title(data, response_type)


#: Explicit plural labels for types a naive pluraliser gets wrong.
_PLURAL_LABELS: dict[str, str] = {
    "PullRequest": "Pull Requests",
    "Repository": "Repositories",
}


def _collection_title(
    response_type: str | None,
    count: int,
    extra: dict[str, Any] | None = None,
    data: list[Any] | None = None,
) -> str:
    """Pluralised heading for a collection view (``Issues - 3 items``).

    The issue-list ``type`` filter (``issues`` / ``pulls``) overrides the
    type-derived label.  With no filter, an issue list that contains pull
    requests is labelled ``Issues and Pull Requests`` — Gitea's ``/issues``
    endpoint returns PRs too.
    """
    type_filter = (extra or {}).get("type")
    if response_type == "Issue":
        if type_filter == "pulls":
            label = "Pull Requests"
        elif type_filter == "issues":
            label = "Issues"
        elif data and any(isinstance(item, dict) and item.get("pull_request") for item in data):
            label = "Issues and Pull Requests"
        else:
            label = "Issues"
    else:
        label = _pluralize(response_type) if response_type else "Results"
    return f"{label} - {count} items" if count else label


def _pluralize(type_name: str) -> str:
    """Pluralise a type name (``Issue`` -> ``Issues``, ``PullRequest`` -> ``Pull Requests``)."""
    if type_name in _PLURAL_LABELS:
        return _PLURAL_LABELS[type_name]
    if type_name.endswith(("s", "x", "z", "ch", "sh")):
        return f"{type_name}es"
    if type_name.endswith("y") and len(type_name) > 1 and type_name[-2] not in "aeiou":
        return f"{type_name[:-1]}ies"
    return f"{type_name}s"


def _item_title_key(properties: dict[str, Any], response_type: str | None = None) -> str | None:
    """Pick the per-item title field from the schema's properties.

    Type-aware so a repository list titles each item ``owner/repo``
    (``full_name``) and a release list uses ``tag_name``.
    """
    for key in _title_keys(response_type):
        if key in properties:
            return key
    return None


def resolve_formatter(
    schema: dict[str, Any] | None,
    explicit: MarkdownFormatter | None = None,
    response_type: str | None = None,
    view_hints: ViewHints | None = None,
    *,
    openapi_spec: OpenAPISpec | None = None,
) -> MarkdownFormatter:
    """Return the formatter for a result: explicit, type-bound, or generic.

    Three tiers, consulted in order:

    1. ``explicit`` — an explicit per-result formatter (the resource
       surface's ``format_hint`` resolution).
    2. The type-bound domain formatter for ``response_type``
       (:func:`_type_bound_formatter`) — a *bespoke* formatter registered
       for a type (e.g. ``labels``).  When none is registered, the generic
       schema-anchored collection view is used for list results
       (:func:`_generic_collection_view`), so a tool and its resource
       sibling render the same view without a hand-written per-type list.
    3. :func:`format_as_markdown`, with ``schema`` bound up front because
       ``call_markdown_formatter`` dispatches only ``extra`` (never
       ``schema`` or ``detail``).

    ``view_hints`` is threaded into the generic collection view; it is the
    registration-resolved :class:`~gitea_mcp_server.models.ViewHints` for
    *response_type* (#775).

    Centralising the choice here keeps the pipeline's ``_format`` a single,
    uniform formatter call site.  The function lives in the format layer so
    the pipeline never imports the domain formatter module.
    """
    if explicit is not None:
        return explicit
    bound = _type_bound_formatter(response_type)
    if bound is not None:
        return bound
    if response_type and openapi_spec is not None:
        return functools.partial(
            _generic_collection_view,
            response_type=response_type,
            openapi_spec=openapi_spec,
            view_hints=view_hints,
        )
    return functools.partial(format_as_markdown, schema=schema)


# ---------------------------------------------------------------------------
# Shared content transform: base64 decode
# ---------------------------------------------------------------------------


async def decode_base64_content(response: Any) -> str:
    """Decode base64 file content from a Gitea ContentsResponse.

    Gitea's ``/repos/{owner}/{repo}/contents/{path}`` endpoint returns a JSON
    object with ``content`` (base64-encoded) and ``encoding`` ("base64") fields.
    This function extracts and decodes the content for text output.

    Shared by tools (response post-processing in ``_pipeline_with_context``)
    and the ``read_resource`` tool (content detection in
    ``_read_resource_tool``).  When the caller detects a base64-encoded
    ``ContentsResponse`` (via the OpenAPI spec's ``x-response-transform``
    annotation for tools, or runtime JSON parse for resources), it calls this
    to produce plain text.

    Handles four response shapes:
    - ``str``: returned as-is (e.g., error messages from the API)
    - ``dict`` with ``encoding="base64"``: ``content`` is base64-decoded
    - ``dict`` without base64 encoding: ``content`` field returned as-is
    - Any other type: converted to ``str()``

    Args:
        response: Raw API response (str, dict, or other).

    Returns:
        Decoded text content.
    """
    if isinstance(response, str):
        return response
    if isinstance(response, dict) and response.get("encoding") == "base64":
        return base64.b64decode(response.get("content") or "").decode("utf-8")
    if isinstance(response, dict):
        return cast("str", response.get("content", ""))
    return str(response)


# Length bounds for auto-detecting ISO datetime strings without schema hint
_ISO_DT_MIN_LEN = 20
_ISO_DT_MAX_LEN = 30


def _snake_to_title(name: str) -> str:
    """Convert snake_case or CamelCase to Title Case with spaces."""
    result = ""
    for i, ch in enumerate(name):
        if ch == "_":
            result += " "
        elif ch.isupper() and i > 0 and name[i - 1].islower():
            result += " " + ch
        elif ch.isupper() and i > 0 and name[i - 1] == " ":
            result += ch.lower()
        else:
            result += ch
    return result.strip().title()


def _format_datetime(dt: str | None) -> str:
    """Format datetime string to human-readable format."""
    if not dt:
        return "N/A"
    try:
        parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return dt


def _format_scalar(value: Any, schema: dict[str, Any] | None = None) -> str:
    """Format a scalar value as a string, respecting schema format hints."""
    if value is None:
        return "N/A"
    if not isinstance(value, str):
        return str(value)
    fmt = schema.get("format") if schema else None
    if fmt == "date-time":
        return _format_datetime(value)
    # Auto-format ISO datetime strings even without schema hint
    if _ISO_DT_MIN_LEN <= len(value) <= _ISO_DT_MAX_LEN and "T" in value:
        formatted = _format_datetime(value)
        if formatted != value:
            return formatted
    return value


def _format_simple_value(value: Any) -> str:
    """Format any value as a string (lists, dicts, scalars) without schema."""
    if value is None:
        return "N/A"
    if isinstance(value, list):
        parts = [_format_simple_value(v) for v in value]
        return ", ".join(parts)
    if isinstance(value, dict):
        return json_module.dumps(value, indent=2)
    return str(value)


def _extract_ref(schema: dict[str, Any] | None) -> str | None:
    """Extract a ``$ref`` pointer string from a schema dict.

    Thin delegation to :func:`~gitea_mcp_server.schema_utils.extract_type_ref`
    — the single shared root-ref notion used by the converter's pre-wrap
    response-type stamp, the display layer's collapse walker, and the
    type-bound formatter dispatch, so they can never disagree about whether a
    schema *is* a reference.

    Args:
        schema: A JSON Schema fragment (may be ``None``).

    Returns:
        The ``$ref`` pointer string or ``None``.
    """
    return extract_type_ref(schema)


def _extract_type_name(schema: dict[str, Any] | None) -> str | None:
    """Extract a type name from a schema dict via ``$ref``.

    Thin delegation to
    :func:`~gitea_mcp_server.schema_utils.extract_type_name` — returns the
    last path segment (the type name) or ``None`` if no ``$ref`` is found.

    Args:
        schema: A JSON Schema fragment (may be ``None``).

    Returns:
        The type name (e.g. ``"Repository"``) or ``None``.
    """
    return extract_type_name(schema)


# Alias-chasing cap and the shared resolver live in ``ref_resolver.py``; the
# collapse and the compact example generator both resolve a payload ``$ref``
# through :func:`~gitea_mcp_server.ref_resolver.resolve_ref_chain`.


def collapse_data(
    data: Any,
    schema: dict[str, Any] | None = None,
    detail: str = DEFAULT_DETAIL,
    *,
    openapi_spec: OpenAPISpec | None = None,
) -> Any:
    """Shape *data* for ``detail="concise"``: keep the payload, mark relations.

    The **payload** — the root object, or the items of a root list — is never
    replaced by a marker (#759).  A root list whose item schema is a ``$ref``
    is resolved one level (via *openapi_spec*) so each item is *summarized*:
    scalar fields survive, nested ``$ref``-backed relations become markers.
    When that item type cannot be resolved, the list is returned unchanged —
    a content-free marker is never substituted for a payload item (#763).

    Below the payload, a **relation** — a ``$ref``-backed value — is replaced
    by the canonical agent-facing marker: ``{"$ref": "TypeName"}`` for an
    object, ``{"$ref": "TypeName", "count": N}`` for a list.  Inline schemas
    (no ``$ref``) are walked, not collapsed.

    ``detail="full"`` or ``schema is None`` returns *data* unchanged.  Nothing
    is ever mutated or truncated.

    Args:
        data: The data to shape (dict, list, or scalar).
        schema: The JSON Schema describing *data*, or ``None``.
        detail: ``"full"`` (return unchanged) or ``"concise"``.
        openapi_spec: Post-conversion OpenAPI 3.1 spec, used to resolve the
            payload's ``$ref`` one level.  ``None`` leaves the payload
            uncollapsed.

    Returns:
        Shaped data (dicts, lists, markers) suitable for JSON serialization or
        markdown rendering.
    """
    if detail == "full" or schema is None:
        return data
    return _summarize_payload(data, schema, openapi_spec)


def _summarize_payload(
    data: Any,
    schema: dict[str, Any],
    openapi_spec: OpenAPISpec | None,
) -> Any:
    """The requested payload: never replaced by a marker (#759).

    A root object is walked as relations; a root list is summarized item by
    item after resolving the payload schema and the item ``$ref`` one level
    each.  An unresolvable payload ``$ref`` leaves the data unchanged rather
    than emitting a marker.
    """
    if isinstance(data, list):
        # Resolve the payload schema itself first: a root ``$ref`` may point
        # at a named array type, whose ``items`` live on the resolved schema,
        # not on the ``$ref`` wrapper.  This keeps the collapse in step with
        # ``schema_to_compact_example``, which resolves the same root ref.
        concrete = _concrete_schema(schema, openapi_spec)
        if concrete is None:
            return data  # payload type unresolved → leave the payload intact
        items_schema = concrete.get("items", {}) if isinstance(concrete, dict) else {}
        items_concrete = _concrete_schema(items_schema, openapi_spec)
        if items_concrete is None:
            return data  # item type unresolved → leave the payload intact
        return [_collapse_relations(item, items_concrete) for item in data]

    if isinstance(data, dict):
        concrete = _concrete_schema(schema, openapi_spec)
        if concrete is None:
            return data  # payload type unresolved → leave the payload intact
        return _collapse_relations(data, concrete)

    return data


def _concrete_schema(
    schema: dict[str, Any],
    openapi_spec: OpenAPISpec | None,
) -> dict[str, Any] | None:
    """Return an inline schema as-is, or the resolved target of its root ``$ref``.

    ``None`` means the schema is (or wraps) a ``$ref`` that cannot be resolved
    against *openapi_spec* — the caller then leaves its payload uncollapsed.
    """
    if _extract_ref(schema) is None:
        return schema
    return resolve_ref_chain(schema, openapi_spec)


def _collapse_relations(value: Any, schema: dict[str, Any] | None) -> Any:
    """Replace a relation with a marker; walk inline schemas.

    Below the payload, a value whose schema declares a ``$ref`` is a relation:
    it is represented by the canonical marker instead of its content.  An
    inline schema (no ``$ref``) is walked so deeper relations still collapse.
    """
    if isinstance(value, dict):
        type_name = _extract_type_name(schema)
        if type_name:
            return ref_marker(type_name)
        combined = _merge_allof_schema(schema)
        properties = combined.get("properties", {}) if isinstance(combined, dict) else {}

        result: dict[str, Any] = {}
        for k, v in value.items():
            prop_schema = properties.get(k) if properties else None
            if prop_schema is not None and not isinstance(prop_schema, dict):
                prop_schema = None
            effective = _resolve_anyof_schema(prop_schema) if prop_schema else None
            result[k] = _collapse_relations(v, effective or prop_schema)
        return result

    if isinstance(value, list):
        items_schema = schema.get("items", {}) if isinstance(schema, dict) else {}
        type_name = _extract_type_name(items_schema)
        if type_name:
            return ref_marker(type_name, len(value))
        return [_collapse_relations(item, items_schema) for item in value]

    return value


def _format_list_as_markdown(
    data: list[Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    field_filter: dict[str, dict] | None = None,
    item_title_key: str | None = None,
) -> str:
    lines: list[str] = []
    item_schema = schema.get("items") if isinstance(schema, dict) else None
    if not data:
        lines.append(f"{indent}_(empty)_")
    # A list whose items are all markers is the list-of-lists relation case: a
    # root list whose items are themselves collapsed list relations (each a
    # count marker).  Render each as its compact label, not a nested section.
    elif data and all(is_ref_marker(v) for v in data):
        for v in data:
            lines.append(f"{indent}- {ref_marker_label(v)}")
    elif data and isinstance(data[0], dict):
        for i, item in enumerate(data):
            title: str | None = None
            if item_title_key:
                val = item.get(item_title_key)
                if val is not None:
                    title = str(val)
            if title is None:
                title = f"Item {i + 1}"
            sub = format_as_markdown(
                item,
                item_schema,
                title=title,
                _depth=0,
                field_filter=field_filter,
            )
            lines.append(sub)
    elif item_schema and get_schema_type(item_schema) in ("string", "number", "integer", "boolean"):
        items = [_format_scalar(v, item_schema) for v in data]
        lines.append(f"{indent}{', '.join(items)}")
    else:
        for item in data:
            sub = _format_simple_value(item)
            lines.append(f"{indent}- {sub}")
    return "\n".join(lines)


def _merge_allof_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not schema or "allOf" not in schema:
        return schema
    merged: dict[str, Any] = {"properties": {}}
    for sub_schema in schema["allOf"]:
        if isinstance(sub_schema, dict):
            sub_props = sub_schema.get("properties", {})
            if isinstance(sub_props, dict):
                merged["properties"].update(sub_props)
    return merged


def _resolve_anyof_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve anyOf/oneOf to the first object variant with properties."""
    if not schema:
        return None
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for sub in variants:
                if isinstance(sub, dict) and sub.get("properties"):
                    t = sub.get("type")
                    if isinstance(t, str) and t == "object":
                        return sub
                    if isinstance(t, list) and "object" in t:
                        return sub
    return schema


def _render_flat_table(lines: list[str], flat: list[tuple[str, str]], indent: str) -> None:
    lines.append(f"{indent}| Property | Value |")
    lines.append(f"{indent}|----------|-------|")
    for label, val in flat:
        escaped = val.replace("|", "\\|")
        lines.append(f"{indent}| {label} | {escaped} |")
    lines.append("")


def _render_nested_sections(
    lines: list[str], nested: list[tuple[str, str]], indent: str, _depth: int
) -> None:
    for label, sub in nested:
        if _depth == 0:
            lines.append(f"## {label}")
        else:
            lines.append(f"{indent}**{label}:**")
        lines.append("")
        lines.append(sub)
        lines.append("")


def _render_list_as_compact_ref(raw_val: list, template: str) -> str:
    """Render a list of dicts as comma-separated template expansions.

    Each dict item is expanded through *template* via ``str.format(**item)``.
    Non-dict items and template errors fall back to ``str(item)``.
    """
    items: list[str] = []
    for item in raw_val:
        if isinstance(item, dict):
            try:
                items.append(template.format(**item))
            except (KeyError, ValueError, TypeError):
                items.append(str(item))
        else:
            items.append(str(item))
    return ", ".join(items)


def _format_dict_as_markdown(  # noqa: PLR0912, PLR0915 - justified: scalar/nested, field_filter, allOf, anyOf, render hints
    data: dict[str, Any],
    schema: dict[str, Any] | None = None,
    indent: str = "",
    _depth: int = 0,
    field_filter: dict[str, dict] | None = None,
) -> str:
    lines: list[str] = []
    combined_schema = _merge_allof_schema(schema)
    properties = (
        combined_schema.get("properties", {})
        if combined_schema and isinstance(combined_schema, dict)
        else {}
    )

    # Determine which keys to iterate
    if field_filter is not None:
        keys = [k for k in field_filter if k in data]
    elif properties:
        keys = list(properties.keys())
    else:
        keys = list(data.keys())

    if not data:
        lines.append(f"{indent}*Empty*")
    elif keys:
        flat: list[tuple[str, str]] = []
        nested: list[tuple[str, str]] = []

        for key in keys:
            prop_schema = properties.get(key) if properties else None
            if prop_schema is not None and not isinstance(prop_schema, dict):
                continue
            effective = _resolve_anyof_schema(prop_schema) if prop_schema else None
            label = _snake_to_title(key)
            raw_val = data.get(key)

            # Resolve field-level render hint from field_filter dict values.
            field_opts = field_filter.get(key, {}) if field_filter else {}
            render_hint = field_opts.get("render", "expand")

            # The canonical $ref marker is checked before render hints.  A
            # compact_ref template (e.g. owner -> "{login}") has no matching
            # key on a marker and would otherwise fall back to a Python repr;
            # the marker always renders as its compact label.
            if is_ref_marker(raw_val):
                flat.append((label, ref_marker_label(raw_val)))
                continue

            # Render hints override nesting — compact_ref, compact_identity,
            # and badge always produce flat table rows regardless of value type.
            if render_hint == "compact_identity" and isinstance(raw_val, dict):
                flat.append((label, _identity(raw_val, field_opts.get("identity_key"))))
            elif render_hint == "compact_identity" and isinstance(raw_val, list):
                flat.append((label, _identity_list(raw_val, field_opts.get("identity_key"))))
            elif render_hint == "compact_ref" and isinstance(raw_val, dict):
                template = field_opts.get("template", "{id}")
                try:
                    formatted = template.format(**raw_val)
                except (KeyError, ValueError, TypeError):
                    formatted = str(raw_val)
                flat.append((label, formatted))
            elif render_hint == "compact_ref" and isinstance(raw_val, list):
                template = field_opts.get("template", "{name}")
                formatted = _render_list_as_compact_ref(raw_val, template)
                flat.append((label, formatted))
            elif render_hint == "badge":
                flat.append((label, "Yes" if raw_val else "No"))
            elif isinstance(raw_val, (dict, list)):
                # The pipeline pre-collapses nested ``$ref``-backed objects to
                # the canonical marker when ``detail=concise`` (#759), so this
                # formatter only ever renders already-collapsed data — it does
                # not collapse.  Markers were handled above; anything left here
                # is real nested payload.
                # Don't propagate field_filter into nested sub-objects -
                # the parent's field names don't apply to child objects.
                sub = format_as_markdown(
                    raw_val,
                    effective or prop_schema,
                    _depth=_depth + 1,
                )
                if sub.strip():
                    nested.append((label, sub))
            else:
                formatted = _format_scalar(raw_val, prop_schema)
                flat.append((label, formatted))

        _render_flat_table(lines, flat, indent)
        _render_nested_sections(lines, nested, indent, _depth)
    else:
        _render_flat_table(
            lines, [(key, _format_simple_value(val)) for key, val in data.items()], indent
        )

    return "\n".join(lines)


def format_as_markdown(
    data: Any,
    schema: dict[str, Any] | None = None,
    title: str | None = None,
    _depth: int = 0,
    field_filter: dict[str, dict] | None = None,
    item_title_key: str | None = None,
) -> str:
    lines: list[str] = []
    indent = "  " * _depth

    if title and _depth == 0:
        lines.append(f"# {title}")
        lines.append("")

    if data is None:
        lines.append(f"{indent}N/A")
        return "\n".join(lines)

    if isinstance(data, list):
        result = _format_list_as_markdown(
            data,
            schema,
            indent,
            field_filter=field_filter,
            item_title_key=item_title_key,
        )
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    if isinstance(data, dict):
        result = _format_dict_as_markdown(
            data,
            schema,
            indent,
            _depth,
            field_filter=field_filter,
        )
        if title and _depth == 0:
            return f"# {title}\n\n{result}"
        return result

    lines.append(f"{indent}{_format_scalar(data, schema)}")
    return "\n".join(lines)


# ============================================================================
# Tool info markdown formatters (used by tool_info synthetic tool)
# ============================================================================


def _format_type(prop: dict[str, Any]) -> str:
    """Build a user-friendly type string with optional enum or array-item details.

    Enriches the type column of the parameter table so agents can see structural
    information without a second lookup:

    - Enum parameters append allowed values: ``string [merge, rebase, squash]``
    - Array parameters with known item properties list them: ``array of {operation, path, content}``

    Args:
        prop: The JSON Schema property dict for a single parameter.

    Returns:
        A human-readable type string.
    """
    ptype = get_schema_type(prop) or "any"

    # 1. Enum — append allowed values right in the type column
    enum_vals = prop.get("enum")
    if enum_vals:
        vals = ", ".join(str(v) for v in enum_vals)
        return f"{ptype} [{vals}]"

    # 2. Array with known item shape — list key property names
    if ptype == "array":
        items = prop.get("items")
        if isinstance(items, dict):
            item_props = items.get("properties")
            if isinstance(item_props, dict):
                keys = ", ".join(item_props.keys())
                return f"array of {{{keys}}}"

    return ptype


def _format_parameter_table(properties: dict[str, Any], required: list[str]) -> str:
    """Render a parameter table from JSON Schema properties."""
    lines = [
        "## Parameters",
        "",
        "| Parameter | Type | Required | Description |",
        "|-----------|------|----------|-------------|",
    ]
    for param_name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        ptype = _format_type(prop)
        preq = "yes" if param_name in required else "no"
        pdesc = prop.get("description", "").replace("|", "\\|")
        lines.append(f"| {param_name} | {ptype} | {preq} | {pdesc} |")
    lines.append("")
    return "\n".join(lines)


def _format_annotations_table(annotations: dict[str, Any]) -> str:
    """Render an annotations table."""
    lines = ["## Annotations", "", "| Hint | Value |", "|------|-------|"]
    for key in ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
        val = annotations.get(key)
        if val is not None:
            lines.append(f"| {key} | {json_module.dumps(val)} |")
    lines.append("")
    return "\n".join(lines)


def _format_json_section(title: str, data: Any) -> str:
    """Render a JSON code block section."""
    return f"## {title}\n\n```json\n{json_module.dumps(data, indent=2)}\n```\n"


def format_tool_info_markdown(schema: ToolSchemaResult) -> str:
    """Format a ``ToolSchemaResult`` as parseable, consistent markdown.

    Produces a predictable structure with a parameter table that agents can
    parse reliably:

    - ``## Parameters`` — table with ``Parameter | Type | Required | Description``
    - ``## Output Example`` — JSON code block, with a note that it is the
      ``json``/``raw`` shape and not the markdown view
    - ``## Annotations`` — table with ``Hint | Value``
    - ``## Tags`` — comma-separated list
    - ``## Output Schema`` — JSON code block (only when ``output_schema`` present)

    Tool-info output is always full detail — the display pipeline pre-collapses
    only when ``detail=concise``, and this formatter renders fixed-shape output
    that never collapses, so it takes no ``detail`` parameter.
    """
    lines: list[str] = []

    name = schema.get("name", "")
    if name:
        lines.append(f"# {name}")
        lines.append("")

    desc = schema.get("description", "")
    if desc:
        lines.append(desc)
        lines.append("")

    params = schema.get("parameters", {})
    if isinstance(params, dict):
        properties = params.get("properties", {})
        if properties:
            lines.append(_format_parameter_table(properties, params.get("required", [])))

    example = schema.get("output_example")
    if example is not None:
        lines.append(_format_json_section("Output Example", example))
        # The example is the json/raw shape; markdown is a schema-derived view
        # — say so where an agent reads it.
        lines.append("_This is the `json`/`raw` shape; `markdown` renders a")
        lines.append("schema-derived view, not this full shape._")
        lines.append("")

    annotations = schema.get("annotations")
    if isinstance(annotations, dict):
        lines.append(_format_annotations_table(annotations))

    tags = schema.get("tags")
    if tags:
        lines.append("## Tags\n")
        lines.append(", ".join(tags))
        lines.append("")

    output_schema = schema.get("output_schema")
    if isinstance(output_schema, dict):
        lines.append(_format_json_section("Output Schema", output_schema))

    return "\n".join(lines).strip()


def build_server_info_markdown(openapi_spec: OpenAPISpec) -> str:
    """Build server info markdown from OpenAPI spec info block.

    Unlike registered domain formatters (which take ``data`` and optional
    ``extra``), this function takes the raw OpenAPI spec directly.  It lives
    in ``format.py`` rather than ``tools/display.py`` because it is not a
    registered formatter — it is a shared utility used by
    ``resources/custom.py``.
    """
    info = openapi_spec.get("info", {})
    title = info.get("title", "Unknown")
    version = info.get("version", "Unknown")
    description = info.get("description", "")
    lines = [
        "# Server Information",
        "",
        f"**Server Type**: {title}",
        f"**API Version**: {version}",
        "",
    ]
    if description:
        lines.append("## Description")
        lines.append("")
        lines.append(description)
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "MarkdownFormatter",
    "build_server_info_markdown",
    "call_markdown_formatter",
    "collapse_data",
    "decode_base64_content",
    "format_as_markdown",
    "get_formatter",
    "get_formatter_for_type",
    "register_formatter",
    "resolve_formatter",
]
