"""Cross-cutting invariant: the contract spine owns the MCP progress lifecycle.

MCP progress is a client-facing signal, so it is part of the one agent-facing
contract.  The shared spine (``tools/contract.py``) is the only production
emitter: it reports a **start** signal before the executor and a **terminal**
signal after a successful render, identically for autogen and synthetic tools.

This guard fails if an executor re-introduces a progress call and re-splits the
lifecycle.  It is AST-based (robust to whitespace and comments) and walks the
filesystem-discovered production module set, so the checked set cannot drift
from a hand-maintained list.  Blind spots (aliased or dynamic dispatch) are
documented on :func:`~tests.helpers.ast_guards.iter_call_sites` — this is a
reintroduction deny-list, mirroring the ad-hoc-meta guard in
``test_registration.py``.
"""

from __future__ import annotations

from tests.helpers.ast_guards import iter_call_sites
from tests.helpers.import_graph import production_modules

# The spine's emission helper, and the raw MCP call the helper wraps.
_SAFE_HELPER = "safe_ctx_report_progress"
_DIRECT = "report_progress"

_SPINE = "gitea_mcp_server.tools.contract"
_WRAPPER = "gitea_mcp_server.context_utils"

# Module -> the emission kinds it is allowed to contain.  The spine emits via
# the safe helper; only the helper's own definition may call ``ctx.report_progress``.
_ALLOWED: dict[str, frozenset[str]] = {
    _SPINE: frozenset({"safe helper call"}),
    _WRAPPER: frozenset({"direct call"}),
}


def _progress_emitters(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, kind)`` for every progress emission in *source*.

    The kind is derived from the matched callee — :func:`iter_call_sites`
    returns the matched identifier (``safe_ctx_report_progress`` or
    ``report_progress``), not a domain kind.
    """
    kinds = {_SAFE_HELPER: "safe helper call", _DIRECT: "direct call"}
    return [
        (line, kinds[name])
        for line, name in iter_call_sites(
            source,
            names=frozenset({_SAFE_HELPER}),
            attrs=frozenset({_DIRECT}),
        )
    ]


def test_only_the_spine_and_the_wrapper_emit() -> None:
    """Exactly the spine and the safe wrapper emit progress, each as expected.

    Set equality both ways: no unexpected emitter (subset), and no expected
    emitter missing (superset).  The latter means removing the spine's emission
    fails here instead of passing vacuously.
    """
    emitters: dict[str, list[tuple[int, str]]] = {}
    for module, path in sorted(production_modules().items()):
        findings = _progress_emitters(path.read_text())
        if findings:
            emitters[module] = findings

    assert set(emitters) == set(_ALLOWED), (
        "Progress must be emitted only by the contract spine "
        f"({_SPINE}, via {_SAFE_HELPER}) and wrapped only by {_WRAPPER} "
        f"({_DIRECT}).  Found emitters: {emitters}"
    )
    for module, findings in emitters.items():
        kinds = {kind for _, kind in findings}
        assert kinds <= _ALLOWED[module], (
            f"{module} emitted progress with unexpected form(s): {sorted(kinds - _ALLOWED[module])}"
        )


def test_scanner_detects_planted_violation() -> None:
    """The detector is not vacuous: it flags both emission forms with line numbers."""
    source = (
        "async def executor(ctx):\n"
        "    ctx.report_progress(progress=1.0)\n"
        "    await safe_ctx_report_progress(ctx, progress=1.0)\n"
    )
    assert _progress_emitters(source) == [
        (2, "direct call"),
        (3, "safe helper call"),
    ]
