"""Bespoke display formatters for resources and type-bound tools.

Most agent-facing markdown is **derived from the response schema** by the
format layer's generic collection view (``format._generic_collection_view``,
#771): the bound type's properties, in declaration order, with ``$ref``-backed
relations compacted to an identity.  The only curated knowledge is the
converter-stamped deficiency list (``x-mcp-view-omit`` /
``x-mcp-view-compact``, ``openapi_converter/display_hints.py``), so a new or
renamed schema field appears automatically.

This module holds the formatters that are genuinely *bespoke* — a view the
schema cannot express.  Today that is one: ``labels``, which carries the
accepted-format and validation guidance that helps an agent choose labels,
not just a field projection.

All resources and tools return raw data.  The single result pipeline
(``tools/result_pipeline.py``) resolves a formatter through
``format.resolve_formatter``:

- **Resources** — the ``read_resource`` executor (``tools/mcp_tools.py``)
  resolves the resource's ``format_hint`` (content metadata) into an
  ``ExecutionResult.markdown_formatter`` and forwards the remaining content
  meta (``owner``/``repo``/``type``) as ``ExecutionResult.extra``.
- **Tools** — ``format.resolve_formatter`` binds a formatter by the result's
  ``response_type`` (the ``types=`` argument below), so a tool renders the
  same view as its resource sibling.

This module is a pure plugin set: the registry itself lives in
``format.py`` (the format layer) so the result pipeline resolves formatters
without importing this module — Result → Format → Display.  Each formatter
below registers via :func:`~gitea_mcp_server.format.register_formatter`.

Every formatter here is a :data:`~gitea_mcp_server.format.MarkdownFormatter`
— a pure renderer that takes ``data`` and may declare a keyword-only
``extra`` for context.  The contract itself (and why ``detail`` is not part
of it) is stated canonically in ``format.py``; this module does not restate
it.
"""

from typing import Any

from gitea_mcp_server.format import (
    register_formatter,
)


@register_formatter("labels", types=["Label"])
def _format_labels_markdown(
    data: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> str:
    """Format labels as Markdown with format and validation hints.

    Bespoke by design: the collection view carries the accepted-format and
    validation guidance that helps an agent *choose* labels — knowledge the
    Label schema cannot express.  The field projection itself is generic
    (``_label_detail_lines`` reads the label dict directly).

    Needs ``extra`` with a scope for the heading: ``owner``/``repo`` on
    repo-scoped tools, or ``org`` on org-scoped tools (``org_list_labels``,
    ``org_get_label``, …) — the org is the owner-equivalent there (#766).

    The formatter is a pure renderer and does not know the requested
    ``detail`` level: under ``detail=concise`` the pipeline summarizes
    items (#759) — the ``Label`` schema has no nested ``$ref`` fields, so
    a concise item is the full scalar dict and renders here unchanged.
    Items are dicts on both detail levels; the non-dict branch below is a
    defensive guard for unexpected shapes, not the collapse contract.

    A *dict* arrives from single-label reads (``issue_get_label``)
    and renders the detail view instead of the collection list.
    """
    if isinstance(data, dict):
        return _format_label_detail(data, extra=extra)

    scope = _label_scope(extra)

    lines = [
        f"# Labels for {scope}",
        "",
        f"**Total**: {len(data)} labels",
        "",
        "## Accepted Format",
        "",
        "Labels can be specified as either:",
        '- **Names** (strings): e.g. `"bug"`, `"Kind/Feature"`',
        "- **IDs** (integers): e.g. `1`, `42`",
        "",
        "**Validation**: Both names and IDs are validated against the"
        " repository's existing labels.",
        " Unknown values produce an error listing available labels.",
        "",
    ]

    if not data:
        lines.append("*No labels configured for this repository.*")
        lines.append("")
    else:
        lines.append(f"## Labels ({len(data)})")
        lines.append("")
        for label in data:
            if not isinstance(label, dict):
                # Guard against non-dict items (unexpected data shape).
                lines.append(f"- {label}")
                continue
            name = label.get("name", "Unnamed")
            archived_tag = " *(archived)*" if label.get("is_archived", False) else ""
            lines.append(f"### {name} (#{label.get('id', '?')}){archived_tag}")
            lines.extend(_label_detail_lines(label))
            lines.append("")

    return "\n".join(lines)


def _label_scope(extra: dict | None) -> str:
    """Resolve the label scope label from formatter context.

    Repo-scoped tools pass ``owner``/``repo``; org-scoped tools pass ``org``
    (the owner-equivalent, #766).  Falls back to ``?/?`` when neither is
    present, matching the pre-#766 graceful default.
    """
    ctx = extra or {}
    owner = ctx.get("owner") or ctx.get("org")
    repo = ctx.get("repo")
    if owner and repo:
        return f"{owner}/{repo}"
    if owner:
        return str(owner)
    return "?/?"


def _label_detail_lines(label: dict) -> list[str]:
    """Bullet lines for one label dict — shared by both labels views."""
    name = label.get("name", "Unnamed")
    color = label.get("color", "")
    desc = label.get("description") or "(no description)"
    exclusive = label.get("exclusive", False)

    scope_info = ""
    if "/" in name:
        scope = name.rsplit("/", 1)[0]
        scope_info = f" (scope: `{scope}`)"

    return [
        f"- **Color**: `#{color}`",
        f"- **Description**: {desc}",
        f"- **Exclusive**: {'Yes' if exclusive else 'No'}{scope_info}",
    ]


def _format_label_detail(label: dict, *, extra: dict | None = None) -> str:
    """Detail view for a single Label dict (``issue_get_label``).

    No collection heading or validation-format section — those guide label
    *selection*; a detail read already holds one label.  The scope is shown
    when ``extra`` carries it (``issue_get_label`` passes ``owner``/``repo``;
    ``org_get_label`` passes ``org``, #766); graceful otherwise.
    """
    ctx = extra or {}
    owner = ctx.get("owner") or ctx.get("org")
    repo = ctx.get("repo")
    scope = f" for {owner}/{repo}" if owner and repo else (f" for {owner}" if owner else "")
    name = label.get("name", "Unnamed")
    archived_tag = " *(archived)*" if label.get("is_archived", False) else ""
    lines = [f"# Label: {name} (#{label.get('id', '?')}){archived_tag}{scope}", ""]
    lines.extend(_label_detail_lines(label))
    return "\n".join(lines)


__all__ = [
    "_format_labels_markdown",
]
