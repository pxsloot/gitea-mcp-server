"""MCP Resource Access Tools.

These tools allow agents to discover and read MCP resources that are registered
with the server. They bridge the gap between the resource protocol and the agent's
toolset.

Tool list:
- list_resources: List all available MCP resources
- read_resource: Read a resource by its URI
- search_resources: Search resources by natural language (BM25 ranking)
- gitea://tool/{name}/schema: Resource for full tool schema by name
"""

import json
import logging
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.format import _format_as_markdown
from gitea_mcp_server.search import BM25SearchEngine
from gitea_mcp_server.tools.examples import _schema_to_example

logger = logging.getLogger(__name__)


def _extract_resource_content(contents: list[Any] | None, uri: str) -> str:
    """Extract and convert content from resource result."""
    if not contents:
        msg = f"Resource '{uri}' returned no content"
        raise LookupError(msg) from None
    content = contents[0].content
    if isinstance(content, bytes):
        return content.decode("utf-8")
    if isinstance(content, str):
        return content
    return str(content)


async def _mcp_list_resources_impl(ctx: Context) -> dict[str, Any]:
    """Implementation of list_resources.

    Uses FastMCP Context to list registered resources and templates.

    Args:
        ctx: FastMCP Context object (injected automatically)

    Returns:
        Dictionary with 'resources' key and 'count' key
    """
    resources_list = []

    try:
        # Use public FastMCP API to list resources and templates
        resources = await ctx.fastmcp.list_resources()
        templates = await ctx.fastmcp.list_resource_templates()

        def _build_resource_entry(
            base: dict[str, Any],
            meta: dict[str, Any] | None,
        ) -> dict[str, Any]:
            """Add required_scope to a resource entry from its metadata."""
            if meta:
                scope = meta.get("fastmcp", {}).get("_internal", {}).get("required_scope")
            else:
                scope = None
            base["required_scope"] = scope
            return base

        # Process concrete resources
        # FastMCP auto-populates name from function name when not explicitly set:
        # @mcp.resource("uri://foo")  -> name="foo" (function name)
        # @mcp.resource("uri://bar", name="custom") -> name="custom"
        for resource in resources:
            entry = _build_resource_entry(
                {
                    "uri": str(resource.uri),
                    "name": resource.name,
                    "description": resource.description or "",
                    "mimeType": resource.mime_type or "text/plain",
                    "type": "resource",
                    "tags": list(resource.tags)
                    if hasattr(resource, "tags") and resource.tags
                    else [],
                },
                getattr(resource, "meta", None),
            )
            resources_list.append(entry)

        # Process resource templates
        for template in templates:
            entry = _build_resource_entry(
                {
                    "uri": str(template.uri_template),
                    "name": template.name,
                    "description": template.description or "",
                    "mimeType": template.mime_type or "text/plain",
                    "type": "template",
                    "tags": list(template.tags)
                    if hasattr(template, "tags") and template.tags
                    else [],
                },
                getattr(template, "meta", None),
            )
            resources_list.append(entry)
    except (AttributeError, TypeError):
        logger.exception("Error listing resources")
        return {"resources": [], "count": 0}

    return {"resources": resources_list, "count": len(resources_list)}


def _format_resource_content(raw: str, fmt: str) -> str:
    """Reformat a resource result string by format.

    If the content is JSON, parse and reformat (markdown or pretty-printed).
    Otherwise return unchanged for all formats.
    """
    if fmt == "raw":
        return raw
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw

    if fmt == "json":
        return json.dumps(data, indent=2)

    if fmt == "markdown":
        return _format_as_markdown(data, None)

    return raw


async def _mcp_read_resource_impl(ctx: Context, uri: str) -> str:
    """Implementation of read_resource.

    Args:
        ctx: FastMCP Context object (injected automatically)
        uri: The resource URI to read

    Returns:
        The resource content as a string

    Raises:
        ValueError: If the resource is not found or cannot be read
    """
    try:
        # ctx.read_resource returns a ResourceResult (FastMCP 3.x)
        result = await ctx.read_resource(uri)
        contents = result.contents
        return _extract_resource_content(contents, uri)
    except Exception as e:
        logger.exception("Failed to read resource %s", uri)
        msg = f"Error reading resource '{uri}': {type(e).__name__}: {e}"
        raise ValueError(msg) from e


