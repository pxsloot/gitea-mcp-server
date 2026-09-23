"""Unit tests for gitea_mcp_server/tools/virtual_params.py.

Tests cover the three lifecycle functions (inject_into, extract_from, apply_to)
and integration with _ToolWrappingTransform._wrap().
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.models import ToolCustomization
from gitea_mcp_server.tools.virtual_params import (
    VirtualParam,
    apply_pre_hooks,
    apply_scope_filter,
    apply_to,
    extract_from,
    inject_into,
    validate_extracted,
)
from tests.helpers.spec_fixtures import make_openapi_spec

# A minimal VirtualParam entry used by lifecycle tests that patch _VIRTUAL_PARAMS.
_FORMAT_VP = VirtualParam(
    schema={"type": "string", "enum": ["json", "markdown", "raw"]},
    default="markdown",
    description="Response format control.",
)

# A second minimal entry for allowlist (only=) injection tests.
_DETAIL_VP = VirtualParam(
    schema={"type": "string", "enum": ["full", "concise"]},
    default="full",
    description="Output detail level.",
)


class TestFormatEnumSource:
    """The agent-facing ``format`` enum derives from the shared constant."""

    def test_format_enum_matches_shared_constant(self) -> None:
        """The injected ``format`` enum equals ``RESPONSE_FORMATS`` in order.

        Locks the single source of truth (#785 direction): the virtual-param
        schema, the config validator, the result pipeline, and ``read_doc`` all
        read the same constant.
        """
        from gitea_mcp_server.constants import RESPONSE_FORMATS

        params: dict[str, Any] = {}
        inject_into(params)
        assert params["properties"]["format"]["enum"] == list(RESPONSE_FORMATS)


# ---------------------------------------------------------------------------
# inject_into
# ---------------------------------------------------------------------------


class TestInjectInto:
    """Tests for inject_into - schema augmentation (mechanism, not format)."""

    def test_adds_patched_entry_when_missing(self) -> None:
        """Adds a patched virtual param when not already in properties."""
        params: dict = {"properties": {}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"test_param": _FORMAT_VP},
        ):
            inject_into(params)
        props = params["properties"]
        assert "test_param" in props
        assert props["test_param"]["type"] == "string"
        assert props["test_param"]["default"] == "markdown"

    def test_does_not_overwrite_existing_param(self) -> None:
        """Skips virtual param if tool already has a parameter with that name."""
        params: dict = {"properties": {"test_param": {"type": "integer"}}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"test_param": _FORMAT_VP},
        ):
            inject_into(params)
        assert params["properties"]["test_param"] == {"type": "integer"}

    def test_no_op_when_empty_registry(self) -> None:
        """Does nothing when _VIRTUAL_PARAMS is empty."""
        params: dict = {"properties": {}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {},
            clear=True,
        ):
            inject_into(params)
        assert params["properties"] == {}

    def test_handles_empty_parameters(self) -> None:
        """Works with an empty parameters dict, creating properties."""
        params: dict = {}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"test_param": _FORMAT_VP},
        ):
            inject_into(params)
        assert "test_param" in params["properties"]

    def test_only_allowlist_restricts_injection(self) -> None:
        """only= restricts injection to the named params (synthetic allowlist)."""
        params: dict = {"properties": {}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": _FORMAT_VP,
                "detail": _DETAIL_VP,
            },
        ):
            inject_into(params, only={"format"})
        props = params["properties"]
        assert "format" in props
        assert "detail" not in props

    def test_only_none_injects_all(self) -> None:
        """only=None (autogen) injects every visible param."""
        params: dict = {"properties": {}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": _FORMAT_VP,
                "detail": _DETAIL_VP,
            },
        ):
            inject_into(params, only=None)
        assert "format" in params["properties"]
        assert "detail" in params["properties"]

    def test_returns_injected_set(self) -> None:
        """Returns the set of param names actually written to the schema."""
        params: dict = {"properties": {}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": _FORMAT_VP,
                "detail": _DETAIL_VP,
            },
            clear=True,
        ):
            injected = inject_into(params, only=None)
        assert injected == {"format", "detail"}

    def test_returns_injected_set_excludes_shadowed(self) -> None:
        """Shadowed (pre-existing) params are not part of the injected set."""
        params: dict = {"properties": {"format": {"type": "integer"}}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": _FORMAT_VP,
                "detail": _DETAIL_VP,
            },
            clear=True,
        ):
            injected = inject_into(params, only=None)
        # format already existed (never shadowed) — only detail was written.
        assert injected == {"detail"}

    def test_returns_injected_set_excludes_predicate_gated(self) -> None:
        """Predicate-gated params (e.g. fetch_all on autogen) are excluded."""
        params: dict = {"properties": {}}
        gated = VirtualParam(
            schema={"type": "boolean"},
            default=False,
            description="Synthetic-only.",
            tool_predicate=lambda t: bool((t.meta or {}).get("_synthetic")),
        )
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": _FORMAT_VP,
                "fetch_all": gated,
            },
            clear=True,
        ):
            # Autogen tool: no _synthetic meta marker → predicate fails.
            injected = inject_into(params, tool=Tool(name="t", description="", parameters={}))
        assert injected == {"format"}
        assert "fetch_all" not in params["properties"]

    def test_default_override_does_not_touch_real_api_param(self) -> None:
        """A real API parameter named ``format`` keeps its schema and default.

        ``inject_into`` never shadows a real parameter (``only=None``) and the
        default override is resolved per *injected* param — so a real
        ``format`` query/body parameter is never hijacked to the server's
        response-format default (#785).
        """
        real_format = {"type": "string", "default": "tar.gz"}
        params: dict = {"properties": {"format": real_format}}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP},
            clear=True,
        ):
            injected = inject_into(params, default_overrides={"format": "json"})
        assert injected == set()
        assert params["properties"]["format"] == real_format

    def test_format_injected_without_static_default(self) -> None:
        """The registry does not fabricate a ``format`` default.

        ``format``'s default is server config, supplied by the caller via
        ``default_overrides``.  Without an override no ``default`` key is
        written — the spine supplies the configured value (#785).
        """
        params: dict = {}
        inject_into(params, only={"format"})
        fmt = params["properties"]["format"]
        assert "default" not in fmt
        assert fmt["enum"] == ["json", "markdown", "raw"]

    def test_default_override_sets_injected_format_default(self) -> None:
        """A supplied override becomes the injected ``format`` default."""
        params: dict = {}
        inject_into(params, only={"format"}, default_overrides={"format": "json"})
        assert params["properties"]["format"]["default"] == "json"

    def test_none_default_is_written_as_null(self) -> None:
        """``default=None`` is a real null default, not "no default".

        Only the ``_NO_DEFAULT`` sentinel omits the ``default`` key; a
        ``None`` default (e.g. ``sudo``) is written as JSON null so the
        schema keeps advertising it (#785 review F1).
        """
        null_default = VirtualParam(schema={"type": "string"}, default=None, description="")
        params: dict = {}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"sudo_like": null_default},
            clear=True,
        ):
            inject_into(params)
        assert params["properties"]["sudo_like"]["default"] is None


# ---------------------------------------------------------------------------
# validate_extracted
# ---------------------------------------------------------------------------


class TestValidateExtracted:
    """Tests for validate_extracted — pre-executor virtual-param validation.

    The registry schema is the single source for injection and validation;
    only enum-bearing params (``format``/``detail``/``content_type``) are
    constrained (#789).
    """

    def test_valid_values_pass(self) -> None:
        validate_extracted({"format": "json", "detail": "concise", "content_type": "text"})

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValidationError, match="format must be one of"):
            validate_extracted({"format": "bogus"})

    def test_invalid_detail_raises(self) -> None:
        with pytest.raises(ValidationError, match="detail must be one of"):
            validate_extracted({"detail": "bogus"})

    def test_invalid_content_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="content_type must be one of"):
            validate_extracted({"content_type": "bogus"})

    def test_none_value_rejected(self) -> None:
        """An explicit null is not a declared value — rejected, not defaulted."""
        with pytest.raises(ValidationError, match="format must be one of"):
            validate_extracted({"format": None})

    def test_params_without_enum_pass(self) -> None:
        """sudo/fetch_all declare no enum, so any value is a no-op."""
        validate_extracted({"sudo": "alice", "fetch_all": True})

    def test_unknown_key_ignored(self) -> None:
        """Non-registry keys (pipeline metadata) are ignored defensively."""
        validate_extracted({"not_a_virtual_param": "x"})

    def test_empty_noop(self) -> None:
        validate_extracted({})


# ---------------------------------------------------------------------------
# apply_pre_hooks
# ---------------------------------------------------------------------------


class TestApplyPreHooks:
    """Tests for apply_pre_hooks - pre-call side effects."""

    def test_runs_pre_hook_with_value(self) -> None:
        """Calls the pre_hook with the extracted value."""
        mock_hook = MagicMock()
        extracted = {"my_param": "hello"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "my_param": VirtualParam(
                    schema={}, default=None, description="", pre_hook=mock_hook
                ),
            },
        ):
            apply_pre_hooks(extracted)
        mock_hook.assert_called_once_with("hello", {})

    def test_no_op_when_no_extracted_params(self) -> None:
        """Does nothing when extracted is empty."""
        mock_hook = MagicMock()
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "my_param": VirtualParam(
                    schema={}, default=None, description="", pre_hook=mock_hook
                ),
            },
        ):
            apply_pre_hooks({})
        mock_hook.assert_not_called()

    def test_handles_none_pre_hook(self) -> None:
        """VirtualParam with pre_hook=None is a no-op."""
        extracted = {"my_param": "value"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "my_param": VirtualParam(schema={}, default=None, description="", pre_hook=None),
            },
        ):
            # Should not raise
            apply_pre_hooks(extracted)

    def test_runs_all_pre_hooks_in_order(self) -> None:
        """Calls multiple pre_hooks in registration order."""
        calls: list[str] = []

        def hook_a(_v: object, _kw: dict[str, Any] | None = None) -> None:
            calls.append("a")

        def hook_b(_v: object, _kw: dict[str, Any] | None = None) -> None:
            calls.append("b")

        extracted = {"a": 1, "b": 2}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "a": VirtualParam(schema={}, default=None, description="", pre_hook=hook_a),
                "b": VirtualParam(schema={}, default=None, description="", pre_hook=hook_b),
            },
        ):
            apply_pre_hooks(extracted)
        assert calls == ["a", "b"]


# ---------------------------------------------------------------------------
# sudo - context var lifecycle
# ---------------------------------------------------------------------------


class TestSudoHooks:
    """Tests that the sudo pre/post hooks manage the context var correctly."""

    def test_sudo_pre_hook_sets_context(self) -> None:
        """_sudo_pre_hook sets sudo_context to the string value."""
        from gitea_mcp_server.tools.virtual_params import (
            _sudo_pre_hook,
            sudo_context,
        )

        assert sudo_context.get() is None
        _sudo_pre_hook("alice", {})
        assert sudo_context.get() == "alice"

    def test_sudo_pre_hook_skips_none(self) -> None:
        """_sudo_pre_hook does not set context when value is None."""
        from gitea_mcp_server.tools.virtual_params import (
            _sudo_pre_hook,
            sudo_context,
        )

        sudo_context.set("previous")
        _sudo_pre_hook(None, {})
        assert sudo_context.get() == "previous"

    def test_sudo_post_hook_clears_context(self) -> None:
        """_sudo_post_hook clears sudo_context."""
        from gitea_mcp_server.tools.virtual_params import (
            _sudo_post_hook,
            sudo_context,
        )

        sudo_context.set("bob")
        result = ToolResult(content=[TextContent(type="text", text="ok")])
        returned = _sudo_post_hook(result, "bob", {})
        assert sudo_context.get() is None
        assert returned is result  # Passthrough

    def test_sudo_in_virtual_params_is_registered(self) -> None:
        """sudo is registered in _VIRTUAL_PARAMS with pre and post hooks."""
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        assert "sudo" in _VIRTUAL_PARAMS
        vp = _VIRTUAL_PARAMS["sudo"]
        assert vp.pre_hook is not None
        assert vp.post_hook is not None
        assert vp.schema == {"type": "string", "minLength": 1}
        assert vp.default is None

    def test_sudo_injected_schema_keeps_null_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real sudo entry injects ``"default": null``, not a missing key.

        ``None`` is sudo's legitimate null default; only the ``_NO_DEFAULT``
        sentinel (``format``) omits the ``default`` key (#785 review F4).
        """
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        monkeypatch.setattr(_VIRTUAL_PARAMS["sudo"], "visible", True)
        params: dict = {}
        inject_into(params, only={"sudo"})
        assert params["properties"]["sudo"]["default"] is None


# ---------------------------------------------------------------------------
# sudo - scope-gated visibility
# ---------------------------------------------------------------------------


class TestApplyScopeFilter:
    """Tests that apply_scope_filter sets visibility based on required_scope."""

    def setup_method(self) -> None:
        """Save initial sudo visibility before each test."""
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        self._original_sudo_visible = _VIRTUAL_PARAMS["sudo"].visible

    def teardown_method(self) -> None:
        """Restore original sudo visibility after each test."""
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        _VIRTUAL_PARAMS["sudo"].visible = self._original_sudo_visible

    def test_hides_sudo_when_scope_missing(self) -> None:
        """sudo hidden when 'sudo' not in available scopes."""
        apply_scope_filter({"read:repository"})
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        assert _VIRTUAL_PARAMS["sudo"].visible is False

    def test_shows_sudo_when_scope_present(self) -> None:
        """sudo shown when 'sudo' in available scopes."""
        apply_scope_filter({"sudo", "read:repository"})
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        assert _VIRTUAL_PARAMS["sudo"].visible is True

    def test_shows_sudo_when_all_present(self) -> None:
        """sudo shown when 'all' in available scopes (full-access token)."""
        apply_scope_filter({"all"})
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        assert _VIRTUAL_PARAMS["sudo"].visible is True

    def test_inject_into_skips_sudo_when_hidden(self) -> None:
        """sudo not added to tool schema when hidden by scope filter."""
        apply_scope_filter({"read:repository"})
        params: dict = {"properties": {}}
        inject_into(params)
        assert "sudo" not in params["properties"]

    def test_inject_into_includes_sudo_when_visible(self) -> None:
        """sudo added to tool schema when visible (scope present)."""
        apply_scope_filter({"sudo"})
        params: dict = {"properties": {}}
        inject_into(params)
        assert "sudo" in params["properties"]
        assert params["properties"]["sudo"]["type"] == "string"
        assert params["properties"]["sudo"]["minLength"] == 1

    def test_leaves_unrestricted_params_untouched(self) -> None:
        """Params with required_scope=None are not affected by scope filter."""
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        apply_scope_filter(set())
        # sudo has required_scope="sudo", should be hidden with empty scopes
        assert _VIRTUAL_PARAMS["sudo"].visible is False


class TestRequiredScope:
    """Tests for the required_scope field on VirtualParam."""

    def test_default_is_none(self) -> None:
        """required_scope defaults to None (no restriction)."""
        vp = VirtualParam(schema={}, default=None, description="test")
        assert vp.required_scope is None

    def test_can_be_set(self) -> None:
        """required_scope can be set to a scope string."""
        vp = VirtualParam(schema={}, default=None, description="test", required_scope="sudo")
        assert vp.required_scope == "sudo"

    def test_sudo_in_registry_has_required_scope(self) -> None:
        """sudo virtual param has required_scope='sudo'."""
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        assert _VIRTUAL_PARAMS["sudo"].required_scope == "sudo"


class TestSudoErrorPaths:
    """Error/safety path tests for sudo lifecycle."""

    def test_extract_and_pre_hook_clear_post_hook_restores(self) -> None:
        """Full lifecycle: extract sets context, apply_to clears it."""
        from gitea_mcp_server.tools.virtual_params import (
            apply_pre_hooks,
            apply_to,
            extract_from,
            sudo_context,
        )

        kwargs = {"owner": "test", "repo": "x", "sudo": "alice"}
        assert sudo_context.get() is None

        # 1. extract - pops sudo from kwargs
        extracted = extract_from(kwargs)
        assert "sudo" not in kwargs
        assert extracted == {"sudo": "alice"}

        # 2. pre-hook - sets context var
        apply_pre_hooks(extracted, kwargs)
        assert sudo_context.get() == "alice"

        # 3. post-hook - clears context var
        result = ToolResult(content=[TextContent(type="text", text="ok")])
        final = apply_to(result, extracted)
        assert sudo_context.get() is None
        assert final is result  # passthrough

    def test_extract_from_still_pops_sudo_when_hidden(self) -> None:
        """extract_from pops sudo from kwargs even when invisible."""
        from gitea_mcp_server.tools.virtual_params import (
            _VIRTUAL_PARAMS,
            extract_from,
        )

        _VIRTUAL_PARAMS["sudo"].visible = False
        kwargs = {"owner": "test", "sudo": "cheater"}
        extracted = extract_from(kwargs)
        assert "sudo" not in kwargs  # still popped from kwargs
        assert extracted == {"sudo": "cheater"}
        _VIRTUAL_PARAMS["sudo"].visible = True

    def test_post_hook_double_clear_is_safe(self) -> None:
        """Calling post_hook when context is already None is safe (no-op)."""
        from gitea_mcp_server.tools.virtual_params import (
            _sudo_post_hook,
            sudo_context,
        )

        sudo_context.set(None)
        result = ToolResult(content=[TextContent(type="text", text="ok")])
        returned = _sudo_post_hook(result, "alice", {})
        assert sudo_context.get() is None
        assert returned is result  # passthrough

    def test_extract_from_unknown_param_ignored(self) -> None:
        """Unknown params in _VIRTUAL_PARAMS are ignored by extract_from."""
        from gitea_mcp_server.tools.virtual_params import extract_from

        kwargs = {"owner": "test", "nobody_home": "x"}
        extracted = extract_from(kwargs)
        assert extracted == {}


# ---------------------------------------------------------------------------
# extract_from
# ---------------------------------------------------------------------------


class TestExtractFrom:
    """Tests for extract_from - pre-call parameter extraction (mechanism)."""

    def test_pops_patched_param_and_returns_value(self) -> None:
        """Pops 'format' from kwargs and returns {name: value}."""
        kwargs = {"owner": "test", "format": "markdown"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP},
        ):
            extracted = extract_from(kwargs)
        assert extracted == {"format": "markdown"}
        assert "format" not in kwargs

    def test_returns_empty_dict_no_virtual_params(self) -> None:
        """Returns {} when no virtual params are present."""
        kwargs = {"owner": "test", "repo": "r", "page": 1}
        extracted = extract_from(kwargs)
        assert extracted == {}

    def test_removes_only_known_virtual_params(self) -> None:
        """Pops every known virtual param from kwargs."""
        kwargs = {"owner": "test", "format": "json"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP},
        ):
            extracted = extract_from(kwargs)
        assert "format" not in kwargs
        assert len(kwargs) == 1
        assert "owner" in kwargs

    def test_only_allowlist_pops_only_allowlisted_params(self) -> None:
        """With ``only``, off-allowlist registry keys stay in kwargs."""
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP, "detail": _FORMAT_VP},
        ):
            kwargs = {"owner": "test", "format": "json", "detail": "concise"}
            extracted = extract_from(kwargs, only={"format"})

        # format is allowlisted → popped; detail is not → left for
        # validation to reject as unknown (never silently dropped).
        assert extracted == {"format": "json"}
        assert "format" not in kwargs
        assert kwargs == {"owner": "test", "detail": "concise"}

    def test_only_none_pops_every_virtual_param(self) -> None:
        """``only=None`` (autogen) pops all virtual params regardless of allowlist."""
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {"format": _FORMAT_VP, "detail": _FORMAT_VP},
        ):
            kwargs = {"owner": "test", "format": "json", "detail": "concise"}
            extracted = extract_from(kwargs, only=None)

        assert extracted == {"format": "json", "detail": "concise"}
        assert kwargs == {"owner": "test"}


