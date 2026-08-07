"""MCP Resource Access Tools.

These tools allow agents to discover and read MCP resources that are registered
with the server. They bridge the gap between the resource protocol and the agent's
toolset.

Tool list:
- list_resources: List all available MCP resources
- read_resource: Read a resource by its URI
- gitea://tool/{name}/schema: Resource for full tool schema by name

``search_resources`` is registered in ``tools/search.py`` alongside
``search_tools`` via ``register_synthetic_tools()``.

Display pipeline lives in ``tools/resource_display.py`` — this module imports
from it rather than duplicating formatting logic.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from gitea_mcp_server.format import (
    _format_paginated_result,
    decode_base64_content,
)
from gitea_mcp_server.models import ResourceEntry, ResourceListing
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.tools.customize import synthetic_annotations
from gitea_mcp_server.tools.examples import _serialize_tool_schema
from gitea_mcp_server.tools.resource_display import (
    _clean_resource_uri,
    _extract_resource_content,
    _format_resource_content,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Resource listing implementation (used by list_resources tool,
# imported by tools/unified_search.py and tools/search.py)
# ============================================================================


async def _mcp_list_resources_impl(ctx: Context) -> ResourceListing:
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
            base: ResourceEntry,
            meta: dict[str, Any] | None,
        ) -> ResourceEntry:
            """Add metadata fields to a resource entry from the resource's meta dict.

            Extracts discoverable fields that agents can inspect before calling
            ``read_resource``: ``required_scope``, ``optional_params``,
            ``size_hint``, and ``default_detail``.  All are optional — missing
            fields are simply absent from the entry.
            """
            base["required_scope"] = meta.get("required_scope") if meta else None
            if meta:
                optional_params = meta.get("optional_params")
                if optional_params:
                    base["optional_params"] = optional_params
                size_hint = meta.get("size_hint")
                if size_hint:
                    base["size_hint"] = size_hint
                default_detail = meta.get("default_detail")
                if default_detail:
                    base["default_detail"] = default_detail
            return base

        # Process concrete resources
        # FastMCP auto-populates name from function name when not explicitly set:
        # @mcp.resource("uri://foo")  -> name="foo" (function name)
        # @mcp.resource("uri://bar", name="custom") -> name="custom"
        for resource in resources:
            entry = _build_resource_entry(
                ResourceEntry(
                    uri=_clean_resource_uri(str(resource.uri)),
                    name=resource.name,
                    description=resource.description or "",
                    mimeType=resource.mime_type or "text/plain",
                    type="resource",
                    tags=list(resource.tags)
                    if hasattr(resource, "tags") and resource.tags
                    else [],
                ),
                getattr(resource, "meta", None),
            )
            resources_list.append(entry)

        # Process resource templates
        for template in templates:
            entry = _build_resource_entry(
                ResourceEntry(
                    uri=_clean_resource_uri(str(template.uri_template)),
                    name=template.name,
                    description=template.description or "",
                    mimeType=template.mime_type or "text/plain",
                    type="template",
                    tags=list(template.tags)
                    if hasattr(template, "tags") and template.tags
                    else [],
                ),
                getattr(template, "meta", None),
            )
            resources_list.append(entry)
    except (AttributeError, TypeError):
        logger.exception("Error listing resources")
        return ResourceListing(resources=[], count=0)

    return ResourceListing(resources=resources_list, count=len(resources_list))


# ============================================================================
# Resource read implementation
# ============================================================================


# Known meta keys that carry display pipeline metadata rather than
# extra context for formatters.  Everything else in meta is forwarded
# as extra to the display pipeline.
_KNOWN_META_KEYS: frozenset[str] = frozenset({"response_schema", "format_hint"})


def _extract_extra_meta(meta: dict[str, Any]) -> dict[str, Any] | None:
    """Extract non-known keys from a resource meta dict.

    Strips keys that belong to the display pipeline
    (``response_schema``, ``format_hint``) and returns everything else as
    extra context for formatters.  Returns ``None`` when no extra keys
    exist, matching the ``or None`` idiom used throughout the codebase.
    """
    extra = {k: v for k, v in meta.items() if k not in _KNOWN_META_KEYS}
    return extra or None


async def _mcp_read_resource_impl(
    ctx: Context, uri: str,
) -> tuple[Any, dict[str, Any] | None, str | None, dict[str, Any] | None]:
    """Read a resource and return (content, schema, format_hint, extra).

    The content is the raw string from the resource handler.  Schema,
    format_hint, and extra are extracted from ``ResourceContent.meta`` and
    passed to the display pipeline.

    Args:
        ctx: FastMCP Context object (injected automatically).
        uri: The resource URI to read.

    Returns:
        Tuple of ``(content, schema, format_hint, extra)``.
    """
    try:
        # ctx.read_resource returns a ResourceResult (FastMCP 3.x)
        result = await ctx.read_resource(uri)
        contents = result.contents
        raw = _extract_resource_content(contents, uri)

        # Extract display metadata from the first content's meta.
        schema: dict[str, Any] | None = None
        format_hint: str | None = None
        extra: dict[str, Any] | None = None
        if contents and hasattr(contents[0], "meta") and contents[0].meta:
            meta = contents[0].meta
            schema = meta.get("response_schema")
            format_hint = meta.get("format_hint")
            # Everything except response_schema and format_hint is extra context
            extra = _extract_extra_meta(meta)
    except Exception as e:
        logger.exception("Failed to read resource %s", uri)
        msg = f"Error reading resource '{uri}': {type(e).__name__}: {e}"
        raise ValueError(msg) from e
    else:
        return raw, schema, format_hint, extra


# ============================================================================
# Output schemas
# ============================================================================

_LIST_RESOURCES_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "result": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "Resource URI (may be a template)"},
                    "name": {"type": "string", "description": "Human-readable name"},
                    "description": {"type": "string", "description": "Description of the resource"},
                    "mimeType": {"type": "string", "description": "MIME type (e.g. text/markdown)"},
                    "type": {"type": "string", "description": "resource or template"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categorization tags",
                    },
                    "required_scope": {
                        "oneOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Required token scope or null",
                    },
                    "optional_params": {
                        "oneOf": [
                            {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "type": {"type": "string"},
                                        "values": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                },
                            },
                            {"type": "null"},
                        ],
                        "description": "Optional query parameters the resource accepts",
                    },
                    "size_hint": {
                        "oneOf": [
                            {"type": "string", "enum": ["tiny", "small", "medium", "large"]},
                            {"type": "null"},
                        ],
                        "description": "Estimated token cost (tiny/small/medium/large)",
                    },
                    "default_detail": {
                        "oneOf": [
                            {"type": "string", "enum": ["full", "concise"]},
                            {"type": "null"},
                        ],
                        "description": "Recommended detail level (full/concise)",
                    },
                },
            },
            "description": "List of resource metadata entries",
            "example": [
                {
                    "uri": "gitea://repos/{owner}/{repo}",
                    "name": "Repository",
                    "description": "Get full repository metadata",
                    "mimeType": "application/json",
                    "type": "template",
                    "tags": ["wrapper", "repository"],
                    "required_scope": "read:repository",
                },
            ],
        },
    },
}

_READ_RESOURCE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "result": {
            "type": "string",
            "description": "Resource content as text (markdown, JSON, or plain text)",
            "example": '{\n  "id": 1,\n  "name": "example-repo",\n  "description": "A sample repository"\n}',
        },
    },
}


# ============================================================================
# Tools
# ============================================================================


async def _list_resources_tool(  # noqa: PLR0913 - ctx is FastMCP DI plumbing
    format: str = "markdown",
    tag: str = "",
    type: str = "",
    page: int = 1,
    limit: int = 10,
    fetch_all: bool = False,
    detail: str = "full",
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
    - ``page``: Page number (1-based, default 1).
    - ``limit``: Maximum results per page (1-100, default 10).
    - ``fetch_all``: When true, return all resources instead of a single page.
      Since data is in-memory, this simply skips the page/limit slice.
    - ``detail``: Markdown rendering depth -- ``"full"`` (default) for complete
      expansion, ``"concise"`` for compact summaries with collapsed nesting.

    ## Return Structure

    Returns a flat list of resource metadata dictionaries, with pagination
    metadata (``total_count``, ``has_more``, ``next_offset``) in
    ``structured_content`` — the same contract as ``search_tools``,
    ``search_resources``, ``search_docs``, and ``search``.

    Each resource dictionary contains:
    - `uri`: The resource URI (may be a template with `{param}` placeholders)
    - `name`: Human-readable name
    - `description`: Description of what the resource provides
    - `mimeType`: MIME type of the content (e.g., "text/markdown", "application/json")
    - `type`: Either "resource" or "template"
    - `tags`: List of tags categorizing the resource (e.g., ["repository", "wrapper"])
    - `required_scope`: Token scope required to access this resource (e.g., "read:repository"),
      or `null` if no specific scope is required
    - `optional_params`: Optional list of dicts describing available query parameters
      (e.g., ``[{"name": "state", "type": "string", "values": ["open", "closed"]}]``),
      or absent if the resource has no optional parameters
    - `size_hint`: Estimated token cost — ``"tiny"`` | ``"small"`` | ``"medium"`` | ``"large"``
      (absent if not set). Check this before reading expensive resources.
    - `default_detail`: Recommended detail level — ``"full"`` | ``"concise"``
      (absent if not set)

    ## Usage Example

    ```python
    # Agent pattern: discover then read
    result = await list_resources(fetch_all=True)

    for resource in result['result']:
        print(f"- {resource['uri']} ({resource['mimeType']})")

        # Example: read a repository resource
        if 'repos/{owner}/{repo}' in resource['uri']:
            content = await read_resource(uri="gitea://repos/owner/repo")
            print(content)
    ```

    ## Notes

    - Templates require parameter substitution before calling `read_resource`
    - Check the `tags` field to understand resource categories:
      - `wrapper`: User-friendly content (raw JSON with display metadata; rendered as Markdown by default via the display pipeline)
      - `raw`: Raw JSON from API
      - `api`: Auto-generated from OpenAPI spec
    - The `mimeType` reflects the stored content type (``application/json`` for
      wrapper resources, ``text/plain`` for raw text).  Use the ``format`` parameter
      to control display format — ``format=markdown`` (default) renders JSON data
      through the display pipeline, ``format=json`` returns the raw JSON,
      ``format=raw`` bypasses all formatting.
    - The `required_scope` field tells you what Gitea token scope is needed:
      - `"read:repository"` - needs read access to repositories
      - `"read:issue"` - needs read access to issues
      - `null` - requires no specific scope (public info)
    - The `size_hint` field estimates token cost. For ``"large"`` resources
      (e.g., ``pulls``, ``issues``), pass ``detail="concise"`` to save tokens.
    - The `default_detail` field recommends a detail level. Respect it unless
      you specifically need full expansion.
    - Use the `format` parameter to control output: ``format=markdown`` (default),
      ``format=json``, or ``format=raw``.

    Returns:
        A flat list of resource info dicts with pagination metadata
        (``total_count``, ``has_more``, ``next_offset``) in
        ``structured_content``:
        [
            {
                "uri": "gitea://repos/{owner}/{repo}",
                "name": "Repository",
                "description": "Get full repository metadata",
                "mimeType": "application/json",
                "type": "template",
                "tags": ["wrapper", "repository"],
                "required_scope": "read:repository",
                "size_hint": "medium",
                "default_detail": "full"
            },
            ...
        ]
    """
    raw = await _mcp_list_resources_impl(ctx)

    # Apply filters
    if tag:
        raw["resources"] = [r for r in raw["resources"] if tag in r.get("tags", [])]
    if type:
        raw["resources"] = [r for r in raw["resources"] if r.get("type", "") == type]

    all_resources = raw["resources"]
    total_count = len(all_resources)

    if total_count == 0:
        return ToolResult(
            content=[TextContent(type="text", text="No resources found.")],
            structured_content={"result": [], "total_count": 0},
        )

    return _format_paginated_result(
        all_resources, total_count, format, page, limit, fetch_all,
        detail=detail,
    )


