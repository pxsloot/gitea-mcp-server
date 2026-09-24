"""Label parameter schema augmentation.

Agents may pass labels as names (strings) or IDs (integers).  The Gitea spec
narrows the ``labels`` item type to one of the two; ``update_labels_schema``
widens it to ``["string", "integer"]`` at schema time so the agent-facing
schema reflects what the runtime accepts.

All label business logic — caching, validation, name/ID conversion — lives in
``label_service.py`` and runs at call time in ``tools/label_transform.py``.
"""

from fastmcp.server.providers.openapi import OpenAPITool

from gitea_mcp_server.schema_utils import schema_type_matches
from gitea_mcp_server.tools.schemas import schema_type_is_array


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
    if not schema_type_is_array(labels_schema):
        return

    items_schema = labels_schema.get("items", {})

    has_string = schema_type_matches(items_schema, "string")
    has_integer = schema_type_matches(items_schema, "integer")
    if has_string or has_integer:
        items_schema["type"] = ["string", "integer"]


__all__ = [
    "update_labels_schema",
]
