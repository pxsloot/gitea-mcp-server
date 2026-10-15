"""docs/CAPABILITIES.md is the planning-facing index of cross-cutting facilities.

It exists to stop a planner building a second facility for a solved concern, so
a stale pointer is worse than no pointer -- it sends the next design down the
wrong path.  These tests make the map executable, mirroring
``tests/unit/test_architecture_doc.py`` for the Module Map: every canonical home
the index names -- module or package -- must resolve to a real production file.
"""

from __future__ import annotations

from tests.helpers.doc_guards import (
    dangling_modules,
    dangling_packages,
    module_tokens,
    package_tokens,
)
from tests.helpers.import_graph import project_root

_DOC = project_root() / "docs" / "CAPABILITIES.md"
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


def test_required_sections_present() -> None:
    """The capability index and metadata-channel sections exist."""
    text = _DOC.read_text()
    missing = [heading for heading in _REQUIRED_HEADINGS if heading not in text]
    assert not missing, f"CAPABILITIES.md is missing required sections: {missing}"


def test_named_canonical_homes_exist() -> None:
    """Every module and package named as a canonical home resolves to a real file."""
    section = _index_section()
    dangling = dangling_modules(module_tokens(section)) + dangling_packages(package_tokens(section))
    assert not dangling, f"Capability map names homes that do not exist: {dangling}"


def test_sweep_is_not_vacuous() -> None:
    """A fabricated home is caught; a real one passes.

    Guards against a pattern edit that silently matches nothing.
    """
    assert dangling_modules({"tools/does_not_exist.py"}) == ["tools/does_not_exist.py"]
    assert dangling_modules({"tools/filter_info.py"}) == []
    assert dangling_packages({"no_such_package/"}) == ["no_such_package/"]
    assert dangling_packages({"tools/"}) == []


def test_index_is_populated() -> None:
    """The index names a non-trivial set of homes (guard against an emptied map)."""
    assert len(module_tokens(_index_section())) >= 10


def test_doc_is_linked_from_the_doc_index() -> None:
    """The capability view is routable from INDEX.md."""
    index = (project_root() / "docs" / "INDEX.md").read_text()
    assert "CAPABILITIES.md" in index
