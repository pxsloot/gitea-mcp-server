"""The architecture doc's Module Map must match the real module surface.

The map is the first thing an agent reads to orient in this codebase, and a
stale entry sends the next investigation down the wrong path.  These tests make
the map executable: every file it names must exist, and every production module
must be named.
"""

from __future__ import annotations

import re

from tests.helpers.import_graph import PROJECT_PACKAGE, production_modules, project_root, source_dir

_DOC = project_root() / "docs" / "ARCHITECTURE.md"
_MODULE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
_PACKAGE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_]+/)`")


def _module_map_section() -> str:
    text = _DOC.read_text()
    start = text.index("## Module Map")
    end = text.index("## Key Design Decisions")
    return text[start:end]


def _module_tokens() -> set[str]:
    return set(_MODULE_TOKEN_RE.findall(_module_map_section()))


def _production_rel_paths() -> dict[str, str]:
    """Map each production module's package-relative path to its basename."""
    return {
        "/".join(path.relative_to(source_dir()).parts): path.name
        for path in production_modules().values()
    }


def test_named_modules_exist() -> None:
    """Every module named in the Module Map resolves to a real file."""
    rel_paths = set(_production_rel_paths())
    basenames = set(_production_rel_paths().values())
    dangling = [
        token
        for token in sorted(_module_tokens())
        if token not in rel_paths and token not in basenames
    ]
    assert not dangling, f"Module Map names files that do not exist: {dangling}"


def test_every_production_module_is_named() -> None:
    """Every production module appears in the Module Map."""
    rel_to_base = _production_rel_paths()
    tokens = _module_tokens()
    base_counts: dict[str, int] = {}
    for base in rel_to_base.values():
        base_counts[base] = base_counts.get(base, 0) + 1
    missing = [
        rel
        for rel, base in sorted(rel_to_base.items())
        if rel not in tokens and not (base in tokens and base_counts[base] == 1)
    ]
    assert not missing, f"Production modules absent from the Module Map: {missing}"


def test_named_packages_exist() -> None:
    """Every package directory named in the Module Map exists."""
    packages = set(_PACKAGE_TOKEN_RE.findall(_module_map_section()))
    root = source_dir()
    dangling = [p for p in sorted(packages) if not (root / p / "__init__.py").exists()]
    assert not dangling, f"Module Map names packages that do not exist: {dangling}"


def test_project_package_is_the_expected_name() -> None:
    """Guard against a silent rename of the package the map is written against."""
    assert (project_root() / PROJECT_PACKAGE).is_dir()


# ── Superseded-mention sweep ─────────────────────────────────────────────────

# Retired flat-key identifiers that must not appear in docs/ outside the
# design decision that documents the contract (decision #20).  Match only
# when they appear as string literals (dict keys) or in meta-access patterns.
_RETIRED_KEY_PATTERNS = [
    re.compile(r'["_](?:WRAP_ME|contract_wrap|customization|virtual_params|executor_id)["\']'),
    re.compile(
        r'meta\[["\']_(?:WRAP_ME|contract_wrap|customization|virtual_params|executor_id)["\']\]'
    ),
    re.compile(
        r'meta\.get\(["\']_(?:WRAP_ME|contract_wrap|customization|virtual_params|executor_id)["\']\)'
    ),
]


def _decision_20_section() -> str:
    """Return the text of decision #20 (the registration contract)."""
    text = _DOC.read_text()
    try:
        start = text.index("20. **Typed registration metadata")
        # Find the next decision or section boundary.
        end = text.index("\n---\n", start)
        return text[start:end]
    except ValueError:
        return ""


def test_no_retired_mechanism_identifiers_in_docs() -> None:
    """Retired flat-key identifiers do not appear in docs/ outside decision #20.

    The registration contract (decision #20) documents the record; the old
    flat keys are gone.  This test makes DoD #4 ("superseded mentions are
    gone") executable, so a stale reference fails the build instead of
    surfacing later as a wiring surprise.
    """
    docs_dir = project_root() / "docs"
    decision_20 = _decision_20_section()
    offenders: dict[str, list[str]] = {}

    for doc_path in sorted(docs_dir.rglob("*.md")):
        text = doc_path.read_text()
        # Exempt decision #20 in ARCHITECTURE.md (it documents the contract).
        if doc_path.name == "ARCHITECTURE.md" and decision_20:
            text = text.replace(decision_20, "")
        found = []
        for pattern in _RETIRED_KEY_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                found.extend(matches)
        if found:
            offenders[str(doc_path.relative_to(project_root()))] = list(set(found))

    assert not offenders, (
        f"Retired mechanism identifiers found in docs: {offenders}. "
        "The registration contract (decision #20) is the canonical home; "
        "update the reference to the record."
    )
