"""Request-scoped context values shared across layers.

Some per-call state must be visible to more than one layer.  The sudo virtual
parameter's pre-hook sets the target username, and the HTTP transport's request
hook reads it to append ``?sudo=<username>``.  That state lives here, in a leaf
module, so the transport (``client``) and the virtual-parameter layer
(``tools/virtual_params``) can share it without the transport importing
``tools`` — the layering contract in ``tests/unit/test_layer_contract.py``
depends on this indirection.

The value is a :class:`contextvars.ContextVar`: set before a call, read during
the request, cleared after the response.
"""

from __future__ import annotations

from contextvars import ContextVar

sudo_context: ContextVar[str | None] = ContextVar("sudo_context", default=None)
"""Target username for the current tool call's sudo impersonation, or ``None``.

Set by the sudo pre-hook (``tools/virtual_params.py``), read by the client's
request hook (``client.py``), cleared by the sudo post-hook.
"""

__all__ = ["sudo_context"]
