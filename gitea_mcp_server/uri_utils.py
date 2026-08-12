"""URI template utility functions.

Flat infrastructure module — no domain dependencies.  Provides helpers for
working with RFC 6570 URI templates used across the resource registration,
tool, and display layers.
"""

import re


def clean_resource_uri(uri: str) -> str:
    """Strip RFC 6570 form-style query parameters from a resource URI.

    Resource templates use ``{?param}`` syntax internally so FastMCP routes
    query-string parameters to the handler.  This function returns the base
    URI without ``{?param}`` suffixes:

    - For display: show a clean template without parameter noise — agents
      discover available optional parameters via ``optional_params`` metadata.
    - For skip-set normalization: custom resources register with suffixes
      (e.g. ``gitea://repos/{owner}/{repo}/issues{?state,type}``) while
      auto-generated resources build URIs from spec paths alone
      (``gitea://repos/{owner}/{repo}/issues``).  Stripping suffixes on
      both sides ensures they match.

    Example:
        ``gitea://repos/{owner}/{repo}/issues{?state}`` →
        ``gitea://repos/{owner}/{repo}/issues``

    Note:
        The regex only strips ``{?...}`` when it appears at the **end** of the
        URI (``$`` anchor).  This assumes query params are always the last
        segment in a URI template — a convention enforced by convention, not
        code.  If a future URI template places ``{?param}`` before trailing
        path segments, this function must be updated.

    Args:
        uri: The raw URI template.

    Returns:
        Cleaned URI with ``{?...}`` suffix removed.
    """
    return re.sub(r"\{\?[^}]+\}$", "", uri)


__all__ = [
    "clean_resource_uri",
]