# ---------------------------------------------------------------------------
# apply_to
# ---------------------------------------------------------------------------


class TestApplyTo:
    """Tests for apply_to - post-call result transformation."""

    def test_runs_post_hook_with_value(self) -> None:
        """Calls the post_hook with (result, value, all_extracted)."""
        result = ToolResult(content=[TextContent(type="text", text="hello")])
        transformed = ToolResult(content=[TextContent(type="text", text="transformed")])

        hook = MagicMock(return_value=transformed)
        extracted = {"format": "markdown"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": VirtualParam(schema={}, default="json", description="", post_hook=hook),
            },
        ):
            output = apply_to(result, extracted)

        hook.assert_called_once_with(result, "markdown", extracted)
        assert output is transformed

    def test_returns_result_when_no_extracted_params(self) -> None:
        """Returns result unchanged when extracted is empty."""
        result = ToolResult(content=[TextContent(type="text", text="hello")])
        assert apply_to(result, {}) is result

    def test_handles_none_post_hook(self) -> None:
        """VirtualParam with post_hook=None is a no-op."""
        result = ToolResult(content=[TextContent(type="text", text="hello")])
        extracted = {"format": "json"}
        with patch.dict(
            "gitea_mcp_server.tools.virtual_params._VIRTUAL_PARAMS",
            {
                "format": VirtualParam(schema={}, default="json", description="", post_hook=None),
            },
        ):
            assert apply_to(result, extracted) is result


