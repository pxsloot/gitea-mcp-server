"""Exclusion pattern matching utilities.

Provides:
- ``matches_pattern`` / ``matches_any`` - match a component name or tags
  against exclude/include patterns.

Pattern types (matched against component name or tags):
    - ``exact_name``             - exact match on component name
    - ``glob_pattern``           - fnmatch glob match on component name
    - ``tag:tagname``            - match on component tags

Include overrides exclude: if a component matches any include pattern,
it passes through even if it also matches an exclude pattern.

Note:
    This module only supplies the matching primitives.  Config loading lives
    in ``spec_loader.py`` (``load_exclusion_config``).  Filtering happens at
    spec-prep time via ``route_map_fn`` — see ``spec_loader.load_and_convert_spec``
    and ``mcp_builder.create_openapi_provider``.
"""

import logging
from fnmatch import fnmatch

logger = logging.getLogger(__name__)

_TAG_PREFIX = "tag:"


def _is_tag_pattern(pattern: str) -> bool:
    return pattern.startswith(_TAG_PREFIX)


def matches_pattern(name: str, tags: set[str], pattern: str, tool_prefix: str = "") -> bool:
    if _is_tag_pattern(pattern):
        tag_name = pattern[len(_TAG_PREFIX):]
        return tag_name in tags
    if tool_prefix and fnmatch(f"{tool_prefix}{name}", pattern):
        return True
    return fnmatch(name, pattern)


def matches_any(name: str, tags: set[str], patterns: list[str], tool_prefix: str = "") -> bool:
    return any(matches_pattern(name, tags, p, tool_prefix) for p in patterns)


__all__ = ["matches_any", "matches_pattern"]
