"""The inter-module dependency direction is an enforced contract.

Layers are ordered low -> high.  A module may import only modules in the same
or a lower layer; an upward import — or an explicitly forbidden edge — fails
here rather than surfacing later as a wiring surprise.  This is the general
form of the ad-hoc result-pipeline/display guard it replaces.

Adding a module to a new layer, or moving one between layers, means editing
``LAYERS``; a module that belongs to no layer fails ``test_every_module_has_a_layer``.
"""

from __future__ import annotations

import ast

import pytest

from tests.helpers.import_graph import (
    build_import_graph,
    discover_modules,
    resolve_import_targets,
)

# Ordered low -> high.  Each entry is (layer name, module prefixes); a module
# belongs to the layer whose matching prefix is longest, so the bare package
# prefix ``gitea_mcp_server`` captures only the package ``__init__``.
LAYERS: list[tuple[str, tuple[str, ...]]] = [
    (
        "leaf",
        (
            "gitea_mcp_server.constants",
            "gitea_mcp_server.exceptions",
            "gitea_mcp_server.openapi_types",
            "gitea_mcp_server.models",
            "gitea_mcp_server.schema_utils",
            "gitea_mcp_server.marker",
            "gitea_mcp_server.pagination",
            "gitea_mcp_server.uri_utils",
            "gitea_mcp_server.context_utils",
            "gitea_mcp_server.request_context",
            "gitea_mcp_server.logging_config",
            "gitea_mcp_server.config",
            "gitea_mcp_server.search",
            "gitea_mcp_server.scope",
        ),
    ),
    ("converter", ("gitea_mcp_server.openapi_converter",)),
    ("payload-resolution", ("gitea_mcp_server.ref_resolver",)),
    ("format", ("gitea_mcp_server.format",)),
    ("client", ("gitea_mcp_server.client",)),
    (
        "runtime",
        (
            "gitea_mcp_server.tools",
            "gitea_mcp_server.resources",
            "gitea_mcp_server.cache_invalidation",
            "gitea_mcp_server.label_service",
            "gitea_mcp_server.response_cache",
            "gitea_mcp_server.validation",
        ),
    ),
    ("setup", ("gitea_mcp_server.server_setup",)),
    ("root", ("gitea_mcp_server",)),
]

# Edges forbidden even though the layer order would permit them.
FORBIDDEN_EDGES: list[tuple[str, str]] = [
    # The result pipeline must not depend on the Gitea-specific formatter
    # catalog; formatters are plugins loaded by the composition root.
    ("gitea_mcp_server.tools.result_pipeline", "gitea_mcp_server.tools.display"),
    # The format layer must not reach into the spec converter directly: it
    # receives display knowledge as data resolved at registration time.
    ("gitea_mcp_server.format", "gitea_mcp_server.openapi_converter"),
]


def _layer_index(module: str) -> int:
    """Return the index of the layer owning *module*, or ``-1`` if unplaced."""
    best_index = -1
    best_len = -1
    for index, (_, prefixes) in enumerate(LAYERS):
        for prefix in prefixes:
            if (module == prefix or module.startswith(prefix + ".")) and len(prefix) > best_len:
                best_len = len(prefix)
                best_index = index
    return best_index


def test_every_module_has_a_layer() -> None:
    """Every module belongs to a layer, so its dependencies are governed."""
    unclassified = sorted(name for name in discover_modules() if _layer_index(name) < 0)
    assert not unclassified, (
        "These modules are in no layer; add them to LAYERS so their "
        f"dependencies are governed: {unclassified}"
    )


def test_dependencies_point_downward_or_sideways() -> None:
    """No module imports a module in a higher layer."""
    graph = build_import_graph()
    violations: list[str] = []
    for importer in sorted(graph):
        for imported in sorted(graph[importer]):
            imp = _layer_index(importer)
            exp = _layer_index(imported)
            if exp > imp:
                violations.append(
                    f"{importer} ({LAYERS[imp][0]}) imports {imported} ({LAYERS[exp][0]})"
                )
    assert not violations, (
        "Dependencies must point to the same or a lower layer:\n  " + "\n  ".join(violations)
    )


def test_forbidden_edges_are_absent() -> None:
    """Explicitly forbidden edges (beyond layer order) do not exist."""
    graph = build_import_graph()
    found: list[str] = []
    for importer, forbidden in FORBIDDEN_EDGES:
        targets = graph.get(importer, set())
        if any(target == forbidden or target.startswith(forbidden + ".") for target in targets):
            found.append(f"{importer} -> {forbidden}")
    assert not found, "Forbidden dependency edge(s) present:\n  " + "\n  ".join(found)


def _targets(source: str, package: str) -> list[str]:
    node = ast.parse(source).body[0]
    assert isinstance(node, (ast.Import, ast.ImportFrom))
    return resolve_import_targets(node, package)


@pytest.mark.parametrize(
    "source",
    [
        "from .openapi_converter.display_hints import view_hints_for",
        "from .openapi_converter import display_hints",
        "from . import openapi_converter",
        "from gitea_mcp_server import openapi_converter",
        "import gitea_mcp_server.openapi_converter.core",
    ],
)
def test_relative_imports_resolve_to_absolute(source: str) -> None:
    """Relative imports resolve, so a relative converter import cannot slip through."""
    targets = _targets(source, "gitea_mcp_server")
    assert any(
        t == "gitea_mcp_server.openapi_converter"
        or t.startswith("gitea_mcp_server.openapi_converter.")
        for t in targets
    ), source


def test_non_converter_import_yields_no_converter_target() -> None:
    """A converter-free import produces no converter target."""
    targets = _targets("from gitea_mcp_server import models", "gitea_mcp_server")
    assert not any(t.startswith("gitea_mcp_server.openapi_converter") for t in targets)