# ---------------------------------------------------------------------------
# Integration with _wrap()
# ---------------------------------------------------------------------------


class TestWrapIntegration:
    """Tests that _ToolWrappingTransform._wrap() integrates with the VirtualParam lifecycle."""

    def _make_tool(self) -> Tool:
        """Minimal Tool stamped with the contract wrap marker."""
        return Tool(
            name="issue_list_issues",
            description="List issues in a repository.",
            parameters={"properties": {"owner": {"type": "string"}}},
            meta={
                "_contract_wrap": True,
                "_customization": ToolCustomization(
                    has_labels=False,
                    is_text_response=False,
                    route_path="/repos/{owner}/{repo}/issues",
                    route_method="GET",
                ),
            },
        )

    @pytest.mark.asyncio
    async def test_injects_format_into_parameters(self) -> None:
        """_wrap() adds the format parameter via the VirtualParam registry (delegates to inject_into)."""
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(include_defaults=False),
            response_format="markdown",
        )
        tool = self._make_tool()
        [wrapped] = await transform.list_tools([tool])

        assert "format" in wrapped.parameters.get("properties", {})
        fmt_schema = wrapped.parameters["properties"]["format"]
        assert fmt_schema["type"] == "string"
        assert fmt_schema["default"] == "markdown"
        assert "markdown" in fmt_schema["enum"]

    @pytest.mark.asyncio
    async def test_format_extracted_before_execution(self) -> None:
        """Format is stripped from kwargs before the HTTP execution path."""
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(include_defaults=False),
            response_format="markdown",
        )
        tool = self._make_tool()

        # Wrap the tool
        [wrapped] = await transform.list_tools([tool])

        # Mock the underlying execution
        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = ToolResult(
                content=[TextContent(type="text", text="result")],
                structured_content={"result": [{"id": 1}]},
            )

            # Call with format=markdown
            await wrapped.run({"owner": "test", "format": "markdown"})

            # Verify format was stripped before reaching run_with_error_handling
            call_kwargs = mock_run.call_args[0][0]
            assert "format" not in call_kwargs
            assert call_kwargs == {"owner": "test"}

    @pytest.mark.asyncio
    async def test_default_markdown_no_format_supplied(self) -> None:
        """Default markdown when format is not supplied."""
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(include_defaults=False),
            response_format="markdown",
        )
        tool = self._make_tool()

        [wrapped] = await transform.list_tools([tool])

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            expected_result = ToolResult(
                content=[TextContent(type="text", text="markdown output")],
                structured_content={"result": [{"id": 1}]},
            )
            mock_run.return_value = expected_result

            result = await wrapped.run({"owner": "test"})
            assert result.structured_content == {"result": [{"id": 1}]}

    @pytest.mark.asyncio
    async def test_invalid_format_rejected_before_execution(self) -> None:
        """An invalid format fails before the HTTP execution path (#789).

        Guards the regression where an invalid ``format`` reached ``render``
        only after the API call (and, for a write, after the side effect).
        """
        from gitea_mcp_server.server_setup.mcp_builder import _ToolWrappingTransform

        transform = _ToolWrappingTransform(
            openapi_spec=make_openapi_spec(include_defaults=False),
            response_format="markdown",
        )
        [wrapped] = await transform.list_tools([self._make_tool()])

        with patch(
            "gitea_mcp_server.server_setup.mcp_builder.run_with_error_handling",
            new_callable=AsyncMock,
        ) as mock_run:
            with pytest.raises(ValueError, match="format must be one of"):
                await wrapped.run({"owner": "test", "format": "bogus"})
            mock_run.assert_not_called()


