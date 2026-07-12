"""Label conversion utilities for tool arguments.

This module is a thin adapter between the tool runtime pipeline and
``LabelService``.  All label business logic — caching, validation,
conversion — lives in ``label_service.py``.
"""

from typing import TYPE_CHECKING, Any

from fastmcp.server.providers.openapi import OpenAPITool

from gitea_mcp_server.label_service import LabelService
from gitea_mcp_server.tools.schemas import _schema_type_is_array

if TYPE_CHECKING:
    from gitea_mcp_server.client import GiteaClient


async def _convert_labels(
    kwargs: dict[str, Any],
    has_labels: bool,
    label_service: LabelService,
    gitea_client: "GiteaClient | None" = None,
) -> None:
    """Convert label strings/ints to validated integer IDs in-place.

    .. deprecated::
       Replaced by ``_convert_labels_inline`` in ``tools/label_transform.py``.
       The ``has_labels`` guard is now handled upstream by
       ``LabelTransform._should_wrap()``.  Kept for test backward
       compatibility only; no production code imports this.

    Delegates all validation and conversion to ``LabelService.validate_and_convert``.

    Args:
        kwargs: The tool's keyword arguments (mutated in-place).
        has_labels: Whether the tool's schema has a ``labels`` parameter.
        label_service: The ``LabelService`` instance.
        gitea_client: GiteaClient for API calls.  If ``None``, conversion is skipped.

    Raises:
        ValidationError: If any label name or ID is unknown.
    """
    if not has_labels:
        return
    labels = kwargs.get("labels")
    if not labels:
        return

    owner = kwargs.get("owner") or kwargs.get("org")
    repo = kwargs.get("repo")
    if not owner or not repo:
        return
    if gitea_client is None:
        return

    converted = await label_service.validate_and_convert(
        labels, owner, repo, gitea_client
    )
    kwargs["labels"] = converted


def update_labels_schema(component: OpenAPITool) -> None:
    """Update a tool's labels parameter schema to accept both strings and integers.

    Mutates the component's parameter schema in-place so agents see
    ``["string", "integer"]`` as the accepted item type.

    Args:
        component: The OpenAPITool whose schema to augment.
    """
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
    "update_labels_schema",
]
