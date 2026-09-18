"""Cross-cutting invariant: post-conversion specs go through the factory.

``docs/testing/FIXTURES.md`` defines the convention: every valid
post-conversion spec is built with ``make_openapi_spec()``, which returns a
typed ``OpenAPISpec`` and hides the single ``cast()``.  The former
annotated-inline-dict pattern duplicates that cast at every call site and
lets the suite drift.

This AST guard walks ``tests/`` for those annotated literals and fails with
``file:line`` locations, so the convention cannot silently re-drift after the
issue #762 migration.  It is deliberately AST-based rather than a text
grep: it is robust to whitespace and string annotations, and it does not
match prose or comments.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent

_ANNOTATION = "OpenAPISpec"


def _annotation_name(node: ast.expr) -> str | None:
    """Return the referenced name for a simple annotation expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_violations(root: Path) -> list[str]:
    """Return ``file:line`` for every annotated spec dict literal under root."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            if _annotation_name(node.annotation) != _ANNOTATION:
                continue
            if isinstance(node.value, ast.Dict):
                rel = path.relative_to(root.parent).as_posix()
                violations.append(f"{rel}:{node.lineno}")
    return violations


def test_no_inline_openapi_spec_annotations() -> None:
    """No test may annotate a dict literal as a typed spec (use the factory)."""
    violations = _iter_violations(TESTS_ROOT)
    assert not violations, (
        "Inline spec dict literals found (use make_openapi_spec() instead; "
        "see docs/testing/FIXTURES.md):\n  " + "\n  ".join(violations)
    )


def test_scanner_detects_planted_violation(tmp_path: Path) -> None:
    """Negative control: the scanner must flag the pattern, not pass vacuously."""
    planted = tmp_path / "test_planted.py"
    planted.write_text(f"spec: {_ANNOTATION} = {{}}\n")
    violations = _iter_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0].endswith("test_planted.py:1")
