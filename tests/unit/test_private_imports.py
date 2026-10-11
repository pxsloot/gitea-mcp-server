"""Cross-cutting invariant: no production module couples to an underscore-private
name across a boundary.

Production code must not import a name it does not own from an underscore-private
surface -- external (third-party or stdlib) or first-party -- and must not reach
a private attribute via ``getattr(obj, "_x")``.  Dunder names (``__future__``,
``__all__``, ...) are outside the rule.  This is the executable form of the
"no private coupling" rule (#830); DESIGN Pattern 4 ("never a hack into its
internals") and DEVELOPMENT §"Public vs Private" state it.

The scan walks both shipped packages (``gitea_mcp_server``, ``gitea_mcp_client``)
via filesystem discovery, so the checked set cannot drift from a hand list and a
package ``__init__`` is covered too.

Blind spots (documented on ``tests/helpers/ast_guards``): aliasing, dynamic
``importlib.import_module("pkg._x")`` and ``__import__``.  This is a
reintroduction deny-list, not taint analysis.
"""

from __future__ import annotations

from tests.helpers.ast_guards import iter_private_getattr, iter_private_imports
from tests.helpers.import_graph import PACKAGES, discover_modules, package_of

# Importer module -> the private names it is allowed to import/access.  #830
# lands in two PRs: PR1 carries the five known first-party crossings so the
# boundary rule ships green; PR2 resolves them and empties this map, making the
# rule hold at every boundary.  Every entry is a deliberate, documented
# exception -- never a silent default (issue DoD #2).
ALLOWLIST: dict[str, frozenset[str]] = {
    "gitea_mcp_server.server": frozenset({"_ToolWrappingTransform"}),
    "gitea_mcp_server.server_setup.mcp_builder": frozenset(
        {"_detect_has_labels", "_is_array_response", "_prepare_annotations"}
    ),
    "gitea_mcp_server.tools.errors": frozenset({"_collect_enum_values"}),
}


def _violations() -> dict[str, list[tuple[int, str, str]]]:
    """Map importer module -> ``(lineno, kind, private_name)`` violations.

    ``kind`` is ``"import"`` or ``"getattr"``; it is diagnostic only -- the
    allowlist is keyed on the private name.
    """
    found: dict[str, list[tuple[int, str, str]]] = {}
    for package in PACKAGES:
        for dotted, path in discover_modules(package).items():
            source = path.read_text()
            module_violations: list[tuple[int, str, str]] = [
                (line, "import", name)
                for line, _target, name in iter_private_imports(source, package_of(dotted, path))
            ]
            module_violations += [
                (line, "getattr", name) for line, name in iter_private_getattr(source)
            ]
            if module_violations:
                found[dotted] = sorted(module_violations)
    return found


def test_no_private_coupling_across_boundaries() -> None:
    """Only the documented allowlist may import/access underscore-private names.

    Set equality both ways: an unexpected coupling fails (subset), and a stale
    allowlist entry fails (superset) -- so resolving a crossing without editing
    the allowlist is caught, not silently accepted.
    """
    found = _violations()
    actual = {module: frozenset(name for _, _, name in hits) for module, hits in found.items()}
    assert actual == ALLOWLIST, (
        "Production code couples to underscore-private names outside the "
        f"documented allowlist (#830). Found: {found}. "
        f"Allowlist: {dict(ALLOWLIST)}"
    )


def test_scanner_detects_planted_violations() -> None:
    """The detectors are not vacuous: every shape is caught, dunders are not."""
    source = (
        "from pkg import _private\n"  # 1
        "import dep._submodule\n"  # 2
        "from . import _relative\n"  # 3
        "from .sibling import _sibling\n"  # 4
        "from pkg._mod import public\n"  # 5
        "from pkg import public, _also_private\n"  # 6
        "from pkg import public_name\n"  # 7 (clean)
        "from __future__ import annotations\n"  # 8 (clean: dunder module)
        "from pkg import __version__\n"  # 9 (clean: dunder name)
        "x = getattr(obj, '_thing')\n"  # 10
        "y = getattr(obj, '__dunder__')\n"  # 11 (clean: dunder attr)
        "from gettext import _\n"  # 12 (clean: bare gettext alias)
    )
    assert iter_private_imports(source, "gitea_mcp_server.tools") == [
        (1, "pkg", "_private"),
        (2, "dep._submodule", "_submodule"),
        (3, "gitea_mcp_server.tools", "_relative"),
        (4, "gitea_mcp_server.tools.sibling", "_sibling"),
        (5, "pkg._mod", "_mod"),
        (6, "pkg", "_also_private"),
    ]
    assert iter_private_getattr(source) == [(10, "_thing")]
