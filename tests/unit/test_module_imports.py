"""Structural guards: the module tree imports cleanly and stays layered.

These tests import the whole module tree — discovered from the filesystem, so
the surface cannot drift from a hand-maintained list — and validate ``__all__``
exports.  The dependency *direction* is governed by ``test_layer_contract``;
this file keeps the runtime and registry checks that a static import graph
cannot express.

Reasons to keep this file:
- Catches import regressions — Python fails hard on a broken import
- Catches ``__all__`` drift — a renamed/removed name still listed in ``__all__``
- Locks the runtime import side effects the layer contract cannot see
"""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys

import pytest

from tests.helpers.import_graph import discover_modules

ALL_MODULES: list[str] = sorted(discover_modules())


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


# ---------------------------------------------------------------------------
# Modules with zero runtime code (pure types only).  They have no public
# callables to verify, so they are skipped in the __all__-validation test.
# ---------------------------------------------------------------------------
_ZERO_RUNTIME_MODULES = frozenset(
    {
        "gitea_mcp_server.models",
        "gitea_mcp_server.openapi_types",
    }
)


def _all_exports_skip_reason(module_name: str) -> str | None:
    """Return a skip reason if *module_name* should be excluded, else ``None``.

    Called at collection time to build ``@pytest.mark.skipif`` marks per
    the project's testing standards (conditional skips must be declared
    at collection time, not via inline ``pytest.skip()``).
    """
    if module_name in _ZERO_RUNTIME_MODULES:
        return "Zero-runtime module (typed dicts only)"
    try:
        mod = importlib.import_module(module_name)
    except Exception:
        return f"Cannot import {module_name}"
    if not hasattr(mod, "__all__"):
        return f"{module_name} has no __all__"
    return None


# Parametrize each module with a skip-if mark when the module has nothing
# to validate.  This makes skip reasons visible at collection time (e.g.
# ``pytest --co``, ``pytest -rs``) and follows the project convention of
# ``@pytest.mark.skipif`` over inline ``pytest.skip()``.
_ALL_EXPORTS_PARAMS = []
for _mod in ALL_MODULES:
    _reason = _all_exports_skip_reason(_mod)
    if _reason:
        _ALL_EXPORTS_PARAMS.append(
            pytest.param(_mod, marks=pytest.mark.skipif(True, reason=_reason))
        )
    else:
        _ALL_EXPORTS_PARAMS.append(pytest.param(_mod))


class TestAllExportsAreValid:
    """For every module with ``__all__``, every exported name actually exists.

    This catches ``__all__`` drift: if a function is renamed or removed but
    ``__all__`` is not updated, this test will fail.
    """

    @pytest.mark.parametrize("module_name", _ALL_EXPORTS_PARAMS)
    def test_all_exports_exist(self, module_name: str) -> None:
        """All names in ``__all__`` are valid attributes of the module.

        Note: modules without ``__all__`` and zero-runtime modules are
        skipped via ``@pytest.mark.skipif`` at parametrization time
        (see ``_ALL_EXPORTS_PARAMS`` above).  The test body only sees
        modules that have a valid ``__all__`` list.
        """
        module = importlib.import_module(module_name)
        all_names: list[str] = module.__all__  # guaranteed present via skip marks

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
                assert imported is not None, f"from {module_name} import {name} returned None"


class TestStructuralGuards:
    """Runtime and registry guards the static layer contract cannot express.

    The dependency direction is enforced in ``test_layer_contract``; these
    tests cover runtime import side effects, the formatter registry's home, and
    the display-hint regression lock (#775).
    """

    def test_registry_lives_in_format_layer(self) -> None:
        """The registry symbols are defined in ``format``, not ``display``."""
        import gitea_mcp_server.format as format_module

        for symbol in (
            "register_formatter",
            "get_formatter",
            "get_formatter_for_type",
            "resolve_formatter",
        ):
            assert hasattr(format_module, symbol), f"format is missing {symbol}"

        # Display holds no registry state — plugins only.
        import gitea_mcp_server.tools.display as display_module

        assert not hasattr(display_module, "_FORMATTERS")
        assert not hasattr(display_module, "_TYPE_FORMATTERS")

    def test_importing_pipeline_does_not_load_display(self) -> None:
        """The package import must not load the plugins; the root does.

        Run in a fresh interpreter so the in-process registrations the test
        suite performs (``conftest``) cannot mask the real import graph: if
        ``tools/__init__.py`` re-grows a ``display`` side-effect import, this
        fails even though a static namespace check would still pass.
        """
        code = (
            "import sys; import gitea_mcp_server.tools.result_pipeline; "
            "assert 'gitea_mcp_server.tools.display' not in sys.modules, "
            "'importing result_pipeline pulled in the display plugins'"
        )
        result = subprocess.run(  # noqa: S603 - trusted interpreter, literal code
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_format_carries_no_hint_index(self) -> None:
        """``format`` carries hints as data, never as a lazily-built index.

        The converter resolves view hints at registration (#775); the format
        layer must hold no hint lookup and no spec mutation.  The direct
        converter-import ban lives in ``test_layer_contract``.
        """
        from gitea_mcp_server import format as format_module

        source = inspect.getsource(format_module)
        for symbol in (
            "_hint_index",
            "_HINT_INDEX_KEY",
            "_view_hints",
            "x-mcp-view",
            "x-mcp-hint-index",
        ):
            assert symbol not in source, f"format.py still references {symbol!r}"
