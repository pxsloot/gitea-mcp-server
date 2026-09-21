"""Display-view hints for the generic markdown renderer.

The agent-facing markdown *collection* view is derived from the response
schema: the bound type's properties, in declaration order, with scalars as
table rows and ``$ref``-backed relations compacted to an identity.  That
derivation is generic — it works for any type the spec defines, including
types this server has never seen.

A schema cannot express every presentation decision, though.  Some fields
are noise in a list view (URLs, internal flags, nested trackers); some
relations should compact to a specific identity rather than the generic
one.  Those are *deficiencies of the spec*, and this module holds them as
curated tables keyed by type name.

The tables serve two consumers:

* :func:`validate_display_hints` — the pre-wrap validation pass, run once at
  conversion against the schema.  **Fail loud.**  Every hint is validated:
  an unknown property name, or a hint for a type the spec does not define,
  is logged as an error.  A stale hint is a bug, not a silent no-op — this
  is the systemic form of the drift guard the old hand-written whitelists
  lacked.
* :func:`view_hints_for` — the runtime lookup.  The registration layers
  resolve a response type's hints once into ``tool.meta["view_hints"]`` /
  resource content meta (a :class:`~gitea_mcp_server.models.ViewHints`
  dict), so the render path never scans or mutates the spec.

The hints are keyed by **type name**, not by operation: every tool returning
``Issue`` gets the same view, so a tool and its resource sibling can never
disagree.  The runtime renderer reads the properties off the type's schema
and the deficiencies off the resolved ``ViewHints``.

The response-schema helpers (``success_schema`` / ``primary_type``, in
:mod:`~gitea_mcp_server.openapi_converter.type_references`) are what stamp
``x-response-type`` pre-wrap; the display view is keyed by that same type
name, so the two can never disagree about an operation's primary type.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from gitea_mcp_server.models import ViewHints

if TYPE_CHECKING:
    from gitea_mcp_server.openapi_types import OpenAPISpec

logger = logging.getLogger(__name__)

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
        "body",
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
        "body",
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
#:
#: The value is the identity field to read from the nested object (e.g.
#: ``base`` → ``ref``), or ``None`` to use the generic identity policy
#: (``login`` → ``username`` → ``name`` → ``full_name`` → ``id``).  A
#: relation whose object has no identity field must name one here, or it
#: would render as a Python repr.
_VIEW_COMPACT: dict[str, dict[str, str | None]] = {
    "Issue": {
        "user": None,
        "assignee": None,
        "assignees": None,
        "milestone": "title",
        "labels": "name",
    },
    "PullRequest": {
        "user": None,
        "assignee": None,
        "assignees": None,
        "milestone": "title",
        "base": "ref",
        "head": "ref",
        "labels": "name",
    },
    "Repository": {"owner": None},
    "Release": {"author": None},
}

#: Properties to render as a boolean flag (``Yes``/``No``), keyed by type
#: name.  A flag relation is not an identity — ``pull_request`` carries
#: ``{draft, merged, html_url, merged_at}``, so compacting it to ``merged``
#: would be semantically wrong; the agent wants "is this a PR?".
_VIEW_FLAG: dict[str, tuple[str, ...]] = {
    "Issue": ("pull_request",),
}

#: Resolved hints per type, materialized once at import from the curated
#: tables.  This *is* the ``type → (omit, compact, flag)`` index: registration
#: resolves each entity's entry from it, so the render path never rebuilds it
#: and never touches the spec.  Shared read-only, like the tables themselves.
_VIEW_HINTS: dict[str, ViewHints] = {
    type_name: ViewHints(
        omit=list(_VIEW_OMIT.get(type_name, ())),
        compact=dict(_VIEW_COMPACT.get(type_name, {})),
        flag=list(_VIEW_FLAG.get(type_name, ())),
    )
    for type_name in set(_VIEW_OMIT) | set(_VIEW_COMPACT) | set(_VIEW_FLAG)
}


def view_hints_for(response_type: str | None) -> ViewHints | None:
    """Return the curated view hints for a response type, or ``None``.

    ``None`` means the type has no curated deficiency (or no type was bound):
    the generic view then derives everything from the schema, which is the
    common case.  The returned dict is a shared read-only constant.
    """
    if not response_type:
        return None
    return _VIEW_HINTS.get(response_type)


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


def validate_display_hints(openapi_spec: OpenAPISpec) -> None:
    """Validate every curated hint against the component schemas.

    An unknown property name or an undefined type is a bug in the curated
    table — it is reported at ERROR level so it is loud at startup, never a
    silent no-op.  This is the systemic replacement for the old per-whitelist
    drift guard.

    Reads ``components/schemas`` only; it does not depend on response
    wrapping or on the operations that reference a type.  Never raises: drift
    is logged, not thrown.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec.  Not mutated.
    """
    _validate_table(openapi_spec, _VIEW_OMIT, "omit")
    _validate_table(openapi_spec, _VIEW_COMPACT, "compact")
    _validate_table(openapi_spec, _VIEW_FLAG, "flag")


def _validate_table(
    spec: OpenAPISpec,
    table: dict[str, Any],
    key: str,
) -> None:
    """Validate one curated hint table against the spec; log errors on drift."""
    for type_name, hinted in table.items():
        props = _type_properties(spec, type_name)
        if props is None:
            logger.error(
                "Display hint for undefined type %r (%s) — the type is not in "
                "the spec; remove or fix the hint",
                type_name,
                key,
            )
            continue
        for prop in hinted:
            if prop not in props:
                logger.error(
                    "Display hint %s names unknown property %r on type %r — "
                    "the schema changed; fix the hint",
                    key,
                    prop,
                    type_name,
                )


__all__ = [
    "validate_display_hints",
    "view_hints_for",
]
