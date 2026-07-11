"""TypedDict models for structured output types.

All TypedDicts use ``total=False`` to match existing ``.get()`` guard
patterns throughout the codebase.  This is consistent with the convention
established in ``openapi_types.py``.

These types provide static type annotations with zero runtime overhead —
TypedDict is a dict at runtime, so no serialization round-trip is needed.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ToolSearchEntry(TypedDict, total=False):
    """A compact tool entry returned from ``search_tools`` and ``search``.

    ``score`` is the normalized relevance (0.0-1.0) for the query that
    produced this entry; 1.0 is the top match.
    """

    name: str
    description: str
    tags: list[str]
    annotations: dict[str, Any]
    score: float


class ResourceEntry(TypedDict, total=False):
    """A resource entry returned from ``list_resources`` and ``search_resources``.

    The ``type`` field is accessed via ``entry["type"]`` rather than
    ``entry.type`` to avoid shadowing the Python built-in.  This follows
    the same pattern as ``param["in"]`` in ``OpenAPIParameter``.
    """

    uri: str
    name: str
    description: str
    mimeType: str
    type: str  # "resource" or "template"; accessed via ["type"]
    tags: list[str]
    required_scope: str | None


class ResourceListing(TypedDict, total=False):
    """Top-level response from ``list_resources``."""

    resources: list[ResourceEntry]
    count: int


class DocEntry(TypedDict, total=False):
    """A workflow guide entry returned from ``search_docs``."""

    name: str
    title: str
    description: str
    tags: list[str]


class UnifiedSearchItem(TypedDict, total=False):
    """A single merged result from the unified ``search`` tool.

    The ``type`` field discriminates between ``"tool"``, ``"doc"``,
    and ``"resource"`` results.  It is accessed via ``entry["type"]``
    (same reasoning as ``ResourceEntry.type``).

    ``score`` is the normalized relevance (0.0-1.0) for the query that
    produced this item; 1.0 is the top match.
    """

    type: str  # "tool", "doc", or "resource"; accessed via ["type"]
    name: str
    description: str
    tags: list[str]
    access_uri: str
    uri: str
    title: str
    score: float


class ToolSchemaResult(TypedDict, total=False):
    """Full tool schema returned from ``tool_info``."""

    name: str
    description: str
    parameters: dict[str, Any]
    output_example: Any
    output_schema: dict[str, Any]
    annotations: dict[str, Any]
    tags: list[str]
    version: str


class SimpleStringResult(TypedDict, total=False):
    """Simple ``{"result": str}`` shape used by ``read_doc``, ``read_resource``, etc."""

    result: str


__all__ = [
    "DocEntry",
    "ResourceEntry",
    "ResourceListing",
    "SimpleStringResult",
    "ToolSchemaResult",
    "ToolSearchEntry",
    "UnifiedSearchItem",
]
