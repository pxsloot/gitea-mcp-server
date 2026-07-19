"""Exclusion transform - exclude/include tools and resources via config patterns.

Provides:
- ``ExclusionTransform`` - server-level ``Transform`` that filters tools,
  resources, and resource templates based on exclude/include patterns.
- ``load_exclusion_config`` - loads exclusion rules from a YAML config file.

Pattern types (matched against component name or tags):
    - ``exact_name``             - exact match on component name
    - ``glob_pattern``           - fnmatch glob match on component name
    - ``tag:tagname``            - match on component tags

Include overrides exclude: if a component matches any include pattern,
it passes through even if it also matches an exclude pattern.
"""

import logging
from collections.abc import Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

import yaml
from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.transforms import Transform
from fastmcp.tools.base import Tool

logger = logging.getLogger(__name__)

_TAG_PREFIX = "tag:"


def _is_tag_pattern(pattern: str) -> bool:
    return pattern.startswith(_TAG_PREFIX)


def matches_pattern(name: str, tags: set[str], pattern: str, tool_prefix: str = "") -> bool:
    if _is_tag_pattern(pattern):
        tag_name = pattern[len(_TAG_PREFIX) :]
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


class ExclusionTransform(Transform):
    """Transform that filters tools/resources by exclude/include patterns.

    Include overrides exclude: if a component matches any include pattern,
    it passes through regardless of exclude patterns.

    Attributes:
        exclude: List of patterns - components matching these are removed.
        include: List of patterns - components matching these are kept
            even if they match an exclude pattern.
    """

    def __init__(
        self,
        exclude: list[str] | None = None,
        include: list[str] | None = None,
        tool_prefix: str = "",
    ) -> None:
        self._exclude = exclude or []
        self._include = include or []
        self._tool_prefix = tool_prefix

    # -- internal helpers ---------------------------------------------------

    def _is_allowed(self, name: str, tags: set[str] | None = None) -> bool:
        tags = tags or set()
        return matches_any(name, tags, self._include, self._tool_prefix) or not matches_any(
            name, tags, self._exclude, self._tool_prefix
        )

    # -- tools --------------------------------------------------------------

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [t for t in tools if self._is_allowed(t.name, t.tags)]

    async def get_tool(self, name: str, call_next: Any, *, version: Any = None) -> Tool | None:
        tool = cast("Tool | None", await call_next(name, version=version))
        if tool is None:
            return None
        if not self._is_allowed(tool.name, tool.tags):
            return None
        return tool

    # -- resources ----------------------------------------------------------

    async def list_resources(self, resources: Sequence[Resource]) -> Sequence[Resource]:
        return [r for r in resources if self._is_allowed(r.name, r.tags)]

    async def get_resource(
        self, uri: str, call_next: Any, *, version: Any = None
    ) -> Resource | None:
        resource = cast("Resource | None", await call_next(uri, version=version))
        if resource is None:
            return None
        if not self._is_allowed(resource.name, resource.tags):
            return None
        return resource

    # -- resource templates -------------------------------------------------

    async def list_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        return [t for t in templates if self._is_allowed(t.name, t.tags)]

    async def get_resource_template(
        self, uri: str, call_next: Any, *, version: Any = None
    ) -> ResourceTemplate | None:
        template = cast("ResourceTemplate | None", await call_next(uri, version=version))
        if template is None:
            return None
        if not self._is_allowed(template.name, template.tags):
            return None
        return template


__all__ = ["ExclusionTransform", "load_exclusion_config", "matches_any", "matches_pattern"]
