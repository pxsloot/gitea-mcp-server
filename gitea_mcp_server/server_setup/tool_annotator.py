"""Tool annotation and customization utilities.

Re-export facade — all implementations have been split into focused modules:
  - tool_customize.py: core customization pipeline
  - tool_schemas.py: output schema derivation and $ref resolution
  - tool_errors.py: error handling utilities
  - tool_labels.py: label conversion
  - tool_examples.py: schema-to-example generation
  - tool_search.py: search transform and synthetic tools
"""

# ruff: noqa: F401 — re-exports for backward compat (tests import private names)
from gitea_mcp_server.server_setup.tool_customize import (
    _is_array_response,
    add_inferred_hints,
    categorize_tool,
    compute_invalidation_patterns,
    customize_component,
    derive_required_scope,
    generate_tool_title,
)
from gitea_mcp_server.server_setup.tool_errors import (
    _lookup_response_description,
    _raise_validation_error,
    _raise_value_error,
    _raise_value_error_from,
    _run_validation,
    _run_with_error_handling,
)
from gitea_mcp_server.server_setup.tool_examples import (
    _example_array,
    _example_object,
    _example_string,
    _schema_to_example,
    _serialize_tool_schema,
)
from gitea_mcp_server.server_setup.tool_labels import (
    _convert_labels,
    _format_available_labels,
    update_labels_schema,
)
from gitea_mcp_server.server_setup.tool_schemas import (
    _deep_resolve_schema,
    _get_success_schema,
    _is_text_response,
    _resolve_ref,
    _schema_type_is_array,
    derive_output_schema,
)
from gitea_mcp_server.server_setup.tool_search import (
    TolerantSearchTransform,
    _compact_search_serializer,
)
from gitea_mcp_server.validation import ValidationError

__all__ = [
    "TolerantSearchTransform",
    "add_inferred_hints",
    "categorize_tool",
    "compute_invalidation_patterns",
    "customize_component",
    "derive_output_schema",
    "derive_required_scope",
    "generate_tool_title",
    "update_labels_schema",
]
