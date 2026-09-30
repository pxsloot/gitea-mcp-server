"""The architecture doc's Module Map must match the real module surface.

The map is the first thing an agent reads to orient in this codebase, and a
stale entry sends the next investigation down the wrong path.  These tests make
the map executable: every file it names must exist, and every production module
must be named.
"""

from __future__ import annotations

import re

from gitea_mcp_server.registration import (
    RETIRED_RESOURCE_META_KEYS,
    RETIRED_TOOL_META_KEYS,
)
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

# The retired flat registration keys are the canonical deny-list
# (``registration.py``).  A stale reference reads one off a ``meta`` mapping;
# the sanctioned record's own serialized field names (``"virtual_params"``,
# ``"customization"``, ...) are legitimate content, so the non-prefixed keys
# are only matched in meta-access position.  The underscore-prefixed keys are
# never record field names, so a bare bounded token (``_customization``,
# ``_WRAP_ME``) is unambiguously stale.  ``\b`` avoids ``_build_customization_meta``.
_RETIRED_META_KEYS = frozenset(RETIRED_TOOL_META_KEYS | RETIRED_RESOURCE_META_KEYS)
_RETIRED_ALTERNATION = "|".join(re.escape(key) for key in sorted(_RETIRED_META_KEYS))
_UNDERSCORE_IDENTIFIERS = sorted(
    {key for key in _RETIRED_META_KEYS if key.startswith("_")} | {"_WRAP_ME"}
)
_UNDERSCORE_ALTERNATION = "|".join(re.escape(key) for key in _UNDERSCORE_IDENTIFIERS)

_RETIRED_MECHANISM_PATTERNS = (
    # A bare underscore-prefixed identifier (retired key or the wrap marker).
    re.compile(rf"\b(?:{_UNDERSCORE_ALTERNATION})\b"),
    # Any retired key read off a ``meta`` mapping.
    re.compile(rf"""meta\[\s*["'](?:{_RETIRED_ALTERNATION})["']\s*\]"""),
    re.compile(rf"""meta\.get\(\s*["'](?:{_RETIRED_ALTERNATION})["']"""),
)

# The design decision that documents the contract is its canonical home and is
# exempt from the sweep.
_DECISION_HEADING_RE = re.compile(r"(?m)^\s*\d+\.\s+\*\*Typed registration metadata")
_NEXT_DECISION_RE = re.compile(r"(?m)^\s*\d+\.\s+\*\*")


def _contract_decision_section() -> str:
    """Return the registration-contract design decision's text (decision #20).

    Keyed on the decision's title (stable across renumbering) and bounded by the
    next decision or the section separator, so a renumber or an inserted
    ``---`` neither drops nor over-extends the exemption.
    """
    text = _DOC.read_text()
    match = _DECISION_HEADING_RE.search(text)
    if match is None:
        return ""
    boundaries = []
    next_decision = _NEXT_DECISION_RE.search(text, match.end())
    if next_decision:
        boundaries.append(next_decision.start())
    separator = text.find("\n---\n", match.end())
    if separator != -1:
        boundaries.append(separator)
    end = min(boundaries) if boundaries else len(text)
    return text[match.start() : end]


def _retired_mechanism_references(text: str) -> list[str]:
    """Return the retired-mechanism references found in *text*."""
    found: list[str] = []
    for pattern in _RETIRED_MECHANISM_PATTERNS:
        found.extend(pattern.findall(text))
    return found


def test_sweep_detects_retired_references() -> None:
    """The sweep is not vacuous: stale access is caught, the record is not.

    Guards against a pattern edit that silently matches nothing.
    """
    stale = 'tool.meta["_virtual_params"]\nresource.meta.get("required_scopes")\n`_WRAP_ME`\n'
    assert _retired_mechanism_references(stale)

    clean = 'tool.meta["registration"]\n"virtual_params": None\n"size_hint": "tiny"\n'
    assert _retired_mechanism_references(clean) == []


def test_no_retired_mechanism_identifiers_in_docs() -> None:
    """Retired flat-key references do not appear in docs/ outside the contract.

    The registration contract (decision #20) documents the record; the old flat
    keys are gone.  This test makes DoD #4 ("superseded mentions are gone")
    executable, so a stale reference fails the build instead of surfacing later
    as a wiring surprise.
    """
    docs_dir = project_root() / "docs"
    contract_section = _contract_decision_section()
    offenders: dict[str, list[str]] = {}

    for doc_path in sorted(docs_dir.rglob("*.md")):
        text = doc_path.read_text()
        # Exempt decision #20 in ARCHITECTURE.md (it documents the contract).
        if doc_path.name == "ARCHITECTURE.md" and contract_section:
            text = text.replace(contract_section, "")
        found = _retired_mechanism_references(text)
        if found:
            offenders[str(doc_path.relative_to(project_root()))] = sorted(set(found))

    assert not offenders, (
        f"Retired mechanism identifiers found in docs: {offenders}. "
        "The registration contract (decision #20) is the canonical home; "
        "update the reference to the record."
    )
