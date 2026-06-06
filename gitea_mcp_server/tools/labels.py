"""Label conversion utilities for tool arguments."""

from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.openapi import OpenAPITool

from gitea_mcp_server.label_manager import LabelManager
from gitea_mcp_server.tools.schemas import _schema_type_is_array
from gitea_mcp_server.validation import ValidationError

if TYPE_CHECKING:
    from gitea_mcp_server.client import GiteaClient


def _format_available_labels(label_names: list[str]) -> str:
    """Format available labels as grouped text for error messages and tool descriptions."""
    groups: dict[str, list[str]] = {}
    for name in label_names:
        prefix = name.split("/", 1)[0] if "/" in name else ""
        groups.setdefault(prefix, []).append(name)

    lines: list[str] = []
    for prefix in sorted(groups, key=lambda p: (p == "", p)):
        label_list = sorted(groups[prefix])
        lines.append(f"  - {', '.join(label_list)}")
    return "\n".join(lines)


async def _convert_labels(
    kwargs: dict[str, Any],
    has_labels: bool,
    label_manager: LabelManager,
    gitea_client: "GiteaClient | None" = None,
) -> None:
    if not has_labels:
        return
    labels = kwargs.get("labels")
    if not labels or all(isinstance(label, int) for label in labels):
        return

    owner = kwargs.get("owner") or kwargs.get("org")
    repo = kwargs.get("repo")
    if not owner or not repo:
        return

    if gitea_client is None:
        return

    label_map = await label_manager.get_label_map(owner, repo, gitea_client)
    converted = []
    unknown = []
    for label in labels:
        if isinstance(label, str):
            label_lower = label.lower()
            if label_lower in label_map:
                converted.append(label_map[label_lower]["id"])
            else:
                unknown.append(label)
        else:
            converted.append(label)

    if unknown:
        available = sorted(v["name"] for v in label_map.values())
        formatted = _format_available_labels(available)
        msg = (
            f"Unknown label(s): {unknown}.\n\n"
            f"Available labels for {owner}/{repo}:\n"
            f"{formatted}\n\n"
            f"Use list_labels({owner}, {repo}) or read "
            f"gitea://repos/{owner}/{repo}/labels to see details."
        )
        raise ValidationError(message=msg, field="labels")

    kwargs["labels"] = converted


def update_labels_schema(component: OpenAPITool) -> None:
    params = getattr(component, "parameters", None)
    if not params:
        return

    props = params.get("properties", {})
    if "labels" not in props:
        return

    labels_schema = props["labels"]
    if not _schema_type_is_array(labels_schema):
        return

    items_schema = labels_schema.get("items", {})

    current_type = items_schema.get("type")
    if current_type in ("integer", "string"):
        items_schema["type"] = ["string", "integer"]


__all__ = [
    "_convert_labels",
    "_format_available_labels",
    "update_labels_schema",
]
