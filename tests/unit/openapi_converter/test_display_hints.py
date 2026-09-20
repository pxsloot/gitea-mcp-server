"""Unit tests for display-view hints (openapi_converter.display_hints).

Covers ``stamp_display_hints`` — the pre-wrap pass that stamps
``x-mcp-view-omit`` / ``x-mcp-view-compact`` on operations, keyed by the
response type, for the generic schema-anchored markdown view (#771).  The
hints are a curated deficiency list; validation must fail loudly when a hint
names a property the schema does not define.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.openapi_converter.display_hints import (
    VIEW_COMPACT_KEY,
    VIEW_OMIT_KEY,
    _type_properties,
    _validate_hints,
    stamp_display_hints,
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


def _object_op(op_id: str, ref: str) -> dict[str, Any]:
    return {
        "operationId": op_id,
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}
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


class TestStampDisplayHints:
    def test_stamps_omit_and_compact_for_known_type(self) -> None:
        spec = make_openapi_spec(
            paths={"/issues": {"get": _list_op("issueListIssues", "Issue")}},
            components={
                "schemas": {
                    "Issue": {
                        "type": "object",
                        "properties": {
                            "number": {},
                            "title": {},
                            "url": {},
                            "user": {"$ref": "#/components/schemas/User"},
                        },
                    }
                }
            },
        )
        stamp_display_hints(spec)
        op = cast("dict[str, Any]", spec["paths"]["/issues"]["get"])
        assert "url" in op[VIEW_OMIT_KEY]
        assert "user" in op[VIEW_COMPACT_KEY]

    def test_unknown_type_gets_no_hints(self) -> None:
        spec = make_openapi_spec(
            paths={"/widgets": {"get": _list_op("widgetList", "Widget")}},
            components={"schemas": {"Widget": {"type": "object", "properties": {"name": {}}}}},
        )
        stamp_display_hints(spec)
        op = spec["paths"]["/widgets"]["get"]
        assert VIEW_OMIT_KEY not in op
        assert VIEW_COMPACT_KEY not in op

    def test_object_response_also_stamped(self) -> None:
        spec = make_openapi_spec(
            paths={"/repo": {"get": _object_op("repoGet", "Repository")}},
            components={
                "schemas": {
                    "Repository": {
                        "type": "object",
                        "properties": {"name": {}, "url": {}, "owner": {}},
                    }
                }
            },
        )
        stamp_display_hints(spec)
        op = cast("dict[str, Any]", spec["paths"]["/repo"]["get"])
        assert "url" in op[VIEW_OMIT_KEY]
        assert "owner" in op[VIEW_COMPACT_KEY]

    def test_no_primary_type_not_stamped(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/ping": {
                    "get": {
                        "operationId": "ping",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {}}
                                    }
                                }
                            }
                        },
                    }
                }
            }
        )
        stamp_display_hints(spec)
        assert VIEW_OMIT_KEY not in spec["paths"]["/ping"]["get"]

    def test_non_dict_path_item_skipped(self) -> None:
        """A malformed path item is skipped, not crashed on."""
        spec = make_openapi_spec(paths={"/x": "not-a-dict"})
        stamp_display_hints(spec)  # must not raise

    def test_non_operation_keys_skipped(self) -> None:
        """Non-HTTP-method keys (e.g. ``parameters``) are skipped."""
        spec = make_openapi_spec(
            paths={
                "/x": {
                    "parameters": [{"name": "p", "in": "query"}],
                    "get": _list_op("x", "Widget"),
                }
            },
            components={"schemas": {"Widget": {"type": "object", "properties": {"name": {}}}}},
        )
        stamp_display_hints(spec)  # must not raise


class TestValidateHints:
    def _full_spec(self) -> OpenAPISpec:
        """A spec defining every curated type with all hinted properties."""
        from gitea_mcp_server.openapi_converter.display_hints import (
            _VIEW_COMPACT,
            _VIEW_OMIT,
        )

        schemas: dict[str, Any] = {}
        for type_name in set(_VIEW_OMIT) | set(_VIEW_COMPACT):
            props: dict[str, Any] = {p: {} for p in _VIEW_OMIT.get(type_name, ())}
            props.update({p: {} for p in _VIEW_COMPACT.get(type_name, ())})
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
            _validate_hints(spec)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [], [r.getMessage() for r in errors]

    def test_real_spec_stamps_issue_and_pull_hints(
        self, swagger_spec_fixture: dict[str, Any]
    ) -> None:
        """The real spec stamps the curated hints on Issue/PullRequest ops."""
        from gitea_mcp_server.openapi_converter.core import convert_swagger_to_openapi_v3

        spec = cast(
            "OpenAPISpec",
            convert_swagger_to_openapi_v3(cast("SwaggerV2Spec", swagger_spec_fixture)),
        )
        stamped = 0
        for path_item in spec["paths"].values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                if operation.get("x-response-type") == "Issue":
                    assert "body" in operation[VIEW_OMIT_KEY]
                    assert operation[VIEW_COMPACT_KEY]["pull_request"] == "merged"
                    stamped += 1
        assert stamped > 0

    def test_clean_spec_no_errors(self, caplog: pytest.LogCaptureFixture) -> None:
        spec = self._full_spec()
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            _validate_hints(spec)
        assert caplog.text == ""

    def test_unknown_property_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A hint naming a property the schema lacks fails loudly (#767)."""
        spec = self._full_spec()
        # Drop a property the curated table names.
        del spec["components"]["schemas"]["Issue"]["properties"]["url"]
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            _validate_hints(spec)
        assert "unknown property 'url'" in caplog.text

    def test_undefined_type_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        spec = make_openapi_spec()
        with caplog.at_level(
            logging.ERROR, logger="gitea_mcp_server.openapi_converter.display_hints"
        ):
            _validate_hints(spec)
        assert "undefined type" in caplog.text
