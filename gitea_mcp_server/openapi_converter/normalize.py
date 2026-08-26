"""Normalize agent-misleading spec quirks at the transform layer.

The server mirrors the OpenAPI spec one-to-one (design decision #17) — the
spec is the source of truth.  But a few *classes* of spec quirks actively
mislead agents, and these are systemic: Gitea's spec mixes naming conventions
and response shapes in ways that recur across many endpoints.  Rather than
hand-fix individual tools, this module applies **shape-driven** normalization
rules to the whole spec before FastMCP sees it.

Two rules live here:

**Rule A — snake_case parameter normalization.**  Gitea's spec mixes
conventions: body properties like ``Do``/``MergeCommitID`` (on
``MergePullRequestOption``), query params like ``includeDesc``/``starredBy``,
and path params like ``pageName``/``repository-id``.  Non-snake_case names
read like sentence words or leak Go struct internals.  This rule renames any
parameter or body property that is not snake_case and that
:func:`camel_to_snake` can convert (kebab-case names like ``repository-id``
are already readable and are left alone), recording the mapping in
an ``x-param-rename`` extension on the operation (merged with any collision
map set by :func:`resolve_param_collisions`).  ``$ref`` request bodies are
resolved (deep-copied) by this module itself, so the rule is self-sufficient
— it does not depend on collision resolution having inlined the body.  At
runtime, the shim in ``mcp_builder._apply_param_rename`` corrects the
``parameter_map`` so the HTTP request still sends the original wire name.

Path parameters are **deferred** to issue #734: renaming them additionally
requires rewriting the ``{placeholder}`` in the route path template, which is
a deeper FastMCP-IR mutation.  This module normalizes body, query, header,
and cookie parameters only.

**Rule B — boolean-check response normalization.**  Gitea models "is this
thing true?" endpoints as a GET that returns ``204 No Content`` on success
and ``404`` when the answer is "no" (e.g. ``repoPullRequestIsMerged``,
``orgIsMember``, ``repoCheckCollaborator``).  The raw shape is ambiguous: an
agent cannot distinguish "exists but false" from "doesn't exist", and a 204
carries no boolean.  This rule detects the shape — a GET whose success
response is a contentless 204 and which declares a 404 — and annotates the
operation with ``x-response-transform: "boolean-check"``.  The schema-time
and runtime pipeline then return an unambiguous boolean and distinguish
"not merged" from "not found".

Both rules are **shape-driven, not name-driven**: they trigger on the shape
of the spec (naming convention, response structure), never on a hardcoded
list of operationIds.  This keeps the normalization generic so it keeps
working as the Gitea spec evolves.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import TYPE_CHECKING, Any

from gitea_mcp_server.constants import HTTP_METHODS_ALL
from gitea_mcp_server.openapi_converter.core import camel_to_snake, resolve_spec_ref

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)

# A snake_case name: lowercase alphanumerics separated by single underscores.
_SNAKE_CASE_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")

# Parameter locations that carry a single scalar name on the wire (as opposed
# to body properties, which are keys inside a JSON object).  Path is deferred
# to issue #734.
_NON_PATH_PARAM_LOCATIONS = ("query", "header", "cookie")


def _is_snake_case(name: str) -> bool:
    """Return ``True`` if ``name`` is already snake_case."""
    return bool(_SNAKE_CASE_RE.fullmatch(name))


def _merge_rename_map(operation: dict[str, Any], rename_map: dict[str, str]) -> None:
    """Merge ``rename_map`` into the operation's ``x-param-rename`` extension.

    Collision resolution (:func:`resolve_param_collisions`) may already have
    set ``x-param-rename``.  Normalization must not clobber it — the two
    passes are independent and both correct the ``parameter_map`` at runtime.
    On a key conflict (same new name from both passes) normalization wins,
    since it runs last and its mapping is the authoritative final name.

    Args:
        operation: The OpenAPI operation dict (mutated in-place).
        rename_map: ``{new_name: original_name}`` mapping to merge.
    """
    if not rename_map:
        return
    existing = operation.get("x-param-rename")
    if isinstance(existing, dict):
        existing.update(rename_map)
    else:
        operation["x-param-rename"] = dict(rename_map)


def _normalize_operation_parameters(
    operation: dict[str, Any],
) -> dict[str, str]:
    """Rename non-snake_case query/header/cookie parameters in an operation.

    Mutates ``operation["parameters"]`` in-place.  Returns a
    ``{new_name: original_name}`` mapping for the renames performed.

    Args:
        operation: The OpenAPI operation dict (mutated in-place).

    Returns:
        Rename map (``{new_name: original_name}``), possibly empty.
    """
    rename_map: dict[str, str] = {}
    params = operation.get("parameters")
    if not isinstance(params, list):
        return rename_map

    for param in params:
        if not isinstance(param, dict):
            continue
        location = param.get("in")
        if location not in _NON_PATH_PARAM_LOCATIONS:
            continue
        name = param.get("name")
        if not isinstance(name, str) or not name:
            continue
        if _is_snake_case(name):
            continue
        new_name = camel_to_snake(name)
        if new_name == name or not new_name:
            continue
        param["name"] = new_name
        rename_map[new_name] = name
        logger.debug(
            "Normalized %s parameter '%s' -> '%s'",
            location,
            name,
            new_name,
        )
    return rename_map


def _get_body_schema(
    operation: dict[str, Any],
    openapi_spec: OpenAPISpec,
) -> dict[str, Any] | None:
    """Extract the request body schema dict from an operation.

    Resolves ``$ref`` chains to shared components (deep-copied so the
    original component definition is never mutated) and writes the resolved
    schema back into the operation.  Collision resolution
    (:func:`resolve_param_collisions`) already inlines ``$ref`` bodies for
    operations *with* path parameters; this resolution makes Rule A
    self-sufficient for operations *without* path parameters (e.g.
    ``POST /markdown``), which collision resolution never visits.

    Args:
        operation: The OpenAPI operation dict.
        openapi_spec: The full OpenAPI spec for ``$ref`` resolution.

    Returns:
        The body schema dict, or ``None`` if no JSON request body exists.
    """
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return None
    content = request_body.get("content", {})
    if not isinstance(content, dict):
        return None
    json_content = content.get("application/json", {})
    if not isinstance(json_content, dict):
        return None
    schema = json_content.get("schema")
    if not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        resolved = resolve_spec_ref(openapi_spec, schema["$ref"])
        if isinstance(resolved, dict):
            schema = copy.deepcopy(resolved)
            json_content["schema"] = schema
    return schema


def _normalize_operation_body(
    operation: dict[str, Any],
    openapi_spec: OpenAPISpec,
) -> dict[str, str]:
    """Rename non-snake_case body properties in an operation.

    Mutates the body schema's ``properties``/``required`` in-place.  Returns
    a ``{new_name: original_name}`` mapping for the renames performed.

    Args:
        operation: The OpenAPI operation dict (mutated in-place).
        openapi_spec: The full OpenAPI spec for ``$ref`` resolution.

    Returns:
        Rename map (``{new_name: original_name}``), possibly empty.
    """
    body_schema = _get_body_schema(operation, openapi_spec)
    if body_schema is None:
        return {}

    props = body_schema.get("properties")
    if not isinstance(props, dict):
        return {}

    required = body_schema.get("required")
    if not isinstance(required, list):
        required = []

    rename_map: dict[str, str] = {}
    for name in list(props.keys()):
        if _is_snake_case(name):
            continue
        new_name = camel_to_snake(name)
        if new_name == name or not new_name:
            continue
        if new_name in props:
            # A body with both ``Do`` and ``do`` would silently overwrite the
            # existing property.  No Gitea endpoint exhibits this today, but
            # warn loudly so an evolving spec fails loudly rather than
            # silently dropping a property.
            logger.warning(
                "Body property '%s' normalizes to '%s' which already exists; "
                "skipping rename to avoid overwriting",
                name,
                new_name,
            )
            continue
        prop_data = props.pop(name)
        props[new_name] = prop_data
        rename_map[new_name] = name
        if name in required:
            required.remove(name)
            required.append(new_name)
        logger.debug("Normalized body property '%s' -> '%s'", name, new_name)

    if rename_map and ("required" in body_schema or required):
        # Only write ``required`` back when the schema already declared it or
        # a rename touched it — never fabricate an empty ``required: []`` on
        # a schema that had none.
        body_schema["required"] = required

    return rename_map


def _is_boolean_check_operation(
    operation: dict[str, Any],
    openapi_spec: OpenAPISpec,
) -> bool:
    """Detect the "is this thing true?" response shape.

    A boolean-check endpoint is a GET whose success response is a contentless
    204 (no 200/201 carrying content) and which declares a 404.  This is the
    shape Gitea uses for membership/merge/star/follow checks.

    The 200/201-with-content guard is essential: a GET that *fetches* a
    resource (e.g. ``issueGetComment``) also declares a 204 and 404 but is
    not a boolean check — it has a 200 with content.  Requiring the success
    response to be contentless excludes those.  ``$ref`` responses are
    resolved so a 200 that references a content-bearing response (e.g.
    ``#/components/responses/Comment``) is correctly excluded.

    Args:
        operation: The OpenAPI operation dict.
        openapi_spec: The full OpenAPI spec for ``$ref`` resolution.

    Returns:
        ``True`` if the operation matches the boolean-check shape.
    """
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return False
    if "204" not in responses:
        return False
    if "404" not in responses:
        return False

    # A 200/201 with content means this is a fetch, not a check.
    for code in ("200", "201"):
        resp = responses.get(code)
        if not isinstance(resp, dict):
            continue
        if "$ref" in resp:
            resolved = resolve_spec_ref(openapi_spec, resp["$ref"])
            if isinstance(resolved, dict):
                resp = resolved
        content = resp.get("content")
        if isinstance(content, dict) and content:
            return False
    return True


def _annotate_boolean_checks(openapi_spec: OpenAPISpec) -> int:
    """Annotate boolean-check operations with ``x-response-transform``.

    Mutates ``openapi_spec`` in-place.  Returns the number of operations
    annotated.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (mutated in-place).
    """
    annotated = 0
    paths: dict[str, Any] = openapi_spec.get("paths", {}) or {}
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS_ALL:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            if method != "get":
                continue
            if not _is_boolean_check_operation(operation, openapi_spec):
                continue
            operation["x-response-transform"] = "boolean-check"
            annotated += 1
            logger.debug(
                "Annotated boolean-check operation %s (%s %s)",
                operation.get("operationId", "<unknown>"),
                method.upper(),
                path,
            )
    return annotated


def normalize_spec(openapi_spec: OpenAPISpec) -> None:
    """Normalize agent-misleading spec quirks across all operations.

    Applies two shape-driven rules:

    1. **snake_case parameters** — renames non-snake_case query/header/cookie
       parameters and body properties (that :func:`camel_to_snake` can
       convert), recording the mapping in ``x-param-rename`` (merged with any
       collision map).
    2. **boolean-check responses** — annotates GET operations whose success
       response is a contentless 204 with a 404 declared, setting
       ``x-response-transform: "boolean-check"``.

    Mutates ``openapi_spec`` in-place.  Called after spec conversion and
    after :func:`resolve_param_collisions`, before FastMCP processes the spec.

    This function is guaranteed not to raise: each operation is normalized
    inside its own try/except (a failure on one operation is logged and
    skipped, so the rest of the spec still normalizes), and the outer
    try/except catches anything unexpected.  Callers do not need a try/except
    wrapper.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (mutated in-place).
    """
    try:
        paths: dict[str, Any] = openapi_spec.get("paths", {}) or {}
        total_renames = 0
        affected_ops: list[str] = []

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method in HTTP_METHODS_ALL:
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue

                try:
                    rename_map: dict[str, str] = {}
                    rename_map.update(_normalize_operation_parameters(operation))
                    rename_map.update(_normalize_operation_body(operation, openapi_spec))
                    if rename_map:
                        _merge_rename_map(operation, rename_map)
                        total_renames += len(rename_map)
                        op_id = operation.get("operationId", f"{method} {path}")
                        affected_ops.append(op_id)
                        logger.debug(
                            "Normalized %d parameter names for %s: %s",
                            len(rename_map),
                            op_id,
                            rename_map,
                        )
                except Exception:
                    # One malformed operation must not abort normalization of
                    # the whole spec.  Log and skip it; the raw quirks on that
                    # operation remain, which is a degraded UX but not a crash.
                    logger.exception(
                        "Failed to normalize operation %s %s",
                        method.upper(),
                        path,
                    )

        boolean_checks = _annotate_boolean_checks(openapi_spec)

        if total_renames:
            logger.info(
                "Normalized %d parameter names across %d operations: %s",
                total_renames,
                len(affected_ops),
                sorted(affected_ops),
            )
        if boolean_checks:
            logger.info(
                "Annotated %d boolean-check operations",
                boolean_checks,
            )
    except Exception:
        # Broad catch is intentional: this function is called during spec
        # loading and must never propagate.  Normalization is a best-effort
        # optimisation — if it fails, the raw spec quirks remain, which is a
        # degraded UX but not a crash.  ``logger.exception`` includes the full
        # traceback so operators can diagnose the root cause.
        logger.exception("Failed to normalize spec quirks")


__all__ = [
    "normalize_spec",
]
