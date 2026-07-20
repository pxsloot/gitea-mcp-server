"""Exclusion config loading and pattern matching.

Provides:
- ``load_exclusion_config`` - loads exclusion rules from a YAML config file.
- ``matches_pattern`` / ``matches_any`` - match a component name or tags
  against exclude/include patterns.

Pattern types (matched against component name or tags):
    - ``exact_name``             - exact match on component name
    - ``glob_pattern``           - fnmatch glob match on component name
    - ``tag:tagname``            - match on component tags

Include overrides exclude: if a component matches any include pattern,
it passes through even if it also matches an exclude pattern.

Note:
    The actual *filtering* of tools/resources now happens at spec-prep time
    via ``route_map_fn`` (see ``spec_loader.load_and_convert_spec`` and
    ``mcp_builder.create_openapi_provider``).  This module only supplies the
    config-loading and matching primitives consumed by that spec-level step.
    The old ``ExclusionTransform`` runtime transform was removed in Phase 2 of
    the Spec-Level Filtering milestone (#472).
"""

import logging
from fnmatch import fnmatch
from pathlib import Path

import yaml

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


def load_exclusion_config(config_path: str | None) -> dict[str, list[str]]:
    """Load exclusion rules from a YAML config file.

    Args:
        config_path: Path to the YAML config file, or None to skip.

    Returns:
        Dict with ``exclude`` and ``include`` keys, each a list of pattern strings.
        Returns empty lists on any error or missing file.
    """
    if config_path is None:
        return {"exclude": [], "include": []}

    path = Path(config_path)
    if not path.exists():
        logger.info("Exclusion config not found: %s", config_path)
        return {"exclude": [], "include": []}

    try:
        with path.open() as f:
            data = yaml.safe_load(f) or {}
        return {
            "exclude": data.get("exclude", []) or [],
            "include": data.get("include", []) or [],
        }
    except Exception:
        logger.exception("Failed to load exclusion config from %s", config_path)
        return {"exclude": [], "include": []}


__all__ = ["load_exclusion_config", "matches_any", "matches_pattern"]
