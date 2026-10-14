"""docs/CAPABILITIES.md is the planning-facing index of cross-cutting facilities.

It exists to stop a planner building a second facility for a solved concern, so
a stale pointer is worse than no pointer — it sends the next design down the
wrong path.  These tests make the map executable, mirroring
``tests/unit/test_architecture_doc.py`` for the Module Map: every canonical home
the index names must resolve to a real production module.
"""

from __future__ import annotations

import re

from tests.helpers.import_graph import production_modules, project_root, source_dir

_DOC = project_root() / "docs" / "CAPABILITIES.md"
_MODULE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
_REQUIRED_HEADINGS = ("## Capability index", "## Metadata channels")
_INDEX_END = "## Relationship to other docs"


def _index_section() -> str:
    """Return the doc span holding the canonical-home tables.

    The two tables live between ``## Capability index`` and ``## Relationship to
    other docs``; prose outside that span may mention test or doc files and is
    intentionally not swept.
    """
    text = _DOC.read_text()
    start = text.index(_REQUIRED_HEADINGS[0])
    end = text.index(_INDEX_END)
    return text[start:end]


def _module_tokens() -> set[str]:
    """The module paths named as canonical homes in the index."""
    return set(_MODULE_TOKEN_RE.findall(_index_section()))


def _production_rel_paths() -> set[str]:
    """Package-relative paths of every production module (``tools/filter_info.py``)."""
    return {
        "/".join(path.relative_to(source_dir()).parts) for path in production_modules().values()
    }


def _production_basenames() -> set[str]:
    """Basenames of every production module (``filter_info.py``)."""
    return {path.name for path in production_modules().values()}


def _dangling(tokens: set[str], rel_paths: set[str], basenames: set[str]) -> list[str]:
    """Return tokens that resolve to no production module."""
    return [token for token in sorted(tokens) if token not in rel_paths and token not in basenames]


def test_required_sections_present() -> None:
    """The capability index and metadata-channel sections exist."""
    text = _DOC.read_text()
    missing = [heading for heading in _REQUIRED_HEADINGS if heading not in text]
    assert not missing, f"CAPABILITIES.md is missing required sections: {missing}"


def test_named_canonical_homes_exist() -> None:
    """Every module named as a canonical home resolves to a real file."""
    dangling = _dangling(_module_tokens(), _production_rel_paths(), _production_basenames())
    assert not dangling, f"Capability map names modules that do not exist: {dangling}"


def test_sweep_is_not_vacuous() -> None:
    """A fabricated home is caught; a real one passes.

    Guards against a pattern edit that silently matches nothing.
    """
    rel_paths = _production_rel_paths()
    basenames = _production_basenames()
    assert _dangling({"tools/does_not_exist.py"}, rel_paths, basenames)
    assert _dangling({"tools/filter_info.py"}, rel_paths, basenames) == []


def test_index_is_populated() -> None:
    """The index names a non-trivial set of homes (guard against an emptied map)."""
    assert len(_module_tokens()) >= 10


def test_doc_is_linked_from_the_doc_index() -> None:
    """The capability view is routable from INDEX.md."""
    index = (project_root() / "docs" / "INDEX.md").read_text()
    assert "CAPABILITIES.md" in index
