"""Display-view hints for the generic markdown renderer (pre-wrap).

The agent-facing markdown *collection* view is derived from the response
schema: the bound type's properties, in declaration order, with scalars as
table rows and ``$ref``-backed relations compacted to an identity.  That
derivation is generic — it works for any type the spec defines, including
types this server has never seen.

A schema cannot express every presentation decision, though.  Some fields
are noise in a list view (URLs, internal flags, nested trackers); some
relations should compact to a specific identity rather than the generic
one.  Those are *deficiencies of the spec*, and this module fixes them the
same way the other converter rules fix swagger deficiencies: by stamping
operation-level extensions, so the runtime stays generic.

Two extensions are stamped on every operation whose success response has a
primary type:

* ``x-mcp-view-omit`` — property names to drop from the collection view.
* ``x-mcp-view-compact`` — property names to render as a compact identity
  instead of a nested section.

The hints are keyed by **type name**, not by operation: every operation
returning ``Issue`` gets the same view, so a tool and its resource sibling
can never disagree.  The runtime renderer reads the hints off the operation
(``x-mcp-view-*``) and the properties off the type's schema.

**Fail loud.**  Every hint is validated against the resolved type schema:
an unknown property name, or a hint for a type the spec does not define, is
logged as an error.  A stale hint is a bug, not a silent no-op — this is the
systemic form of the drift guard the old hand-written whitelists lacked.

This module is deliberately self-contained (no imports from ``core``) so
``core`` can import :func:`stamp_display_hints` without a circular import.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.constants import HTTP_METHODS_ALL
from gitea_mcp_server.schema_utils import extract_type_name

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)

# Operation-level extension keys.  ``x-mcp-`` marks them as our own metadata
# (like ``x-response-type``), not a Gitea leak; they are stripped from the
# agent-facing resolved output schema by ``tools/schemas.deep_resolve_schema``.
VIEW_OMIT_KEY = "x-mcp-view-omit"
VIEW_COMPACT_KEY = "x-mcp-view-compact"

# ---------------------------------------------------------------------------
# Curated hints — a deficiency list, not a view definition.
#
# Only fields the schema cannot express belong here.  The generic renderer
# derives everything else from the type's properties, so a new or renamed
# schema field appears automatically; a hint naming a field that no longer
# exists fails loudly at startup.
# ---------------------------------------------------------------------------

#: Properties to drop from the collection view, keyed by type name.
_VIEW_OMIT: dict[str, tuple[str, ...]] = {
    "Issue": (
        "assets",
        "repository",
        "url",
        "original_author",
        "original_author_id",
        "pin_order",
        "ref",
    ),
    "PullRequest": (
        "url",
        "diff_url",
        "patch_url",
        "merge_base",
        "merge_commit_sha",
        "merged_by",
        "requested_reviewers",
        "requested_reviewers_teams",
        "pin_order",
        "flow",
    ),
    "Repository": (
        "url",
        "clone_url",
        "ssh_url",
        "languages_url",
        "link",
        "parent",
        "permissions",
        "internal_tracker",
        "external_tracker",
        "external_wiki",
        "repo_transfer",
        "avatar_url",
        "wiki_branch",
        "wiki_clone_url",
        "wiki_ssh_url",
        "mirror_interval",
        "mirror_updated",
        "original_url",
        "object_format_name",
        "default_allow_maintainer_edit",
        "default_delete_branch_after_merge",
        "default_merge_style",
        "default_update_style",
        "allow_fast_forward_only_merge",
        "allow_merge_commits",
        "allow_rebase",
        "allow_rebase_explicit",
        "allow_rebase_update",
        "allow_squash_merge",
        "ignore_whitespace_conflicts",
        "globally_editable_wiki",
        "has_actions",
        "has_issues",
        "has_packages",
        "has_projects",
        "has_pull_requests",
        "has_releases",
        "has_wiki",
        "has_wiki_contents",
        "empty",
        "template",
        "internal",
        "fork",
        "archived_at",
        "release_counter",
        "watchers_count",
    ),
    "User": (
        "id",
        "login_name",
        "source_id",
        "language",
        "last_login",
        "prohibit_login",
        "restricted",
        "active",
        "is_admin",
        "starred_repos_count",
        "avatar_url",
    ),
    "Organization": (
        "id",
        "avatar_url",
        "repo_admin_change_team_access",
    ),
    "Release": (
        "id",
        "url",
        "upload_url",
        "tarball_url",
        "zipball_url",
        "archive_download_count",
        "hide_archive_links",
        "target_commitish",
    ),
}

#: Properties to render as a compact identity, keyed by type name.
_VIEW_COMPACT: dict[str, tuple[str, ...]] = {
    "Issue": ("user", "assignee", "assignees", "milestone", "pull_request"),
    "PullRequest": ("user", "assignee", "assignees", "milestone", "base", "head"),
    "Repository": ("owner",),
    "Release": ("author",),
}


def _resolve_ref(spec: OpenAPISpec, ref: str) -> dict[str, Any] | None:
    """Resolve a ``$ref`` pointer (e.g. ``#/components/schemas/Foo``) in a spec.

    Walks the spec tree using string path segments.  Returns ``None`` if any
    segment is missing (handles malformed refs gracefully).
    """
    parts = ref.lstrip("#/").split("/")
    current: Any = spec
    try:
        for part in parts:
            current = current[part]
    except (KeyError, TypeError):
        return None
    return current if isinstance(current, dict) else None


def _success_schema(spec: OpenAPISpec, path: str, method: str) -> dict[str, Any] | None:
    """Return the raw 200/201 response schema (pre-wrap, ``$ref`` intact)."""
    paths: dict[str, Any] = cast("dict[str, Any]", spec.get("paths", {}))
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return None
    operation = path_item.get(method.lower())
    if not isinstance(operation, dict):
        return None
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return None
    for code in ("200", "201"):
        response = responses.get(code)
        if not isinstance(response, dict):
            continue
        if "$ref" in response:
            resolved = _resolve_ref(spec, response["$ref"])
            if not isinstance(resolved, dict):
                continue
            response = resolved
        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        json_content = content.get("application/json", {})
        if not isinstance(json_content, dict):
            continue
        schema = json_content.get("schema")
        if isinstance(schema, dict):
            return schema
    return None


def _primary_type(schema: dict[str, Any] | None) -> str | None:
    """Extract the primary (element) type name from a response schema.

    Handles array responses (``items.$ref``) and object responses (``$ref``,
    including a root wrapped in ``allOf``/``anyOf``/``oneOf``).  Returns
    ``None`` when the schema has no single primary type.
    """
    if not schema:
        return None
    type_ = schema.get("type")
    if type_ == "array" or (isinstance(type_, list) and "array" in type_):
        return extract_type_name(schema.get("items"))
    return extract_type_name(schema)


def _type_properties(spec: OpenAPISpec, type_name: str) -> set[str] | None:
    """Return the property names of a named component schema, or ``None``.

    ``None`` means the type is not defined in the spec (or has no
    ``properties``) — the caller reports it as a hint error.
    """
    components = spec.get("components", {})
    schemas = components.get("schemas", {})
    if not isinstance(schemas, dict):
        return None
    schema = schemas.get(type_name)
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties")
    if not isinstance(props, dict):
        return None
    return set(props.keys())


def _validate_hints(spec: OpenAPISpec) -> None:
    """Validate every curated hint against the spec; log errors on drift.

    An unknown property name or an undefined type is a bug in the curated
    table — it is reported at ERROR level so it is loud at startup, never a
    silent no-op.  This is the systemic replacement for the old per-whitelist
    drift guard.
    """
    for type_name, omitted in _VIEW_OMIT.items():
        props = _type_properties(spec, type_name)
        if props is None:
            logger.error(
                "Display hint for undefined type %r (%s) — the type is not in "
                "the spec; remove or fix the hint",
                type_name,
                VIEW_OMIT_KEY,
            )
            continue
        for prop in omitted:
            if prop not in props:
                logger.error(
                    "Display hint %s names unknown property %r on type %r — "
                    "the schema changed; fix the hint",
                    VIEW_OMIT_KEY,
                    prop,
                    type_name,
                )
    for type_name, compacted in _VIEW_COMPACT.items():
        props = _type_properties(spec, type_name)
        if props is None:
            logger.error(
                "Display hint for undefined type %r (%s) — the type is not in "
                "the spec; remove or fix the hint",
                type_name,
                VIEW_COMPACT_KEY,
            )
            continue
        for prop in compacted:
            if prop not in props:
                logger.error(
                    "Display hint %s names unknown property %r on type %r — "
                    "the schema changed; fix the hint",
                    VIEW_COMPACT_KEY,
                    prop,
                    type_name,
                )


def stamp_display_hints(openapi_spec: OpenAPISpec) -> None:
    """Stamp ``x-mcp-view-*`` hints on operations, keyed by response type.

    For every operation whose success response has a primary type, the
    curated omit/compact lists for that type are stamped onto the operation
    (only when non-empty).  The runtime renderer reads them off the
    operation; the properties themselves are read from the type's schema, so
    the view stays schema-anchored.

    Must run *before* ``_wrap_success_response_schemas`` — the wrapping
    inlines the root ``$ref`` and erases the type name this function keys on.

    Mutates ``openapi_spec`` in place.  Never raises: hint validation logs
    errors, and a malformed operation is skipped.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec (pre-wrap, ``$ref``
            intact).  Mutated in place.
    """
    _validate_hints(openapi_spec)

    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in HTTP_METHODS_ALL or not isinstance(operation, dict):
                continue
            response_type = _primary_type(_success_schema(openapi_spec, path, method))
            if not response_type:
                continue
            omitted = _VIEW_OMIT.get(response_type)
            if omitted:
                operation[VIEW_OMIT_KEY] = list(omitted)
            compacted = _VIEW_COMPACT.get(response_type)
            if compacted:
                operation[VIEW_COMPACT_KEY] = list(compacted)


__all__ = [
    "VIEW_COMPACT_KEY",
    "VIEW_OMIT_KEY",
    "stamp_display_hints",
]
