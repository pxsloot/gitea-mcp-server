"""FastMCP Transform for label validation and conversion.

Extracts label handling from the monolithic ``_ToolWrappingTransform``
into its own FastMCP ``Transform``, working *with* the framework.

The transform is registered on the OpenAPI provider via
``provider.add_transform()`` and runs *after* argument validation but
*before* the HTTP call to Gitea.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.telemetry import get_tracer
from fastmcp.tools.base import Tool, ToolResult

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.label_service import LabelService

if TYPE_CHECKING:
    from gitea_mcp_server.client import GiteaClient

logger = logging.getLogger(__name__)


class LabelTransform(Transform):
    """Transform that validates and converts label arguments before execution.

    Intercepts tools whose ``_customization.has_labels`` metadata is set,
    wrapping their ``run()`` with a call to
    ``LabelService.validate_and_convert()``.

    Designed to be registered as the *innermost* provider-level transform,
    so that label conversion happens after virtual-param extraction and
    argument validation (from outer transforms) but before the HTTP call.
    """

    def __init__(
        self,
        label_service: LabelService,
        gitea_client: "GiteaClient | None" = None,
    ) -> None:
        """Initialize LabelTransform.

        Args:
            label_service: Shared ``LabelService`` instance.
            gitea_client: GiteaClient for API calls on cache miss.
                If ``None``, label conversion is silently skipped.
        """
        self._label_service = label_service
        self._gitea_client = gitea_client

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Pass through — metadata is already set during :func:`_customize_metadata`."""
        return tools

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: Any = None,
    ) -> Tool | None:
        """Get a tool, wrapping ``run()`` with label conversion if needed.

        Args:
            name: Tool name.
            call_next: Callback to invoke the next (inner) transform.
            version: Optional version specifier.

        Returns:
            The tool, with ``run()`` wrapped if it has label parameters,
            or ``None`` if the tool does not exist.
        """
        tool = await call_next(name, version=version)
        if tool is None:
            return None

        if not self._should_wrap(tool):
            return tool

        return await self._wrap_tool(tool)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_wrap(self, tool: Tool) -> bool:
        """Check whether the tool has label parameters that need conversion.

        Args:
            tool: The tool to check.

        Returns:
            ``True`` if the tool's ``_customization.has_labels`` is set.
        """
        meta = tool.meta or {}
        customization = meta.get("_customization", {})
        return bool(customization.get("has_labels", False))

    async def _wrap_tool(self, tool: Tool) -> Tool:
        """Wrap a tool's ``run()`` with label conversion logic.

        Args:
            tool: The tool to wrap (must have ``_customization.has_labels``).

        Returns:
            A new tool whose ``run()`` converts labels before delegating
            to the original ``run()``.
        """
        original_run = tool.run
        label_service = self._label_service
        gitea_client = self._gitea_client

        tracer = get_tracer()

        async def label_transform_fn(**kwargs: Any) -> ToolResult:
            with tracer.start_as_current_span(f"{tool.name}.validate_labels") as span:
                span.set_attribute("tool.name", tool.name)
                span.set_attribute("labels.has_labels", True)

                # Count label types for observability
                raw_labels = kwargs.get("labels")
                if isinstance(raw_labels, list):
                    int_count = sum(1 for item in raw_labels if isinstance(item, int))
                    str_count = sum(1 for item in raw_labels if isinstance(item, str))
                    span.set_attribute("label.count", len(raw_labels))
                    span.set_attribute("label.integers", int_count)
                    span.set_attribute("label.strings", str_count)

                try:
                    await _convert_labels_inline(
                        kwargs,
                        label_service,
                        gitea_client,
                    )
                except ValidationError as e:
                    span.set_attribute("error", True)
                    span.set_attribute("error.message", str(e))
                    raise ValueError(str(e)) from e
            return await original_run(kwargs)

        # Preserve all existing metadata — title, tags, description,
        # output_schema, meta — so outer transforms see the same shape.
        return Tool.from_tool(
            tool,
            title=getattr(tool.annotations, "title", None) if tool.annotations else None,
            tags=tool.tags,
            description=tool.description,
            transform_fn=label_transform_fn,
            output_schema=tool.output_schema,
            meta=tool.meta,
        )


async def _convert_labels_inline(
    kwargs: dict[str, Any],
    label_service: LabelService,
    gitea_client: "GiteaClient | None" = None,
) -> None:
    """Convert label strings/ints to validated integer IDs in-place.

    Extracted from the old ``_convert_labels`` adapter in ``tools/labels.py``
    for use inside the transform's ``transform_fn`` without circular imports.

    Args:
        kwargs: The tool's keyword arguments (mutated in-place).
        label_service: The ``LabelService`` instance.
        gitea_client: GiteaClient for API calls.  If ``None``, conversion
            is silently skipped.

    Raises:
        ValidationError: If any label name or ID is unknown.
    """
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
        labels,
        owner,
        repo,
        gitea_client,
    )
    kwargs["labels"] = converted


__all__ = [
    "LabelTransform",
    "_convert_labels_inline",
]