async def _maybe_decode_base64(raw: str) -> str:
    """Detect and decode base64-encoded Gitea ContentsResponse.

    Gitea's ``/repos/{owner}/{repo}/contents/{path}`` endpoint returns JSON
    with ``encoding: "base64"`` and ``content`` (base64-encoded string).
    This function detects that pattern in resource content and decodes it
    so the display pipeline receives plain text.

    Detection is at runtime via JSON parse — no spec dependency needed.
    Mirrors the spec-driven detection in autogen tools
    (``_ToolWrappingTransform._try_handle_text_response``).

    Args:
        raw: Raw resource content string from the resource handler.

    Returns:
        Decoded plain text if the content was a base64 ContentsResponse,
        otherwise the original ``raw`` string unchanged.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return await decode_base64_content(data)
    return raw


async def _read_resource_tool(
    uri: str,
    format: str = "markdown",
    detail: str = "full",
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
      Base64-encoded Gitea ContentsResponse is auto-decoded to plain text.
    - ``raw``: return the resource content exactly as returned by the resource handler.
      No transformation is applied — base64 content remains base64-encoded.
    - ``json``: pretty-printed JSON (for JSON resources). For non-JSON resources,
      wraps content in ``{"result": "..."}`` for consistent structured output.
      Base64-encoded Gitea ContentsResponse is auto-decoded to plain text.

    ## Parameter: detail

    Output detail level:
    - ``"full"`` (default): complete information, full object expansion.
    - ``"concise"``: compact view with collapsed nested objects. Affects both
      JSON and Markdown output. Schema-aware ``$ref`` collapse is applied
      when the resource carries a response schema.

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
    resources = await list_resources(fetch_all=True)
    repo_template = next(r for r in resources['result'] if r['uri'].endswith('repos/{owner}/{repo}'))

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
       JSON resources are returned as ``{"result": ...}`` structured content.
       Gitea ContentsResponse (base64-encoded file content) is auto-decoded to
       plain text at the tool layer for ``markdown`` and ``json`` formats;
       use ``format=raw`` to access the original base64-encoded JSON.

    Args:
        uri: The resource URI to read (e.g., "gitea://repos/mcp-server/gitea-mcp-server/readme")
        format: Output format -- ``markdown`` (default), ``raw``, or ``json``.

    Returns:
        Raw text content for text/markdown resources, or structured content
        for JSON resources.

    Raises:
        ValueError: If the resource is not found or cannot be read
    """
    raw, schema, format_hint, extra = await _mcp_read_resource_impl(ctx, uri)
    # Decode base64 ContentsResponse at the tool layer: resources are
    # pure data; the tool transforms for agent consumption — mirroring
    # how autogen tools decode in _pipeline_with_context.
    # Preserve raw for format=raw: agents requesting "raw" expect the
    # exact API response, not a transformed version.
    if format != "raw":
        raw = await _maybe_decode_base64(raw)
    formatted = _format_resource_content(
        raw, format, detail=detail,
        schema=schema, format_hint=format_hint, extra=extra,
    )

    return ToolResult(
        content=[TextContent(type="text", text=formatted)],
        structured_content={"result": formatted},
    )


