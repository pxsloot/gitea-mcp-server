"""Permission filtering utilities — re-exports from tool_filter.py.

This module exists to avoid a circular import: ``server.py`` imports
from ``server_setup``, which would create a cycle if it imported
``tool_filter`` directly.  The ``_handle_adopt.py`` pattern keeps
the filter logic in a flat module while exposing it through the
``server_setup`` package.
"""

from gitea_mcp_server.tool_filter import (
    filter_resources_by_permissions,
    filter_tools_by_permissions,
)

__all__ = ["filter_resources_by_permissions", "filter_tools_by_permissions"]
