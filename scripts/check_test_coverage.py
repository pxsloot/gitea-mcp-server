#!/usr/bin/env python3
"""Verify every production module has at least one matching test file.

Walks ``gitea_mcp_server/`` for production modules (excluding ``__init__.py``
and zero-runtime TypedDict-only modules), generates candidate test file names
using the project's naming conventions, and checks for matches in ``tests/unit/``
and ``tests/integration/``.

Exit code 0: all modules covered.  Exit code 1: gaps found.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "gitea_mcp_server"
TEST_UNIT_DIR = REPO_ROOT / "tests" / "unit"
TEST_INTEGRATION_DIR = REPO_ROOT / "tests" / "integration"

# Modules that don't need dedicated test files because they carry zero
# runtime code (TypedDicts only, no functions or classes).
ZERO_RUNTIME_MODULES: set[str] = {
    "models.py",
    "openapi_types.py",
}

# Subpackage abbreviation prefixes for the naming convention
# ``test_<abbrev>_<module>.py``.  These are the prefixes extracted from
# existing test file names.
SUBPACKAGE_PREFIXES: dict[str, list[str]] = {
    "tools": ["tool_"],
    "resources": ["resource_"],
}

# Production modules that are tested exclusively through integration tests
# (no unit test file exists).  Key: source module relative path.
# Value: test file basenames that cover it.
INTEGRATION_ONLY_MODULES: dict[str, list[str]] = {
    "gitea_mcp_server/server.py": ["test_server.py", "test_server_http.py"],
    "gitea_mcp_server/server_setup/http_server.py": [
        "test_http_transport_server.py",
        "test_server_http.py",
    ],
    "gitea_mcp_server/server_setup/resource_setup.py": ["test_resources_integration.py"],
}

# Explicit mappings for modules where the naming convention was deliberately
# departed from.  Key: source module path (relative to REPO_ROOT).
# Value: list of test file basenames that cover it.
EXPLICIT_MAPPINGS: dict[str, list[str]] = {
    "gitea_mcp_server/search.py": ["test_search_bm25.py"],
    "gitea_mcp_server/tools/mcp_tools.py": ["test_mcp_tools.py", "test_mcp_tools_wrapping.py"],
}


def _module_name(path: Path) -> str:
    """Extract module name from path (without extension)."""
    return path.stem


def _is_openapi_converter_module(path: Path) -> bool:
    """Check if the module is under openapi_converter/."""
    return "openapi_converter" in path.parts


def _candidates_for_module(rel_path: str) -> list[str]:
    """Generate candidate test file basenames for a source module.

    Returns a list of basenames (e.g. ``test_client.py``) to look for.
    """
    path = Path(rel_path)
    module = _module_name(path)
    candidates: list[str] = []

    # Flat module: ``test_<module>.py``
    candidates.append(f"test_{module}.py")

    # Subpackage modules: ``test_<abbrev>_<module>.py``
    if len(path.parts) > 2:  # gitea_mcp_server/<subpkg>/<module>.py
        subpkg = path.parts[1]
        for prefix in SUBPACKAGE_PREFIXES.get(subpkg, []):
            candidates.append(f"test_{prefix}{module}.py")

    # openapi_converter modules: check same-subdir and flat name
    if _is_openapi_converter_module(path):
        candidates.append(f"openapi_converter/test_{module}.py")

    return candidates


def _test_files_exist(candidates: list[str]) -> bool:
    """Check if any candidate test file exists in unit or integration dirs."""
    for candidate in candidates:
        for search_dir in (TEST_UNIT_DIR, TEST_INTEGRATION_DIR):
            # For openapi_converter candidate paths that include a subdirectory
            if "/" in candidate:
                if (search_dir.parent / candidate).exists():
                    return True
            # For flat candidate names, search recursively
            else:
                # Check for exact match
                if (search_dir / candidate).exists():
                    return True
                # Check subdirectories of unit tests
                for subdir in search_dir.iterdir():
                    if subdir.is_dir() and (subdir / candidate).exists():
                        return True
    return False


def main() -> int:
    """Run the coverage check.  Returns exit code (0=pass, 1=gap)."""
    # Collect production modules
    prod_modules: list[Path] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        if py_file.name in ZERO_RUNTIME_MODULES:
            continue
        prod_modules.append(py_file)

    # Check each module
    gaps: list[str] = []
    for mod_path in prod_modules:
        rel = str(mod_path.relative_to(REPO_ROOT))

        # Check explicit mappings first
        if rel in EXPLICIT_MAPPINGS:
            continue

        # openapi_converter modules: tested collectively by all test files
        # in tests/unit/openapi_converter/.  If that directory has test
        # files, both core.py and schema.py are covered.
        if _is_openapi_converter_module(mod_path):
            converter_dir = TEST_UNIT_DIR / "openapi_converter"
            if converter_dir.is_dir() and any(
                f.name.startswith("test_") and f.name != "__init__.py"
                for f in converter_dir.iterdir()
            ):
                continue
            gaps.append(rel)
            continue

        # Integration-only modules: check explicit test file names
        if rel in INTEGRATION_ONLY_MODULES:
            found = any(
                (TEST_INTEGRATION_DIR / name).exists() for name in INTEGRATION_ONLY_MODULES[rel]
            )
            if not found:
                gaps.append(rel)
            continue

        # Standard check: generate candidates and look for matches
        candidates = _candidates_for_module(rel)
        if not _test_files_exist(candidates):
            gaps.append(rel)

    if gaps:
        print(f"Missing test files for {len(gaps)} module(s):")
        for gap in gaps:
            print(f"  {gap}")
        print("\nThese modules have no matching test file in tests/unit/ or tests/integration/.")
        print("Add a test file following the naming convention, or update")
        print("scripts/check_test_coverage.py if this is an intentional deviation.")
        return 1

    print(f"All {len(prod_modules)} production modules have matching test files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