class TestDetailSchemaSingleSource:
    """The registry ``detail`` entry derives from ``constants``."""

    def test_registry_detail_matches_constants(self) -> None:
        from gitea_mcp_server.constants import (
            DETAIL_PARAM_SCHEMA,
            DETAIL_PARAM_SCHEMA_CONCISE,
        )
        from gitea_mcp_server.tools.virtual_params import _VIRTUAL_PARAMS

        vp = _VIRTUAL_PARAMS["detail"]
        assert vp.schema == DETAIL_PARAM_SCHEMA
        assert vp.description == DETAIL_PARAM_SCHEMA["description"]
        assert vp.default == DETAIL_PARAM_SCHEMA["default"]

        # The concise variant differs only in its default.
        base = {k: v for k, v in DETAIL_PARAM_SCHEMA.items() if k != "default"}
        concise = {k: v for k, v in DETAIL_PARAM_SCHEMA_CONCISE.items() if k != "default"}
        assert concise == base
        assert DETAIL_PARAM_SCHEMA_CONCISE["default"] == "concise"

    def test_value_set_single_source(self) -> None:
        """The detail value set comes from one Literal; the enum derives."""
        from typing import get_args

        from gitea_mcp_server.constants import (
            DEFAULT_DETAIL,
            DEFAULT_DETAIL_CONCISE,
            DETAIL_PARAM_SCHEMA,
            DETAIL_PARAM_SCHEMA_CONCISE,
            DETAIL_VALUES,
            DetailLiteral,
        )

        assert get_args(DetailLiteral) == DETAIL_VALUES
        assert list(DETAIL_VALUES) == DETAIL_PARAM_SCHEMA["enum"]
        assert DETAIL_PARAM_SCHEMA["default"] == DEFAULT_DETAIL
        assert DETAIL_PARAM_SCHEMA_CONCISE["default"] == DEFAULT_DETAIL_CONCISE
