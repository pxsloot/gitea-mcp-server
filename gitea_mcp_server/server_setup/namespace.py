"""Gitea namespace transform that namespaces tools/prompts but not resources.

Resources already carry the gitea namespace via their ``gitea://`` scheme,
so injecting an additional ``/gitea/`` path segment (as FastMCP's built-in
``Namespace`` does) creates redundant double-namespacing like
``gitea://gitea/repos/{owner}/{repo}``.

This transform only prefixes tool and prompt names (e.g. ``create_issue`` →
``gitea_create_issue``).  Resource URIs are passed through unchanged.
"""

from collections.abc import Sequence

from fastmcp.resources import Resource
from fastmcp.resources.template import ResourceTemplate
from fastmcp.server.transforms.namespace import (
    GetResourceNext,
    GetResourceTemplateNext,
    Namespace,
)
from fastmcp.utilities.versioning import VersionSpec


class GiteaNamespace(Namespace):
    """Namespace transform that only prefixes tool/prompt names.

    Resource URIs are passed through unchanged because the ``gitea://`` scheme
    already provides the namespace.
    """

    # ------------------------------------------------------------------ #
    # Resources — pass through unchanged (already namespaced via scheme)
    # ------------------------------------------------------------------ #

    async def list_resources(
        self, resources: Sequence[Resource]
    ) -> Sequence[Resource]:
        return resources

    async def get_resource(
        self,
        uri: str,
        call_next: GetResourceNext,
        *,
        version: VersionSpec | None = None,
    ) -> Resource | None:
        return await call_next(uri, version=version)

    async def list_resource_templates(
        self, templates: Sequence[ResourceTemplate]
    ) -> Sequence[ResourceTemplate]:
        return templates

    async def get_resource_template(
        self,
        uri: str,
        call_next: GetResourceTemplateNext,
        *,
        version: VersionSpec | None = None,
    ) -> ResourceTemplate | None:
        return await call_next(uri, version=version)