def register_mcp_resource_tools(mcp: FastMCP) -> None:
    """Register MCP resource access tools with the server.

    These tools allow agents to interact with the MCP resource system directly.

    Args:
        mcp: The FastMCP server instance
    """

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "resources": {"type": "array"},
                    "count": {"type": "integer"},
                },
            },
        },
    })
    async def list_resources(
        format: str = "markdown",
        tag: str = "",
        type: str = "",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        """List all available MCP resources.

        This tool discovers all registered MCP resources and resource templates (parameterized URIs)
        available from the server. Use this for data discovery -- find what information is available
        and how to access it.

        For tool discovery (finding what actions you can perform), use search_tools instead.
        For natural-language resource discovery, use search_resources instead.

        Resources come in two types:
        - **resource**: Concrete resources with fixed URIs (e.g., `gitea://version`)
        - **template**: Parameterized URI templates requiring substitution (e.g., `gitea://repos/{owner}/{repo}`)

        ## Parameters

        - ``format``: Output format -- ``markdown`` (default), ``json``, or ``raw``.
        - ``tag``: Optional. Filter by tag name (e.g., ``"wrapper"``, ``"repository"``, ``"issue"``).
        - ``type``: Optional. Filter by resource type (``"resource"`` or ``"template"``).

        ## Return Structure

        Returns a dictionary with two keys:
        - `resources`: List of resource metadata dictionaries
        - `count`: Total number of resources and templates

        Each resource dictionary contains:
        - `uri`: The resource URI (may be a template with `{param}` placeholders)
        - `name`: Human-readable name
        - `description`: Description of what the resource provides
        - `mimeType`: MIME type of the content (e.g., "text/markdown", "application/json")
        - `type`: Either "resource" or "template"
        - `tags`: List of tags categorizing the resource (e.g., ["repository", "wrapper"])
        - `required_scope`: Token scope required to access this resource (e.g., "read:repository"),
          or `null` if no specific scope is required

        ## Usage Example

        ```python
        # Agent pattern: discover then read
        result = await list_resources()
        print(f"Found {result['count']} resources")

        for resource in result['resources']:
            print(f"- {resource['uri']} ({resource['mimeType']})")

            # Example: read a repository resource
            if 'repos/{owner}/{repo}' in resource['uri']:
                content = await read_resource(uri="gitea://repos/owner/repo")
                print(content)
        ```

        ## Notes

        - Templates require parameter substitution before calling `read_resource`
        - Check the `tags` field to understand resource categories:
          - `wrapper`: User-friendly formatted content (Markdown)
          - `raw`: Raw JSON from API
          - `api`: Auto-generated from OpenAPI spec
        - The `mimeType` hints at the content format you'll receive
        - The `required_scope` field tells you what Gitea token scope is needed:
          - `"read:repository"` - needs read access to repositories
          - `"read:issue"` - needs read access to issues
          - `null` - requires no specific scope (public info)
        - Use the `format` parameter to control output: ``format=markdown`` (default),
          ``format=json``, or ``format=raw``.

        Returns:
            Dictionary with 'resources' key containing a list of resource info:
            [
                {
                    "uri": "gitea://repos/{owner}/{repo}",
                    "name": "Repository",
                    "description": "Get full repository metadata",
                    "mimeType": "text/markdown",
                    "type": "template",
                    "tags": ["wrapper", "repository"],
                    "required_scope": "read:repository"
                },
                ...
            ]
        """
        raw = await _mcp_list_resources_impl(ctx)
        if tag:
            raw["resources"] = [r for r in raw["resources"] if tag in r.get("tags", [])]
        if type:
            raw["resources"] = [r for r in raw["resources"] if r.get("type", "") == type]
        raw["count"] = len(raw["resources"])
        if format == "raw":
            return ToolResult(structured_content={"result": raw})
        content = json.dumps(raw, indent=2) if format == "json" else _format_as_markdown(raw, None)
        return ToolResult(
            content=[TextContent(type="text", text=content)],
            structured_content={"result": raw},
        )

    @mcp.tool()
    async def read_resource(
        uri: str,
        format: str = "markdown",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        """Read the content of an MCP resource by URI.

        Fetches the resource from the server's resource registry and returns its
        content. Works with both static resources and parameterized
        resource templates.

        ## Parameter: uri

        The resource URI to read. This can be:
        - A concrete URI: `gitea://version`, `gitea://repos/owner/repo`
        - A template with parameters substituted: `gitea://repos/{owner}/{repo}` → `gitea://repos/mcp-server/gitea-mcp-server`

        URI format: `gitea://<path>` where path follows the Gitea API structure.

        ## Parameter: format

        Output format:
        - ``markdown`` (default): schema-aware Markdown with tables and sections (for JSON resources).
        - ``raw``: return the resource content exactly as stored.
        - ``json``: pretty-printed JSON (for JSON resources).

        Non-JSON resources (markdown, text, binary) are returned unchanged in all formats
        and delivered as raw text (not JSON-wrapped).

        ## Return Value

        For JSON resources (auto-generated, ``token/scopes``), returns structured content
        wrapped in ``{"result": ...}``. For text/markdown resources, returns raw text
        content directly without JSON wrapping.

        ## Usage Examples

        ### Reading a static resource
        ```python
        version = await read_resource("gitea://version")
        print(f"Server version: {version}")
        ```

        ### Reading with different formats
        ```python
        repo = await read_resource("gitea://repos/owner/repo")
        repo_json = await read_resource("gitea://repos/owner/repo", format="json")
        repo_raw = await read_resource("gitea://repos/owner/repo", format="raw")
        ```

        ### Reading a parameterized template
        ```python
        # Discover available templates first
        resources = await list_resources()
        repo_template = next(r for r in resources['resources'] if r['uri'].endswith('repos/{owner}/{repo}'))

        # Substitute parameters
        uri = repo_template['uri'].format(owner='mcp-server', repo='gitea-mcp-server')
        content = await read_resource(uri)

        # Content is Markdown for wrapper resources
        print(content)  # Formatted markdown with repo details
        ```

        ### Batch reading multiple resources
        ```python
        # Read repository, issues, and releases
        repo_uri = "gitea://repos/owner/repo"
        issues_uri = "gitea://repos/owner/repo/issues"
        releases_uri = "gitea://repos/owner/repo/releases"

        repo_info = await read_resource(repo_uri)
        issues = await read_resource(issues_uri)
        releases = await read_resource(releases_uri)
        ```

        ## Error Handling

        Raises `ValueError` if:
        - Resource not found (404)
        - Missing required parameters in template URI
        - Network or API errors occur

        The error message includes the resource URI and failure reason.

        ## Best Practices

        1. **Always discover first**: Call `list_resources()` to see available URIs and their metadata
        2. **Check MIME type**: Use the `mimeType` field to anticipate content format
        3. **Use tags for filtering**: Filter resources by tags (e.g., "wrapper" for human-readable content)
        4. **Handle errors gracefully**: Wrap calls in try-except to handle missing resources or API failures
        5. **Cache when appropriate**: Resources have built-in caching; avoid repeated calls in tight loops
        6. **Use format parameter**: ``format=json`` for structured data extraction, ``format=markdown`` for readability
        7. **Text vs JSON**: Markdown and plain-text resources are returned as raw text;
           JSON resources are returned as ``{"result": ...}`` structured content

        Args:
            uri: The resource URI to read (e.g., "gitea://repos/mcp-server/gitea-mcp-server/readme")
            format: Output format -- ``markdown`` (default), ``raw``, or ``json``.

        Returns:
            Raw text content for text/markdown resources, or structured content
            for JSON resources.

        Raises:
            ValueError: If the resource is not found or cannot be read
        """
        result = await _mcp_read_resource_impl(ctx, uri)
        formatted = _format_resource_content(result, format)

        return ToolResult(
            content=[TextContent(type="text", text=formatted)],
            structured_content={"result": formatted},
        )

    def _extract_resource_text(entry: dict[str, Any]) -> str:
        """Build searchable text from a resource entry."""
        parts = [entry.get("name", "")]
        uri = entry.get("uri", "")
        if uri:
            parts.append(uri)
        desc = entry.get("description", "")
        if desc:
            parts.append(desc)
        for tag in entry.get("tags", []):
            parts.append(tag)
        return " ".join(parts)

    @mcp.tool(output_schema={
        "type": "object",
        "properties": {
            "result": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string"},
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "mimeType": {"type": "string"},
                        "type": {"type": "string"},
                        "tags": {"type": "array"},
                        "required_scope": {"oneOf": [{"type": "string"}, {"type": "null"}]},
                    },
                },
                "description": "Matching resource definitions ranked by relevance",
            },
        },
    })
    async def search_resources(
        query: str,
        format: str = "markdown",
        ctx: Context = CurrentContext(),
    ) -> ToolResult:
        """Search MCP resources by natural language query.

        Uses BM25 ranking to find the most relevant resources matching your query.
        Searches across resource URI, name, description, and tags.

        Use this when you know what kind of information you want but not the
        exact resource URI. For an exhaustive listing, use list_resources instead.

        ## Parameters

        - ``query``: Natural language search query (e.g., "list issues in a repo",
          "get user profile", "pull request reviews")
        - ``format``: Output format -- ``markdown`` (default), ``json``, or ``raw``.

        ## Return Value

        A ranked list of matching resource definitions, each containing:
        - ``uri``: Resource URI (may be a template with ``{param}`` placeholders)
        - ``name``: Human-readable name
        - ``description``: Description of what the resource provides
        - ``mimeType``: MIME type of the content
        - ``type``: Either ``"resource"`` or ``"template"``
        - ``tags``: List of categorisation tags

        Returns at most 10 results, ranked by relevance.

        ## Usage

        ```python
        # Find resources related to pull requests
        results = await search_resources("pull request reviews")
        for r in results:
            print(f"{r['uri']} -- {r['description']}")

        # Search by category
        results = await search_resources("repository languages")
        ```
        """
        raw = await _mcp_list_resources_impl(ctx)
        if not raw["resources"]:
            return ToolResult(structured_content={"result": []})
        texts = [_extract_resource_text(r) for r in raw["resources"]]
        engine = BM25SearchEngine()
        indices = engine.search(texts, query, 10)
        results = [raw["resources"][i] for i in indices]
        if format == "raw":
            return ToolResult(structured_content={"result": results})
        serialized = json.dumps(results, indent=2) if format == "json" else _format_as_markdown(results, None)
        return ToolResult(
            content=[TextContent(type="text", text=serialized)],
            structured_content={"result": results},
        )

    @mcp.resource(
        uri="gitea://tool/{name}/schema",
        name="Tool Schema",
        description="Get the full tool schema for a registered tool by name. "
        "Use after search_tools to inspect parameter details and see an output example.",
        mime_type="application/json",
    )
    async def tool_schema_resource(name: str, ctx: Context = CurrentContext()) -> str:
        """Get the full tool schema for a registered tool by name.

        Call this after search_tools when you need full parameter types,
        an output example, annotations, or tags for a specific tool.

        Typical workflow:
        1. search_tools -- discover available tools (name + description)
        2. tool/{name}/schema -- get full schema for a specific tool
        3. call_tool -- execute the tool with proper arguments

        Args:
            name: The tool name (including any namespace prefix)

        Returns:
            JSON string with the full tool schema (parameters, output_example, annotations, etc.)

        Raises:
            ValueError: If the tool is not found
        """
        tool = await ctx.fastmcp.get_tool(name)
        if tool is None:
            msg = f"Tool '{name}' not found"
            raise ValueError(msg)
        data: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.parameters,
        }
        if tool.output_schema is not None:
            inner = tool.output_schema.get("properties", {}).get("result", {})
            data["output_example"] = _schema_to_example(inner)
        if tool.tags:
            data["tags"] = list(tool.tags)
        if tool.version:
            data["version"] = tool.version
        return json.dumps(data, indent=2)

    logger.info("Registered MCP resource tools: list_resources, read_resource, search_resources, tool_schema_resource")
