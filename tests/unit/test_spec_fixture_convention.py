"""Cross-cutting invariant: post-conversion specs go through the factory.

``docs/testing/FIXTURES.md`` defines the convention: every valid
post-conversion spec is built with ``make_openapi_spec()``, which returns a
typed ``OpenAPISpec`` and hides the single ``cast()``.  Two inline-dict
patterns violate it:

- an annotated literal: ``spec: OpenAPISpec = {...}``
- a keyword argument: ``fn(openapi_spec={...})``

This AST guard walks ``tests/`` for both and fails with ``file:line``
locations, so the convention cannot silently re-drift after the issue #762
migration.  It is deliberately AST-based rather than a text grep: it is
robust to whitespace and string annotations, and it does not match prose or
comments.

Deliberately malformed specs wrap the literal in ``cast(...)``, so the
flagged position is a call, not a dict literal — those pass.

Known blind spots (not detected here; mypy rejects most of them anyway):

- a dict literal passed positionally: ``fn({"openapi": ...})``
- an unannotated local built first: ``spec = {...}; fn(spec)``
- an import alias for the type: ``from ... import OpenAPISpec as Spec; spec: Spec = {...}``
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent

_ANNOTATION = "OpenAPISpec"
_SPEC_ARG = "openapi_spec"


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
    """Return ``file:line (reason)`` for every inline spec dict literal under root."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(root.parent).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign):
                if _annotation_name(node.annotation) == _ANNOTATION and isinstance(
                    node.value, ast.Dict
                ):
                    violations.append(f"{rel}:{node.lineno} (annotated literal)")
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == _SPEC_ARG and isinstance(keyword.value, ast.Dict):
                        violations.append(f"{rel}:{node.lineno} (inline {_SPEC_ARG}= literal)")
    return violations


def test_no_inline_openapi_spec_dicts() -> None:
    """No test may build a spec as an inline dict literal (use the factory)."""
    violations = _iter_violations(TESTS_ROOT)
    assert not violations, (
        "Inline spec dict literals found (use make_openapi_spec() instead — "
        "include_defaults=False when the exact key set matters — or "
        "cast('OpenAPISpec', ...) for deliberately malformed shapes; see "
        "docs/testing/FIXTURES.md):\n  " + "\n  ".join(violations)
    )


def test_scanner_detects_planted_violation(tmp_path: Path) -> None:
    """Negative control: both inline patterns are flagged, not passed vacuously."""
    planted = tmp_path / "test_planted.py"
    planted.write_text(f"spec: {_ANNOTATION} = {{}}\nfn({_SPEC_ARG}={{}})\n")
    violations = _iter_violations(tmp_path)
    assert len(violations) == 2
    assert "test_planted.py:1 (annotated literal)" in violations[0]
    assert f"test_planted.py:2 (inline {_SPEC_ARG}= literal)" in violations[1]


def test_scanner_allows_factory_and_cast(tmp_path: Path) -> None:
    """The scanner must not flag factory calls or cast() malformed shapes."""
    allowed = tmp_path / "test_allowed.py"
    allowed.write_text(
        "from tests.helpers.spec_fixtures import make_openapi_spec\n"
        "a = make_openapi_spec(paths={})\n"
        f"b = cast('{_ANNOTATION}', {{}})\n"
        "fn(openapi_spec=make_openapi_spec())\n"
        f"fn(openapi_spec=cast('{_ANNOTATION}', {{'paths': 'bad'}}))\n"
    )
    assert _iter_violations(tmp_path) == []
