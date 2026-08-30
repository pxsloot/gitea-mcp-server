"""Resource content helpers.

Resources are passive data sources: every handler returns raw data (a JSON
string or plain text) plus content-level metadata (``response_schema``,
``format_hint``, extra context).  No formatting happens here — the
``read_resource`` executor (``tools/mcp_tools.py``) reads the raw content,
extracts the metadata, and hands both to the single result pipeline
(``tools/result_pipeline.py``), which renders every tool and resource
through one display path.

This module retains the two helpers the executor needs:

Public functions:
    clean_resource_uri - re-exported from ``uri_utils.py``; strip ``{?query}``
        from URI templates
    extract_resource_content - extract text content from ResourceResult
"""

import logging
from typing import Any

from gitea_mcp_server.uri_utils import clean_resource_uri

logger = logging.getLogger(__name__)


def extract_resource_content(contents: list[Any] | None, uri: str) -> str:
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


__all__ = [
    "clean_resource_uri",
    "extract_resource_content",
]