# ============================================================================
# Tool schema resource handler
# ============================================================================


def _make_tool_schema_resource_handler(
    openapi_spec: OpenAPISpec | None = None,
) -> Callable[..., Awaitable[str]]:
    """Create the ``gitea://tool/{name}/schema`` resource handler.

    Uses closure-based dependency injection for the OpenAPI spec so the
    value is captured at registration time, avoiding runtime monkey-patching
    on function attributes.

    Args:
        openapi_spec: Post-conversion OpenAPI 3.1 spec, or ``None``.

    Returns:
        Async resource handler callable ``(name, ctx) -> str``.
    """
    async def _tool_schema_resource(name: str, ctx: Context = CurrentContext()) -> str:
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
            JSON string with the full tool schema (parameters, output_example,
            annotations, etc.)

        Raises:
            ValueError: If the tool is not found
        """
        tool = await ctx.fastmcp.get_tool(name)
        if tool is None:
            msg = f"Tool '{name}' not found"
            raise ValueError(msg)

        # Compact example (type names for $ref, no inlined nesting).
        # Bare $ref resolved one level via openapi_spec captured in the closure.
        data = dict(_serialize_tool_schema(tool, openapi_spec=openapi_spec))

        # Resource always includes the fully-resolved output_schema.
        if tool.output_schema is not None:
            data["output_schema"] = tool.output_schema

        return json.dumps(data, indent=2)

    return _tool_schema_resource


# ============================================================================
# Registration
# ============================================================================


def register_mcp_resource_tools(
    mcp: FastMCP,
    openapi_spec: OpenAPISpec | None = None,
) -> None:
    """Register MCP resource access tools with the server.

    These tools allow agents to interact with the MCP resource system directly.

    Args:
        mcp: The FastMCP server instance
        openapi_spec: Post-conversion OpenAPI 3.1 spec, used to resolve bare
            ``$ref`` in tool output examples.
    """
    mcp.tool(
        name="list_resources",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=False),
        output_schema=_LIST_RESOURCES_OUTPUT_SCHEMA,
    )(_list_resources_tool)

    mcp.tool(
        name="read_resource",
        tags={"synthetic"},
        annotations=synthetic_annotations(read_only=True, open_world=True),
        output_schema=_READ_RESOURCE_OUTPUT_SCHEMA,
    )(_read_resource_tool)

    mcp.resource(
        uri="gitea://tool/{name}/schema",
        name="Tool Schema",
        description="Get the full tool schema for a registered tool by name. "
        "Use after search_tools to inspect parameter details and see an output example.",
        mime_type="application/json",
    )(_make_tool_schema_resource_handler(openapi_spec))

    logger.info(
        "Registered MCP resource tools: list_resources, read_resource, tool_schema_resource"
    )


__all__ = [
    "_extract_extra_meta",
    "_make_tool_schema_resource_handler",
    "_maybe_decode_base64",
    "_mcp_list_resources_impl",
    "_mcp_read_resource_impl",
    "register_mcp_resource_tools",
]
