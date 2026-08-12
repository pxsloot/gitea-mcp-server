"""Auto-generated resources from OpenAPI GET endpoints.

Creates resources for all GET operations via the factory
(``make_api_resource``), delegating schema derivation, error handling,
response construction, and registration to the shared pipeline.  These
can be overridden by custom resources with the same URI.

The ``skip_uris`` for auto-generation is provided by the orchestrator
(``resource_setup.py``), which passes the return value of
``register_custom_resources()`` — the caller-owned set of URIs registered
via ``make_api_resource()`` with ``tracking_set``.  Auto resources are
*consumers* of this set, not producers — they call ``make_api_resource()``
without ``tracking_set``.  All custom resources that have API equivalents
in the spec are now registered via the factory, so no additional skip
set is needed.
"""

import logging
from typing import Any, cast

from fastmcp import FastMCP

from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.resources.factory import make_api_resource
from gitea_mcp_server.resources.scope import derive_required_scope
from gitea_mcp_server.uri_utils import clean_resource_uri

logger = logging.getLogger(__name__)


def _derive_resource_name(operation: dict[str, Any], path: str) -> str:
    """Derive a meaningful resource name from an OpenAPI operation."""
    operation_id = operation.get("operationId")
    if operation_id and operation_id.strip():
        name = operation_id.strip()
        result = ""
        for i, char in enumerate(name):
            if char.isupper():
                if i > 0 and (
                    name[i - 1].islower() or (i + 1 < len(name) and name[i + 1].islower())
                ):
                    result += "_"
                result += char.lower()
            else:
                result += char
        return result

    clean_path = path.strip("/")
    segments = [s for s in clean_path.split("/") if not (s.startswith("{") and s.endswith("}"))]
    if not segments:
        segments = [s.strip("{}") for s in clean_path.split("/") if s]
    return "_".join(segments) if segments else "resource"


def register_auto_generated_resources(
    mcp: FastMCP,
    gitea_client: GiteaClient,
    openapi_spec: OpenAPISpec,
    skip_uris: set[str] | None = None,
    filtered_tools_info: dict[str, Any] | None = None,
) -> None:
    """Auto-generate resources from GET endpoints via the factory.

    Each resource delegates to ``make_api_resource()``, which handles
    schema derivation, metadata, error handling, and registration.
    Custom resources registered via the factory are skipped (their URIs
    are in ``skip_uris``).

    Args:
        mcp: The FastMCP server instance.
        gitea_client: GiteaClient for API calls.
        openapi_spec: The OpenAPI specification dictionary.
        skip_uris: Set of URI templates to skip (custom resource overrides).
            The orchestrator (``resource_setup.py``) passes the snapshot
            returned by ``register_custom_resources()``.  When ``None``,
            defaults to an empty set (no URIs skipped).
        filtered_tools_info: Filter-prediction data from spec-level filtering.
            When provided, resources whose operationId appears in the ``filtered``
            dict are skipped -- they are scope-filtered, deprecated, or excluded by
            config.  ``None`` means no filtering is applied (all resources visible).
    """
    if skip_uris is None:
        skip_uris = set()

    # Normalize skip URIs: strip RFC 6570 {?query} suffixes so base
    # URIs match auto-generated URI templates.  Custom resources may
    # register with "gitea://repos/{owner}/{repo}/issues{?state,type}"
    # while auto resources build "gitea://repos/{owner}/{repo}/issues"
    # from the spec path alone.  Stripping suffixes on both sides
    # ensures the comparison is always on the base URI.
    #
    # This is a defense-in-depth complement to the factory's own
    # normalization in tracking_set.add() — auto.py normalizes its
    # input regardless of who provides the skip_uris set.
    normalized_skip: set[str] = {clean_resource_uri(u) for u in skip_uris}

    filtered: dict[str, Any] = {}
    if filtered_tools_info:
        filtered = filtered_tools_info.get("filtered", {})

    paths: dict[str, Any] = cast("dict[str, Any]", openapi_spec.get("paths", {}))
    count = 0
    for path, path_item in paths.items():
        for method in ["get", "GET"]:
            if method in path_item:
                operation = cast("dict[str, Any]", path_item[method])

                if "{" not in path:
                    logger.debug(
                        "Skipping auto-generated resource for %s: no path parameters in template",
                        path,
                    )
                    continue

                uri_template = f"gitea://{path.lstrip('/')}"

                if uri_template in normalized_skip:
                    logger.debug(
                        "Skipping auto-generated resource %s: will be provided by custom resource",
                        uri_template,
                    )
                    continue

                # Spec-level filtering: skip if operationId is filtered
                # (scope-restricted, deprecated, or config-excluded).
                op_id: str = operation.get("operationId", "")
                if op_id and op_id in filtered:
                    reason = filtered[op_id].get("reason", "unknown")
                    logger.debug(
                        "Skipping auto-generated resource %s: filtered (%s)",
                        uri_template,
                        reason,
                    )
                    continue

                resource_name = _derive_resource_name(operation, path)
                swagger_tags = set(operation.get("tags", [])) or None
                required_scope = derive_required_scope(swagger_tags, "GET")

                # No tracking_set: auto resources are consumers of
                # skip_uris (set by the orchestrator above), not
                # producers.  Only custom resources track their URIs.
                try:
                    make_api_resource(
                        mcp, gitea_client, openapi_spec,
                        uri=uri_template,
                        api_path=path,
                        method="GET",
                        name=resource_name,
                        scope=required_scope,
                        tags={"api", "raw", "auto"},
                    )
                    count += 1
                    logger.debug("Registered auto-generated resource: %s", uri_template)
                except ValueError as e:
                    logger.warning(
                        "Skipping auto-generated resource %s: %s",
                        uri_template,
                        e,
                    )
                    continue

    logger.info("Auto-generated %d resources from OpenAPI spec", count)


__all__ = [
    "register_auto_generated_resources",
]
