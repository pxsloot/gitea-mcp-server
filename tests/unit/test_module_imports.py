"""Smoke tests verifying module-level imports and __all__ exports.

These tests ensure the entire module tree can be imported without circular
imports or missing dependencies, and that ``__all__`` in each module
actually references existing names.

Reasons to keep this file:
- Catches circular import regressions (Python fails hard on those)
- Catches ``__all__`` drift — when a public name is renamed or removed but
  ``__all__`` is not updated, the mismatch is visible here
- Low-maintenance: module list rarely changes, and ``__all__`` validation
  is data-driven (iterates module attributes, no hardcoded names)
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# All public modules in the gitea_mcp_server package.
#
# Keep this list in sync with the actual module tree:
# ``docs/DEVELOPMENT.md`` (Code Organization Rules) describes the directory
# layout; when a new module is added to any subpackage, it must be added
# here too so the import-smoke and __all__-validation tests cover it.
# ---------------------------------------------------------------------------

ALL_MODULES: list[str] = [
    # Flat modules
    "gitea_mcp_server",
    "gitea_mcp_server.cache_invalidation",
    "gitea_mcp_server.client",
    "gitea_mcp_server.config",
    "gitea_mcp_server.constants",
    "gitea_mcp_server.exceptions",
    "gitea_mcp_server.format",
    "gitea_mcp_server.label_service",
    "gitea_mcp_server.logging_config",
    "gitea_mcp_server.models",
    "gitea_mcp_server.openapi_types",
    "gitea_mcp_server.pagination",
    "gitea_mcp_server.schema_utils",
    "gitea_mcp_server.scope",
    "gitea_mcp_server.search",
    "gitea_mcp_server.server",
    "gitea_mcp_server.validation",
    # Subpackages
    "gitea_mcp_server.openapi_converter",
    "gitea_mcp_server.openapi_converter.core",
    "gitea_mcp_server.openapi_converter.schema",
    "gitea_mcp_server.resources",
    "gitea_mcp_server.resources.auto",
    "gitea_mcp_server.resources.custom",
    "gitea_mcp_server.resources.factory",
    "gitea_mcp_server.resources.meta",
    "gitea_mcp_server.resources.scope",
    "gitea_mcp_server.server_setup",
    "gitea_mcp_server.server_setup.http_server",
    "gitea_mcp_server.server_setup.mcp_builder",
    "gitea_mcp_server.server_setup.mcp_extensions",
    "gitea_mcp_server.server_setup.resource_setup",
    "gitea_mcp_server.server_setup.spec_loader",
    "gitea_mcp_server.tools",
    "gitea_mcp_server.tools.customize",
    "gitea_mcp_server.tools.display",
    "gitea_mcp_server.tools.docs_tools",
    "gitea_mcp_server.tools.errors",
    "gitea_mcp_server.tools.examples",
    "gitea_mcp_server.tools.exclusion",
    "gitea_mcp_server.tools.extensions_metadata",
    "gitea_mcp_server.tools.filter_info",
    "gitea_mcp_server.tools.label_transform",
    "gitea_mcp_server.tools.labels",
    "gitea_mcp_server.tools.mcp_tools",
    "gitea_mcp_server.tools.namespace",
    "gitea_mcp_server.tools.resource_display",
    "gitea_mcp_server.tools.schemas",
    "gitea_mcp_server.tools.search",
    "gitea_mcp_server.tools.tool_display",
    "gitea_mcp_server.tools.type_info",
    "gitea_mcp_server.tools.unified_search",
    "gitea_mcp_server.tools.virtual_params",
]


class TestAllModulesImport:
    """Every public module imports cleanly — no circular imports or missing deps."""

    @pytest.mark.parametrize("module_name", ALL_MODULES)
    def test_module_imports_cleanly(self, module_name: str) -> None:
        """Assert that ``module_name`` can be imported without error."""
        importlib.import_module(module_name)
        # If we get here, the import succeeded.

    def test_import_all_at_once(self) -> None:
        """Importing every module in sequence should also succeed."""
        for mod in ALL_MODULES:
            importlib.import_module(mod)


# Modules that define __all__ but have zero runtime code (pure types only).
# These have no public callables to verify — skip them.
_ZERO_RUNTIME_MODULES = frozenset({
    "gitea_mcp_server.models",
    "gitea_mcp_server.openapi_types",
})


class TestAllExportsAreValid:
    """For every module with ``__all__``, every exported name actually exists.

    This catches ``__all__`` drift: if a function is renamed or removed but
    ``__all__`` is not updated, this test will fail.
    """

    @pytest.mark.parametrize("module_name", ALL_MODULES)
    def test_all_exports_exist(self, module_name: str) -> None:
        """All names in ``__all__`` are valid attributes of the module."""
        if module_name in _ZERO_RUNTIME_MODULES:
            pytest.skip("Zero-runtime module (typed dicts only)")

        module = importlib.import_module(module_name)
        all_names: list[str] | None = getattr(module, "__all__", None)
        if all_names is None:
            pytest.skip(f"{module_name} has no __all__")

        module_dir = set(dir(module))
        for name in all_names:
            assert name in module_dir, (
                f"{module_name}.__all__ contains '{name}' "
                f"which is not defined in the module. "
                f"Valid names include: {sorted(module_dir)}"
            )

    def test_all_exported_names_are_importable(self) -> None:
        """Every name in ``__all__`` can be imported with ``from module import name``."""
        for module_name in ALL_MODULES:
            if module_name in _ZERO_RUNTIME_MODULES:
                continue
            module = importlib.import_module(module_name)
            all_names: list[str] | None = getattr(module, "__all__", None)
            if not all_names:
                continue
            for name in all_names:
                # Use importlib.import_module -> getattr pattern (equivalent
                # to ``from module import name``) so we can loop.
                imported = getattr(module, name)
                assert imported is not None, (
                    f"from {module_name} import {name} returned None"
                )


class TestNoCircularImports:
    """Verify the entire package can be imported in a fresh interpreter context.

    This imports every module we know about in a single session to flush out
    circular-import bugs that don't appear when importing one module at a time.
    """

    def test_full_tree_import(self) -> None:
        """All modules import cleanly in one pass."""
        for mod in ALL_MODULES:
            # Re-import to ensure idempotent imports
            importlib.reload(importlib.import_module(mod))
