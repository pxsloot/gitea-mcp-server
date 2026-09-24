"""Unit tests for the shared request-scoped context values (``request_context``)."""

from gitea_mcp_server.request_context import sudo_context


class TestSudoContext:
    """``sudo_context`` is a request-scoped ContextVar with a ``None`` default."""

    def test_default_is_none(self) -> None:
        """A fresh context carries no sudo target."""
        assert sudo_context.get() is None

    def test_set_then_reset_restores_previous(self) -> None:
        """Setting and resetting returns the context to its previous value."""
        token = sudo_context.set("alice")
        assert sudo_context.get() == "alice"
        sudo_context.reset(token)
        assert sudo_context.get() is None
