"""Unit tests for display-view hints (openapi_converter.display_hints).

Covers the curated deficiency tables and their two consumers: the pre-wrap
``validate_display_hints`` pass (validates every hint against the schema and
fails loudly on drift) and ``view_hints_for`` (the registration-time resolver
whose result travels in ``tool.meta["view_hints"]`` / resource content meta,
#775).  The hints are not stamped on operations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.openapi_converter.display_hints import (
    _type_properties,
    validate_display_hints,
    view_hints_for,
)
from gitea_mcp_server.openapi_converter.type_references import (
    primary_type,
    success_schema,
)
from tests.helpers.spec_fixtures import make_openapi_spec

if TYPE_CHECKING:
    import pytest

    from gitea_mcp_server.openapi_types import OpenAPISpec, SwaggerV2Spec


def _list_op(op_id: str, ref: str) -> dict[str, Any]:
    return {
        "operationId": op_id,
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "array",
                            "items": {"$ref": f"#/components/schemas/{ref}"},
                        }
                    }
                },
            }
        },
    }


class TestPrimaryType:
    def test_array_element_type(self) -> None:
        schema = {"type": "array", "items": {"$ref": "#/components/schemas/Issue"}}
        assert primary_type(schema) == "Issue"

    def test_object_type(self) -> None:
        assert primary_type({"$ref": "#/components/schemas/Repository"}) == "Repository"

    def test_none_schema(self) -> None:
        assert primary_type(None) is None

    def test_inline_schema_no_type(self) -> None:
        assert primary_type({"type": "object", "properties": {}}) is None


class TestTypeProperties:
    def test_returns_property_names(self) -> None:
        spec = make_openapi_spec(
            components={"schemas": {"Widget": {"type": "object", "properties": {"a": {}, "b": {}}}}}
        )
        assert _type_properties(spec, "Widget") == {"a", "b"}

    def test_unknown_type_returns_none(self) -> None:
        assert _type_properties(make_openapi_spec(), "Nope") is None

    def test_type_without_properties_returns_none(self) -> None:
        spec = make_openapi_spec(components={"schemas": {"Widget": {"type": "object"}}})
        assert _type_properties(spec, "Widget") is None

    def test_missing_schemas_returns_none(self) -> None:
        spec = make_openapi_spec(components={"schemas": "not-a-dict"})
        assert _type_properties(spec, "Widget") is None


class TestSuccessSchemaGuards:
    def test_missing_path_returns_none(self) -> None:

        assert success_schema(make_openapi_spec(), "/nope", "get") is None

    def test_non_dict_path_item_returns_none(self) -> None:

        spec = make_openapi_spec(paths={"/x": "not-a-dict"})
        assert success_schema(spec, "/x", "get") is None

    def test_missing_operation_returns_none(self) -> None:

        spec = make_openapi_spec(paths={"/x": {"get": _list_op("x", "Widget")}})
        assert success_schema(spec, "/x", "post") is None

    def test_response_ref_resolved(self) -> None:

        spec = make_openapi_spec(
            paths={
                "/x": {
                    "get": {
                        "operationId": "x",
                        "responses": {"200": {"$ref": "#/components/responses/Ok"}},
                    }
                }
            },
            components={
                "responses": {
                    "Ok": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}
                        }
                    }
                }
            },
        )
        schema = success_schema(spec, "/x", "get")
        assert schema == {"$ref": "#/components/schemas/Widget"}

    def test_unresolvable_response_ref_returns_none(self) -> None:

        spec = make_openapi_spec(
            paths={
                "/x": {
                    "get": {
                        "operationId": "x",
                        "responses": {"200": {"$ref": "#/components/responses/Missing"}},
                    }
                }
            }
        )
        assert success_schema(spec, "/x", "get") is None

    def test_non_dict_responses_returns_none(self) -> None:

        spec = make_openapi_spec(paths={"/x": {"get": {"operationId": "x", "responses": "nope"}}})
        assert success_schema(spec, "/x", "get") is None

    def test_non_dict_content_returns_none(self) -> None:

        spec = make_openapi_spec(
            paths={
                "/x": {
                    "get": {
                        "operationId": "x",
                        "responses": {"200": {"content": "nope"}},
                    }
                }
            }
        )
        assert success_schema(spec, "/x", "get") is None

    def test_non_dict_json_content_returns_none(self) -> None:

        spec = make_openapi_spec(
            paths={
                "/x": {
                    "get": {
                        "operationId": "x",
                        "responses": {"200": {"content": {"application/json": "nope"}}},
                    }
                }
            }
        )
        assert success_schema(spec, "/x", "get") is None

    def test_non_dict_schema_returns_none(self) -> None:

        spec = make_openapi_spec(
            paths={
                "/x": {
                    "get": {
                        "operationId": "x",
                        "responses": {"200": {"content": {"application/json": {"schema": "nope"}}}},
                    }
                }
            }
        )
        assert success_schema(spec, "/x", "get") is None


class TestViewHintsFor:
    """``view_hints_for`` resolves the curated tables by type name.

    The resolver is the registration-time lookup (#775); its result travels
    in ``tool.meta["view_hints"]`` / resource content meta, so the render path
    never reads hints off the spec.
    """

    def test_issue_resolves_full_hint_set(self) -> None:
        hints = view_hints_for("Issue")
        assert hints is not None
        assert "body" in hints["omit"]
        assert hints["compact"]["labels"] == "name"
        assert hints["compact"]["milestone"] == "title"
        assert "pull_request" in hints["flag"]

    def test_repository_resolves(self) -> None:
        hints = view_hints_for("Repository")
        assert hints is not None
        assert "clone_url" in hints["omit"]
        assert hints["compact"]["owner"] is None

    def test_uncurated_type_returns_none(self) -> None:
        assert view_hints_for("Widget") is None

    def test_empty_or_none_type_returns_none(self) -> None:
        assert view_hints_for(None) is None
        assert view_hints_for("") is None

    def test_shared_constant_per_type(self) -> None:
        """The resolver hands out one constant per type — read-only, no copies."""
        assert view_hints_for("Issue") is view_hints_for("Issue")

    def test_every_curated_type_is_resolvable(self) -> None:
        from gitea_mcp_server.openapi_converter.display_hints import (
            _VIEW_COMPACT,
            _VIEW_FLAG,
            _VIEW_OMIT,
        )

        for type_name in set(_VIEW_OMIT) | set(_VIEW_COMPACT) | set(_VIEW_FLAG):
            hints = view_hints_for(type_name)
            assert hints is not None, type_name
            assert hints["omit"] == list(_VIEW_OMIT.get(type_name, ()))
            assert hints["compact"] == dict(_VIEW_COMPACT.get(type_name, {}))
            assert hints["flag"] == list(_VIEW_FLAG.get(type_name, ()))


class TestValidateHints:
    def _full_spec(self) -> OpenAPISpec:
        """A spec defining every curated type with all hinted properties."""
        from gitea_mcp_server.openapi_converter.display_hints import (
            _VIEW_COMPACT,
            _VIEW_FLAG,
            _VIEW_OMIT,
        )

        schemas: dict[str, Any] = {}
        for type_name in set(_VIEW_OMIT) | set(_VIEW_COMPACT) | set(_VIEW_FLAG):
            props: dict[str, Any] = {p: {} for p in _VIEW_OMIT.get(type_name, ())}
            props.update({p: {} for p in _VIEW_COMPACT.get(type_name, ())})
            props.update({p: {} for p in _VIEW_FLAG.get(type_name, ())})
            schemas[type_name] = {"type": "object", "properties": props}
        return make_openapi_spec(components={"schemas": schemas})

    def test_real_spec_curated_hints_are_clean(
        self, swagger_spec_fixture: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        """The curated tables validate against the real Gitea spec.

        This is the conformance guard the old per-whitelist drift lacked: a
        spec upgrade that renames or drops a hinted field fails here, not
        silently in agent output.
        """
        from gitea_mcp_server.openapi_converter.core import convert_swagger_to_openapi_v3

        spec = cast(
            "OpenAPISpec",
            convert_swagger_to_openapi_v3(cast("SwaggerV2Spec", swagger_spec_fixture)),
        )
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            validate_display_hints(spec)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [], [r.getMessage() for r in errors]

    def test_clean_spec_no_errors(self, caplog: pytest.LogCaptureFixture) -> None:
        spec = self._full_spec()
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            validate_display_hints(spec)
        assert caplog.text == ""

    def test_unknown_property_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A hint naming a property the schema lacks fails loudly (#767)."""
        spec = self._full_spec()
        # Drop a property the curated table names.
        del spec["components"]["schemas"]["Issue"]["properties"]["url"]
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            validate_display_hints(spec)
        assert "unknown property 'url'" in caplog.text

    def test_undefined_type_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        spec = make_openapi_spec()
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            validate_display_hints(spec)
        assert "undefined type" in caplog.text
