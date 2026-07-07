"""Tool customization and discovery package.

All tool-related runtime concerns live here:
- customize: Core customization pipeline (annotations, hints, title, categorization)
- schemas: Output schema derivation and $ref resolution
- errors: Error handling for tool execution
- labels: Label name→ID conversion
- examples: Schema-to-example generation, tool schema serialization
- search: BM25 search engine + TolerantSearchTransform + synthetic tools
- virtual_params: Virtual parameter registry (params that live in the schema
  but are handled before the API call)
- namespace: GiteaNamespace transform (prefix tools, pass resources through)
"""

from gitea_mcp_server.scope import derive_required_scope
from gitea_mcp_server.tools.customize import (
    add_inferred_hints,
    categorize_tool,
    compute_invalidation_patterns,
    generate_tool_title,
)
from gitea_mcp_server.tools.extensions_metadata import ExtensionMetadataTransform
from gitea_mcp_server.tools.namespace import GiteaNamespace
from gitea_mcp_server.tools.schemas import derive_output_schema
from gitea_mcp_server.tools.search import TolerantSearchTransform
from gitea_mcp_server.tools.virtual_params import (
    apply_pre_hooks,
    apply_to,
    extract_from,
    inject_into,
)

__all__ = [
    "ExtensionMetadataTransform",
    "GiteaNamespace",
    "TolerantSearchTransform",
    "add_inferred_hints",
    "apply_pre_hooks",
    "apply_to",
    "categorize_tool",
    "compute_invalidation_patterns",
    "derive_output_schema",
    "derive_required_scope",
    "extract_from",
    "generate_tool_title",
    "inject_into",
]
