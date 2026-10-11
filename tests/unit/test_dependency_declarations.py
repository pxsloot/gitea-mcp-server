"""Cross-cutting invariant: third-party imports and declared dependencies agree.

The external dependency surface is derived from the import graph of both shipped
packages (``tests/helpers/import_graph.third_party_import_surface``); there is no
curated list to drift.  The check is **symmetric**: every top-level third-party
import must be backed by a distribution in ``pyproject.toml``
``[project].dependencies`` (no accidental transitive reach, like ``mcp`` /
``starlette`` before #830), and every declared dependency must be imported or
explicitly documented as runtime-only -- so a declaration cannot silently drift
out of the code either (#830 DoD #3, "cannot silently drift").

The one irreducible residue is the import-name -> distribution-name alias (PEP
503 cannot equate ``yaml`` and ``pyyaml``); it is validated both ways below, so a
stale alias fails too.  Upgrade verification is executable (``mypy`` + the suite
+ the guards), not a stored inventory.
"""

from __future__ import annotations

import re
import tomllib

from tests.helpers.import_graph import project_root, third_party_import_surface

# Import name -> distribution name, only where they differ.  Validated both ways
# in ``test_distribution_aliases_are_not_stale``.
_DISTRIBUTION_ALIASES: dict[str, str] = {"yaml": "pyyaml"}

# Declared dependencies not imported by production code, each deliberate.
# ``python-dotenv`` backs pydantic-settings' ``env_file=".env"`` support
# (``config.py``) and is imported directly by the live test suite; it is kept a
# first-class dependency rather than left to a transitive install.  Validated
# both ways in ``test_runtime_only_dependencies_are_not_stale``.
_RUNTIME_ONLY_DEPENDENCIES: frozenset[str] = frozenset({"python-dotenv"})

# A PEP 508 requirement's name ends at its first version/extras/marker delimiter.
_REQUIREMENT_NAME = re.compile(r"[<>=!~\[; (]")


def _normalise(name: str) -> str:
    """Normalise a distribution name per PEP 503 (lowercase, runs of ``-_.`` -> ``-``)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _declared() -> set[str]:
    """Normalised names of ``[project].dependencies``."""
    data = tomllib.loads((project_root() / "pyproject.toml").read_text())
    return {
        _normalise(_REQUIREMENT_NAME.split(requirement, maxsplit=1)[0])
        for requirement in data["project"]["dependencies"]
    }


def _imported_distributions(surface: dict[str, set[str]]) -> set[str]:
    """Normalised distribution names reachable from production imports."""
    return {_normalise(_DISTRIBUTION_ALIASES.get(top, top)) for top in surface}


def _undeclared(surface: dict[str, set[str]], declared: set[str]) -> dict[str, list[str]]:
    """Third-party top-level imports with no backing declared distribution."""
    undeclared: dict[str, list[str]] = {}
    for top, paths in sorted(surface.items()):
        distribution = _DISTRIBUTION_ALIASES.get(top, top)
        if _normalise(distribution) not in declared:
            undeclared[top] = sorted(paths)
    return undeclared


def _unused_declared(declared: set[str], imported: set[str]) -> set[str]:
    """Declared distributions neither imported nor documented as runtime-only."""
    return declared - imported - _RUNTIME_ONLY_DEPENDENCIES


def test_every_third_party_import_is_declared() -> None:
    """Each third-party top-level import maps to a declared dependency."""
    found = _undeclared(third_party_import_surface(), _declared())
    assert not found, (
        "These third-party top-level imports are not declared in "
        f"[project].dependencies: {found}. Declare them (or add a documented "
        "alias) -- do not rely on a transitive dependency by accident."
    )


def test_every_declared_dependency_is_imported_or_runtime_only() -> None:
    """No declared dependency drifts silently out of the import graph."""
    imported = _imported_distributions(third_party_import_surface())
    unused = _unused_declared(_declared(), imported)
    assert not unused, (
        "These declared dependencies are imported by neither shipped package "
        f"nor documented as runtime-only: {sorted(unused)}. Remove them, or add "
        "them to _RUNTIME_ONLY_DEPENDENCIES with a rationale."
    )


def test_distribution_aliases_are_not_stale() -> None:
    """Every alias names a real import top-level and a real declared dependency."""
    surface = third_party_import_surface()
    declared = _declared()
    for imported, distribution in _DISTRIBUTION_ALIASES.items():
        assert imported in surface, f"Alias {imported!r} is not an imported top-level"
        assert _normalise(distribution) in declared, (
            f"Alias {imported!r} -> {distribution!r} is not a declared dependency"
        )


def test_runtime_only_dependencies_are_not_stale() -> None:
    """Each runtime-only entry is declared and genuinely not imported."""
    imported = _imported_distributions(third_party_import_surface())
    declared = _declared()
    for distribution in _RUNTIME_ONLY_DEPENDENCIES:
        normalised = _normalise(distribution)
        assert normalised in declared, f"{distribution!r} is not a declared dependency"
        assert normalised not in imported, (
            f"{distribution!r} is imported by production code, so it is no longer "
            "runtime-only -- remove it from _RUNTIME_ONLY_DEPENDENCIES"
        )


def test_scanner_detects_planted_violation() -> None:
    """The checks are not vacuous: an undeclared import and an unused declaration are flagged."""
    surface = {"fastmcp": {"fastmcp"}, "totally_unknown_pkg": {"totally_unknown_pkg"}}
    assert _undeclared(surface, {"fastmcp"}) == {"totally_unknown_pkg": ["totally_unknown_pkg"]}
    assert _unused_declared({"fastmcp", "declared-but-unused"}, {"fastmcp"}) == {
        "declared-but-unused"
    }
