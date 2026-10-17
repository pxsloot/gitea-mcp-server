"""Cross-cutting invariant: the contract spine owns argument validation.

Argument validation is part of the one agent-facing contract, so it is owned by
the shared spine (``tools/contract.py``) and applied exactly once, before the
executor, identically for autogen and synthetic tools.  The single entry is
``validation.validate_call``; executors never validate.

This guard fails if an executor re-introduces a validation call and re-splits
the concern.  It is AST-based (robust to whitespace and comments) and walks the
filesystem-discovered production module set, so the checked set cannot drift
from a hand-maintained list.

Blind spots (aliasing, dynamic dispatch, and a direct call to a lower-level
primitive such as ``validate_enum`` or a ``SINGLE_VALIDATORS`` entry) are out of
scope — this is a reintroduction deny-list on the public entry, not taint
analysis.  The same residual shape is documented on
:func:`~tests.helpers.ast_guards.iter_call_sites` and on
``test_progress_contract.py``.
"""

from __future__ import annotations

from tests.helpers.ast_guards import iter_call_sites
from tests.helpers.import_graph import production_modules

# The single public validation entry, and the only production module allowed
# to call it.
_ENTRY = "validate_call"
_SPINE = "gitea_mcp_server.tools.contract"


def _validation_call_sites(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, called_name)`` for every ``validate_call`` call.

    Passing ``attrs`` as well as ``names`` catches the qualified
    ``validation.validate_call(...)`` form, not only the bare-name form.
    """
    return iter_call_sites(source, names=frozenset({_ENTRY}), attrs=frozenset({_ENTRY}))


def test_only_the_spine_calls_the_validation_entry() -> None:
    """Exactly the spine calls ``validate_call``.

    Set equality both ways: no unexpected caller (subset), and the expected
    caller is present (superset) so removing the spine's call fails here
    instead of passing vacuously.  The entry's own definition is a
    ``FunctionDef``, not a ``Call``, so it is never matched.
    """
    callers = {
        module
        for module, path in sorted(production_modules().items())
        if _validation_call_sites(path.read_text())
    }
    assert callers == {_SPINE}, (
        "Argument validation must be called only by the contract spine "
        f"({_SPINE}, via {_ENTRY}).  Found callers: {sorted(callers)}"
    )


def test_scanner_detects_planted_violation() -> None:
    """The detector is not vacuous: it flags both call forms with line numbers."""
    source = (
        "async def executor(kwargs):\n"
        "    validate_call(kwargs)\n"
        "    validation.validate_call(kwargs)\n"
    )
    assert _validation_call_sites(source) == [
        (2, "validate_call"),
        (3, "validate_call"),
    ]
