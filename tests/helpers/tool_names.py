"""Tool name extraction utility for integration tests.

Provides ``extract_tool_names`` to normalise tool listing output from
various MCP structures (dict, list of objects, list of strings) into a
plain list of name strings.
"""


def extract_tool_names(tools):
    """Extract tool names from ``mcp.get_tools()`` return value.

    Handles the three structures MCP may return:

    - ``dict``: keys are tool names
    - ``list`` of objects with a ``.name`` attribute
    - ``list`` of strings (already names)

    Parameters
    ----------
    tools:
        The result from ``mcp.get_tools()``, which can be a dict, list of
        objects, list of strings, or other structure.

    Returns
    -------
    list[str]
        Tool names as strings.  Returns ``[]`` for unrecognised structures.
    """
    if isinstance(tools, dict):
        return list(tools.keys())
    if isinstance(tools, list):
        tool_names = []
        for tool in tools:
            if hasattr(tool, "name"):
                tool_names.append(tool.name)
            elif isinstance(tool, str):
                tool_names.append(tool)
            else:
                try:
                    if hasattr(tool, "get"):
                        name = tool.get("name")
                        if name:
                            tool_names.append(name)
                except Exception:
                    pass
        return tool_names
    return []
