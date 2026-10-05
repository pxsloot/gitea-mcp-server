"""The architecture doc's Module Map must match the real module surface.

The map is the first thing an agent reads to orient in this codebase, and a
stale entry sends the next investigation down the wrong path.  These tests make
the map executable: every file it names must exist, and every production module
must be named.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    end = text.index("## Contracts & Invariants")
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

# The Contracts & Invariants entry that documents the registration contract is
# its canonical home and is exempt from the sweep.
_ENTRY_HEADING_RE = re.compile(r"(?m)^\*\*Registration metadata")
_NEXT_ENTRY_RE = re.compile(r"(?m)^\*\*")


def _contracts_section() -> str:
    """Return the Contracts & Invariants section's text, up to the next rule."""
    text = _DOC.read_text()
    start = text.index("## Contracts & Invariants")
    end = text.find("\n---\n", start)
    return text[start : end if end != -1 else len(text)]


def _contract_entry_section() -> str:
    """Return the registration-contract entry's text.

    Keyed on the entry's title (stable across edits) and bounded by the next
    entry, so an inserted entry neither drops nor over-extends the exemption.
    """
    section = _contracts_section()
    match = _ENTRY_HEADING_RE.search(section)
    if match is None:
        return ""
    next_entry = _NEXT_ENTRY_RE.search(section, match.end())
    end = next_entry.start() if next_entry else len(section)
    return section[match.start() : end]


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

    The registration contract (the "Registration metadata" entry in
    Contracts & Invariants) documents the record; the old flat keys are gone.
    This test makes DoD #4 ("superseded mentions are gone")
    executable, so a stale reference fails the build instead of surfacing later
    as a wiring surprise.
    """
    docs_dir = project_root() / "docs"
    contract_section = _contract_entry_section()
    offenders: dict[str, list[str]] = {}

    for doc_path in sorted(docs_dir.rglob("*.md")):
        text = doc_path.read_text()
        # Exempt the registration-contract entry in ARCHITECTURE.md.
        if doc_path.name == "ARCHITECTURE.md" and contract_section:
            text = text.replace(contract_section, "")
        found = _retired_mechanism_references(text)
        if found:
            offenders[str(doc_path.relative_to(project_root()))] = sorted(set(found))

    assert not offenders, (
        f"Retired mechanism identifiers found in docs: {offenders}. "
        "The registration contract entry is the canonical home; "
        "update the reference to the record."
    )


# ── Cross-reference resolution ───────────────────────────────────────────────
#
# A reference of the form ``ARCHITECTURE.md`` → "Some Title" must name a real
# heading or Contracts & Invariants entry.  Ordinals were replaced by titles
# precisely so pointers stay meaningful; a stale title pointer (the class this
# guard exists to catch) is what makes the next reader fail to find the rule.
#
# References are hard-wrapped and come in two shapes: a title after the arrow,
# and a title named before it (``the "X" entry in ARCHITECTURE.md``).  The
# sweep is therefore not line-oriented.  ``rhs`` spans one wrapped continuation
# line but never crosses a blank line or a table row -- a table row is a
# self-contained reference on one physical line.

_ARCH_REF_RE = re.compile(r"ARCHITECTURE\.md`?\s*(?:→|->)\s*(?P<rhs>[^\n]*(?:\n(?![|\n])[^\n]*)?)")
_PRE_ARROW_REF_RE = re.compile(r"[\"'](?P<title>[^\"']+)[\"']\s+entry in\s+[^\n]*ARCHITECTURE\.md")
_QUOTED_RE = re.compile(r"[\"'](?P<title>[^\"']+)[\"']")
_SENTENCE_END_RE = re.compile(r"\.\s")


def _normalize_anchor(text: str) -> str:
    """Fold a heading/entry/reference title to a comparable anchor."""
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text.rstrip(" .,)")


def _contract_entries() -> list[str]:
    """Return the bold titles of the Contracts & Invariants entries."""
    entries: list[str] = []
    current: list[str] | None = None
    for line in _contracts_section().splitlines():
        if line.startswith("**"):
            if current is not None:
                entries.append(" ".join(current))
            current = [line]
        elif current is not None:
            if line.strip():
                current.append(line.strip())
            else:
                entries.append(" ".join(current))
                current = None
    if current is not None:
        entries.append(" ".join(current))
    titles = []
    for entry in entries:
        match = re.match(r"\*\*(?P<title>.+?)\*\*", entry)
        if match:
            titles.append(match.group("title"))
    return titles


def _architecture_anchors() -> set[str]:
    """Every title a cross-reference may resolve to (headings + entries)."""
    anchors = set()
    for line in _DOC.read_text().splitlines():
        if line.startswith("#"):
            anchors.add(_normalize_anchor(line.lstrip("#").strip()))
    anchors.update(_normalize_anchor(title) for title in _contract_entries())
    return {anchor for anchor in anchors if anchor}


def _resolves(title: str, anchors: set[str]) -> bool:
    """A reference resolves on an exact title or a title prefix."""
    return any(anchor == title or anchor.startswith(title) for anchor in anchors)


def _unresolved_arch_refs(text: str, anchors: set[str]) -> list[str]:
    """Return quoted titles in ARCHITECTURE.md references absent from *anchors*."""
    offenders: list[str] = []
    for ref in _ARCH_REF_RE.finditer(text):
        # A reference ends at the first sentence break, so a following sentence
        # on the wrapped line cannot contribute an unrelated quoted phrase.
        rhs = _SENTENCE_END_RE.split(ref.group("rhs"), maxsplit=1)[0]
        for quoted in _QUOTED_RE.finditer(rhs):
            title = _normalize_anchor(quoted.group("title"))
            if title and not _resolves(title, anchors):
                offenders.append(quoted.group("title"))
    for ref in _PRE_ARROW_REF_RE.finditer(text):
        title = _normalize_anchor(ref.group("title"))
        if title and not _resolves(title, anchors):
            offenders.append(ref.group("title"))
    return offenders


def test_cross_reference_sweep_detects_stale_titles() -> None:
    """The sweep is not vacuous: every reference shape is exercised.

    A stale title is caught in each shape -- same line, wrapped line, and named
    before the arrow -- and a live title passes in each.
    """
    anchors = _architecture_anchors()
    stale = [
        'See `docs/ARCHITECTURE.md` → "Vendor extension (`x-*`) stripping".\n',
        'See `docs/ARCHITECTURE.md` → Contracts &\nInvariants, "Vendor extension (`x-*`) stripping".\n',
        'the "Vendor extension (`x-*`) stripping" entry in `docs/ARCHITECTURE.md` → Contracts & Invariants.\n',
    ]
    live = [
        'See `docs/ARCHITECTURE.md` → Contracts & Invariants, "One result pipeline".\n',
        'See `docs/ARCHITECTURE.md` → Contracts &\nInvariants, "The dependency direction is enforced".\n',
        'the "Only agent-misleading spec quirks are normalized" entry in `docs/ARCHITECTURE.md` → Contracts & Invariants.\n',
    ]
    for fixture in stale:
        assert _unresolved_arch_refs(fixture, anchors), fixture
    for fixture in live:
        assert _unresolved_arch_refs(fixture, anchors) == [], fixture


def test_architecture_cross_references_resolve() -> None:
    """Every ``ARCHITECTURE.md" → "Title" reference names a real title.

    References resolve by section/entry title (never by ordinal); this makes
    the pointer itself executable, so a renamed heading fails the build
    instead of sending the next reader to a title that no longer exists.
    """
    anchors = _architecture_anchors()
    offenders: dict[str, list[str]] = {}
    # This module embeds deliberately stale/live references as fixtures; skip it.
    self_path = Path(__file__).resolve()

    roots = [project_root() / "docs", project_root() / "gitea_mcp_server", project_root() / "tests"]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if (
                path.suffix not in {".md", ".py"}
                or not path.is_file()
                or path.resolve() == self_path
            ):
                continue
            found = _unresolved_arch_refs(path.read_text(), anchors)
            if found:
                offenders[str(path.relative_to(project_root()))] = sorted(set(found))

    assert not offenders, (
        f"ARCHITECTURE.md cross-references name non-existent titles: {offenders}. "
        "Point at a current heading or Contracts & Invariants entry by title."
    )
