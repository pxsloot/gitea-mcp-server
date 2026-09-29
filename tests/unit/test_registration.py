"""Tests for the typed registration record contract (``registration.py``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from gitea_mcp_server.models import ToolCustomization
from gitea_mcp_server.registration import (
    CONTENT_META_KEYS,
    REGISTRATION_KEY,
    ResourceRegistration,
    ToolRegistration,
    build_content_meta,
    get_resource_registration,
    get_tool_registration,
    parse_content_meta,
    set_registration,
)


def _customization(**overrides: Any) -> ToolCustomization:
    base: dict[str, Any] = {"has_labels": True, "route_path": "/x", "route_method": "GET"}
    base.update(overrides)
    return ToolCustomization(**base)


class TestToolRegistrationRoundTrip:
    """``to_dict``/``from_dict`` are total and lossless for every kind."""

    def test_autogen_round_trip(self) -> None:
        record = ToolRegistration.for_autogen(
            customization=_customization(),
            output_schema_raw={"type": "array"},
            response_type="Issue",
            view_hints={"omit": ["body"]},
        )
        restored = ToolRegistration.from_dict(record.to_dict())
        assert restored == record
        assert restored.customization is not None
        assert restored.response_type == "Issue"
        assert restored.view_hints == {"omit": ["body"]}

    def test_synthetic_round_trip(self) -> None:
        record = ToolRegistration.for_synthetic(
            executor_id="search_tools",
            allowlist={"format", "detail"},
        )
        restored = ToolRegistration.from_dict(record.to_dict())
        assert restored == record
        assert restored.virtual_param_allowlist == frozenset({"format", "detail"})
        assert restored.executor_id == "search_tools"
        # Pending until injection resolves it.
        assert restored.virtual_params is None

    def test_proxy_round_trip(self) -> None:
        record = ToolRegistration.for_proxy()
        restored = ToolRegistration.from_dict(record.to_dict())
        assert restored == record
        assert restored.virtual_params == frozenset()

    def test_from_dict_is_total_over_record_instance(self) -> None:
        record = ToolRegistration.for_proxy()
        assert ToolRegistration.from_dict(record) is record

    def test_from_dict_reconstructs_customization_instance(self) -> None:
        record = ToolRegistration.for_autogen(
            customization=_customization(),
            output_schema_raw=None,
            response_type=None,
            view_hints=None,
        )
        restored = ToolRegistration.from_dict(record.to_dict())
        assert isinstance(restored.customization, ToolCustomization)
        assert restored.customization.has_labels is True

    def test_from_dict_accepts_customization_instance(self) -> None:
        custom = _customization()
        data = ToolRegistration.for_autogen(
            customization=custom,
            output_schema_raw=None,
            response_type=None,
            view_hints=None,
        ).to_dict()
        # In-process, meta may still carry the dataclass rather than its dict.
        data["customization"] = custom
        restored = ToolRegistration.from_dict(data)
        assert restored.customization is custom

    def test_with_injected_finalises(self) -> None:
        record = ToolRegistration.for_synthetic(executor_id="t", allowlist={"format"})
        assert not record.is_finalised()
        finalised = record.with_injected({"format", "detail"})
        assert finalised.is_finalised()
        assert finalised.virtual_params == frozenset({"format", "detail"})
        # Original is untouched (replace, not mutate).
        assert record.virtual_params is None


class TestToolRegistrationValidate:
    """The required-field matrix is enforced per tool kind."""

    def test_autogen_valid_when_finalised(self) -> None:
        record = ToolRegistration.for_autogen(
            customization=_customization(),
            output_schema_raw=None,
            response_type=None,
            view_hints=None,
        ).with_injected({"format", "detail"})
        assert record.validate() == []

    def test_pending_record_is_invalid(self) -> None:
        record = ToolRegistration.for_autogen(
            customization=_customization(),
            output_schema_raw=None,
            response_type=None,
            view_hints=None,
        )
        assert any("not finalised" in p for p in record.validate())

    def test_autogen_without_customization_is_invalid(self) -> None:
        record = ToolRegistration(wrap=True, virtual_params=frozenset({"format"}))
        assert any("customization" in p for p in record.validate())

    def test_autogen_with_executor_id_is_invalid(self) -> None:
        record = ToolRegistration(
            wrap=True,
            executor_id="x",
            customization=_customization(),
            virtual_params=frozenset({"format"}),
        )
        assert any("must not carry executor_id" in p for p in record.validate())

    def test_synthetic_valid(self) -> None:
        record = ToolRegistration.for_synthetic(
            executor_id="search_tools", allowlist={"format"}
        ).with_injected({"format"})
        assert record.validate() == []

    def test_synthetic_without_executor_id_is_invalid(self) -> None:
        record = ToolRegistration(
            wrap=True,
            synthetic=True,
            virtual_param_allowlist=frozenset({"format"}),
            virtual_params=frozenset({"format"}),
        )
        assert any("executor_id" in p for p in record.validate())

    def test_synthetic_without_allowlist_is_invalid(self) -> None:
        record = ToolRegistration(
            wrap=True,
            synthetic=True,
            executor_id="t",
            virtual_params=frozenset({"format"}),
        )
        assert any("allowlist" in p for p in record.validate())

    def test_proxy_valid(self) -> None:
        assert ToolRegistration.for_proxy().validate() == []

    def test_proxy_with_synthetic_flag_is_invalid(self) -> None:
        record = ToolRegistration(wrap=False, synthetic=True, virtual_params=frozenset())
        assert any("must not be synthetic" in p for p in record.validate())

    def test_proxy_with_executor_id_is_invalid(self) -> None:
        record = ToolRegistration(wrap=False, virtual_params=frozenset(), executor_id="x")
        assert any("must not carry executor_id" in p for p in record.validate())


class TestResourceRegistration:
    """Resource records round-trip and require the size/detail pair."""

    def test_round_trip(self) -> None:
        record = ResourceRegistration(
            size_hint="large",
            default_detail="concise",
            required_scopes=["read:repository"],
            optional_params=[{"name": "state"}],
        )
        assert ResourceRegistration.from_dict(record.to_dict()) == record

    def test_from_dict_is_total_over_record_instance(self) -> None:
        record = ResourceRegistration(size_hint="tiny", default_detail="full")
        assert ResourceRegistration.from_dict(record) is record

    def test_validate_requires_size_and_detail(self) -> None:
        problems = ResourceRegistration().validate()
        assert any("size_hint" in p for p in problems)
        assert any("default_detail" in p for p in problems)

    def test_valid_record(self) -> None:
        record = ResourceRegistration(size_hint="small", default_detail="full")
        assert record.validate() == []


class TestAccessors:
    """The sanctioned accessors read the single key and tolerate absence."""

    def test_tool_accessor_reads_key(self) -> None:
        record = ToolRegistration.for_proxy()
        component = SimpleNamespace(meta={REGISTRATION_KEY: record.to_dict()})
        assert get_tool_registration(component) == record

    def test_tool_accessor_returns_none_when_absent(self) -> None:
        assert get_tool_registration(SimpleNamespace(meta={})) is None
        assert get_tool_registration(SimpleNamespace(meta=None)) is None
        assert get_tool_registration(SimpleNamespace()) is None

    def test_resource_accessor_reads_key(self) -> None:
        record = ResourceRegistration(size_hint="tiny", default_detail="full")
        component = SimpleNamespace(meta={REGISTRATION_KEY: record.to_dict()})
        assert get_resource_registration(component) == record

    def test_resource_accessor_returns_none_when_absent(self) -> None:
        assert get_resource_registration(SimpleNamespace(meta={})) is None

    def test_set_registration_creates_meta(self) -> None:
        component = SimpleNamespace(meta=None)
        record = ToolRegistration.for_proxy()
        set_registration(component, record)
        assert component.meta is not None
        assert get_tool_registration(component) == record


class TestContentMetaHelpers:
    """The channel-3 helpers round-trip known keys and formatter extras."""

    def test_build_and_parse_round_trip(self) -> None:
        meta = build_content_meta(
            response_schema={"type": "object"},
            format_hint="labels",
            response_type="Label",
            view_hints={"flag": ["exclusive"]},
            extra={"owner": "acme", "repo": "widgets"},
        )
        assert meta is not None
        known, extra = parse_content_meta(meta)
        assert known is not None
        assert set(known) == CONTENT_META_KEYS - {"view_hints"} | {"view_hints"}
        assert known["format_hint"] == "labels"
        assert extra == {"owner": "acme", "repo": "widgets"}

    def test_build_returns_none_when_empty(self) -> None:
        assert build_content_meta() is None

    def test_parse_handles_none(self) -> None:
        assert parse_content_meta(None) == (None, None)
