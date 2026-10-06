"""DESIGN.md's curated claims must match the real project.

``docs/DESIGN.md`` embeds module pointers and a stated pattern count -- curated
facts that rot silently when a module is renamed or a pattern is added.  Mirror
the ``ARCHITECTURE.md`` module sweep (``test_architecture_doc.py``): resolve
every module the doc names, count the patterns it claims, and fail the build on
drift.  This is the doc's own Pattern 7 -- an invariant is a check, not prose.
"""

from __future__ import annotations

import re

from tests.helpers.import_graph import production_modules, project_root, source_dir

_DOC = project_root() / "docs" / "DESIGN.md"
_MODULE_TOKEN_RE = re.compile(r"`([A-Za-z0-9_./-]+\.py)`")
_PATTERN_HEADING_RE = re.compile(r"^### \d+\. ", re.MULTILINE)
_PATTERN_COUNT_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
    r" recurring patterns\b",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _module_tokens() -> set[str]:
    """Every ``*.py`` token the doc names inside backticks."""
    return set(_MODULE_TOKEN_RE.findall(_DOC.read_text()))


def _production_surface() -> tuple[set[str], set[str]]:
    """The production modules' package-relative paths and their basenames."""
    rel = {"/".join(path.relative_to(source_dir()).parts) for path in production_modules().values()}
    return rel, {rel_path.rsplit("/", 1)[-1] for rel_path in rel}


def _resolves(token: str) -> bool:
    """A token resolves as a production module (path or basename) or a repo file."""
    rel, basenames = _production_surface()
    if token in rel or token in basenames:
        return True
    return (project_root() / token).is_file()


def test_design_module_references_resolve() -> None:
    """Every ``.py`` pointer in DESIGN.md names a real file."""
    dangling = [token for token in sorted(_module_tokens()) if not _resolves(token)]
    assert not dangling, f"DESIGN.md names files that do not exist: {dangling}"


def test_module_sweep_is_not_vacuous() -> None:
    """The sweep finds pointers, so a broken regex cannot pass silently."""
    tokens = _module_tokens()
    assert "format.py" in tokens
    assert len(tokens) >= 10


def test_stated_pattern_count_matches_sections() -> None:
    """The stated 'N recurring patterns' equals the numbered pattern sections."""
    text = _DOC.read_text()
    headings = _PATTERN_HEADING_RE.findall(text)
    match = _PATTERN_COUNT_RE.search(text)
    assert match is not None, "DESIGN.md no longer states 'N recurring patterns'"
    word = match.group(1).lower()
    assert word in _NUMBER_WORDS, f"Unknown number word in DESIGN.md: {word!r}"
    expected = _NUMBER_WORDS[word]
    assert expected == len(headings), (
        f"DESIGN.md says {word} patterns but has {len(headings)} sections"
    )
