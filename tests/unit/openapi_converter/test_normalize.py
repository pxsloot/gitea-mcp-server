"""Unit tests for spec normalization (openapi_converter/normalize.py).

Tests the normalization rules:
- Rule A: snake_case parameter/body-property renames (query/header/cookie/body).
- Rule B: boolean-check response annotation.
- Rule C: wildcard path-param annotation (source-driven exception).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from gitea_mcp_server.openapi_converter.normalize import (
    _annotate_boolean_checks,
    _annotate_wildcard_path_params,
    _get_body_schema,
    _is_boolean_check_operation,
    _is_snake_case,
    _merge_rename_map,
    _normalize_operation_body,
    _normalize_operation_parameters,
    normalize_spec,
)
from tests.helpers.spec_fixtures import make_openapi_spec

if TYPE_CHECKING:
    import pytest


class TestIsSnakeCase:
    def test_snake_case_returns_true(self) -> None:
        assert _is_snake_case("owner")
        assert _is_snake_case("delete_branch_after_merge")
        assert _is_snake_case("a1_b2")

    def test_non_snake_case_returns_false(self) -> None:
        assert not _is_snake_case("Do")
        assert not _is_snake_case("MergeCommitID")
        assert not _is_snake_case("includeDesc")
        assert not _is_snake_case("pageName")
        assert not _is_snake_case("repository-id")
        assert not _is_snake_case("status-types")


class TestNormalizeOperationParameters:
    def test_renames_query_param(self) -> None:
        operation = {
            "parameters": [
                {"name": "includeDesc", "in": "query", "schema": {"type": "boolean"}},
            ],
        }
        rename_map = _normalize_operation_parameters(operation)
        assert rename_map == {"include_desc": "includeDesc"}
        assert operation["parameters"][0]["name"] == "include_desc"

    def test_leaves_snake_case_untouched(self) -> None:
        operation = {
            "parameters": [
                {"name": "owner", "in": "query", "schema": {"type": "string"}},
                {"name": "limit", "in": "query", "schema": {"type": "integer"}},
            ],
        }
        rename_map = _normalize_operation_parameters(operation)
        assert rename_map == {}
        assert [p["name"] for p in operation["parameters"]] == ["owner", "limit"]

    def test_path_params_not_renamed(self) -> None:
        """Path params are deferred to issue #734."""
        operation = {
            "parameters": [
                {"name": "pageName", "in": "path", "schema": {"type": "string"}},
            ],
        }
        rename_map = _normalize_operation_parameters(operation)
        assert rename_map == {}
        assert operation["parameters"][0]["name"] == "pageName"

    def test_skips_non_dict_params(self) -> None:
        """A non-dict entry in the parameters list is skipped."""
        operation = {
            "parameters": [
                "not-a-dict",
                {"name": "includeDesc", "in": "query", "schema": {"type": "boolean"}},
            ],
        }
        rename_map = _normalize_operation_parameters(operation)
        assert rename_map == {"include_desc": "includeDesc"}

    def test_skips_params_without_name(self) -> None:
        """A param with a missing or empty name is skipped."""
        operation = {
            "parameters": [
                {"in": "query", "schema": {"type": "string"}},
                {"name": "", "in": "query", "schema": {"type": "string"}},
            ],
        }
        rename_map = _normalize_operation_parameters(operation)
        assert rename_map == {}

    def test_skips_name_unchanged_by_camel_to_snake(self) -> None:
        """A name camel_to_snake leaves unchanged (e.g. 'foo-bar') is not renamed."""
        operation = {
            "parameters": [
                {"name": "foo-bar", "in": "query", "schema": {"type": "string"}},
            ],
        }
        rename_map = _normalize_operation_parameters(operation)
        assert rename_map == {}
        assert operation["parameters"][0]["name"] == "foo-bar"


class TestMergeRenameMap:
    def test_empty_map_is_noop(self) -> None:
        """An empty rename map leaves the operation untouched."""
        operation: dict[str, Any] = {}
        _merge_rename_map(operation, {})
        assert "x-param-rename" not in operation


class TestGetBodySchema:
    def test_content_not_dict_returns_none(self) -> None:
        """A requestBody whose content is not a dict yields no body schema."""
        operation = {"requestBody": {"content": "not-a-dict"}}
        assert _get_body_schema(operation, make_openapi_spec()) is None

    def test_json_content_not_dict_returns_none(self) -> None:
        """A requestBody whose application/json entry is not a dict yields None."""
        operation = {"requestBody": {"content": {"application/json": "not-a-dict"}}}
        assert _get_body_schema(operation, make_openapi_spec()) is None

    def test_resolves_ref_body_and_writes_back(self) -> None:
        """A ``$ref`` body is resolved (deep-copied) and written back.

        Rule A must be self-sufficient: operations without path parameters
        (e.g. ``POST /markdown``) are never visited by collision resolution,
        so ``_get_body_schema`` resolves the ``$ref`` itself.
        """
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "MarkdownOption": {
                        "type": "object",
                        "properties": {
                            "Context": {"type": "string"},
                            "Mode": {"type": "string"},
                        },
                    },
                },
            },
        )
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/MarkdownOption"},
                    },
                },
            },
        }
        schema = _get_body_schema(operation, spec)
        assert schema is not None
        assert set(schema.get("properties", {}).keys()) == {"Context", "Mode"}
        # Written back into the operation.
        written = operation["requestBody"]["content"]["application/json"]["schema"]
        assert written is schema
        # The shared component is NOT mutated (deep copy).
        comp = spec["components"]["schemas"]["MarkdownOption"]
        assert set(comp.get("properties", {}).keys()) == {"Context", "Mode"}

    def test_unresolvable_ref_returns_ref_dict(self) -> None:
        """An unresolvable ``$ref`` is left as-is (no crash)."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/DoesNotExist"},
                    },
                },
            },
        }
        schema = _get_body_schema(operation, make_openapi_spec())
        assert schema == {"$ref": "#/components/schemas/DoesNotExist"}


class TestNormalizeOperationBody:
    def test_renames_body_properties_and_required(self) -> None:
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["Do", "delete_branch_after_merge"],
                            "properties": {
                                "Do": {"type": "string", "enum": ["merge"]},
                                "MergeCommitID": {"type": "string"},
                                "delete_branch_after_merge": {"type": "boolean"},
                            },
                        },
                    },
                },
            },
        }
        rename_map = _normalize_operation_body(operation, make_openapi_spec())
        assert rename_map == {
            "do": "Do",
            "merge_commit_id": "MergeCommitID",
        }
        schema = cast(
            "dict[str, Any]",
            operation["requestBody"]["content"]["application/json"]["schema"],
        )
        assert set(schema["properties"].keys()) == {
            "do",
            "merge_commit_id",
            "delete_branch_after_merge",
        }
        # Required list updated to the new names.
        assert "do" in schema["required"]
        assert "Do" not in schema["required"]
        assert "delete_branch_after_merge" in schema["required"]

    def test_no_body_returns_empty(self) -> None:
        operation: dict[str, Any] = {}
        assert _normalize_operation_body(operation, make_openapi_spec()) == {}

    def test_leaves_snake_case_body_untouched(self) -> None:
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "force_merge": {"type": "boolean"},
                                "head_commit_id": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
        assert _normalize_operation_body(operation, make_openapi_spec()) == {}

    def test_properties_not_dict_returns_empty(self) -> None:
        """A body schema whose properties is not a dict yields no renames."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "properties": "not-a-dict"},
                    },
                },
            },
        }
        assert _normalize_operation_body(operation, make_openapi_spec()) == {}

    def test_skips_prop_unchanged_by_camel_to_snake(self) -> None:
        """A body property camel_to_snake leaves unchanged is not renamed."""
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"foo-bar": {"type": "string"}},
                        },
                    },
                },
            },
        }
        assert _normalize_operation_body(operation, make_openapi_spec()) == {}

    def test_overwrite_collision_warns_and_skips(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A body with both ``Do`` and ``do`` warns and skips, never overwrites.

        Renaming ``Do`` → ``do`` would silently overwrite the existing ``do``
        property.  The rule must warn loudly and skip instead of dropping data.
        """
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "Do": {"type": "string"},
                                "do": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
        with caplog.at_level(logging.WARNING):
            rename_map = _normalize_operation_body(operation, make_openapi_spec())
        assert rename_map == {}
        schema = cast(
            "dict[str, Any]",
            operation["requestBody"]["content"]["application/json"]["schema"],
        )
        # Both properties survive; nothing was overwritten.
        assert set(schema["properties"].keys()) == {"Do", "do"}
        assert "already exists" in caplog.text

    def test_no_required_key_not_fabricated(self) -> None:
        """A body with renames but no ``required`` key stays without one.

        The rule must not write an empty ``required: []`` onto a schema that
        never declared ``required`` — that would fabricate schema structure
        FastMCP and agents did not ask for.
        """
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "Do": {"type": "string"},
                                "MergeCommitID": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
        rename_map = _normalize_operation_body(operation, make_openapi_spec())
        assert rename_map == {"do": "Do", "merge_commit_id": "MergeCommitID"}
        schema = cast(
            "dict[str, Any]",
            operation["requestBody"]["content"]["application/json"]["schema"],
        )
        assert "required" not in schema

    def test_ref_body_renamed_without_path_params(self) -> None:
        """A ``$ref`` body on an operation without path params is normalized.

        Regression: collision resolution only inlines ``$ref`` bodies for
        operations *with* path parameters, so ``POST /markdown`` (no path
        params) was skipped and exposed PascalCase params.  Rule A must be
        self-sufficient.
        """
        spec = make_openapi_spec(
            components={
                "schemas": {
                    "MarkdownOption": {
                        "type": "object",
                        "properties": {
                            "Context": {"type": "string"},
                            "Mode": {"type": "string"},
                            "Text": {"type": "string"},
                            "Wiki": {"type": "boolean"},
                        },
                    },
                },
            },
        )
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/MarkdownOption"},
                    },
                },
            },
        }
        rename_map = _normalize_operation_body(operation, spec)
        assert rename_map == {
            "context": "Context",
            "mode": "Mode",
            "text": "Text",
            "wiki": "Wiki",
        }
        schema = cast(
            "dict[str, Any]",
            operation["requestBody"]["content"]["application/json"]["schema"],
        )
        assert set(schema["properties"].keys()) == {"context", "mode", "text", "wiki"}
        # The shared component is untouched.
        comp = spec["components"]["schemas"]["MarkdownOption"]
        assert set(comp["properties"].keys()) == {"Context", "Mode", "Text", "Wiki"}


class TestIsBooleanCheckOperation:
    def test_boolean_check_shape(self) -> None:
        """GET + contentless 204 + 404 → boolean check."""
        operation = {
            "responses": {
                "204": {"description": "merged"},
                "404": {"description": "not merged"},
            },
        }
        spec = make_openapi_spec()
        assert _is_boolean_check_operation(operation, spec) is True

    def test_fetch_with_200_content_is_not_boolean_check(self) -> None:
        """A GET that fetches (200 with content) is not a boolean check."""
        operation = {
            "responses": {
                "200": {
                    "description": "Comment",
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                        },
                    },
                },
                "204": {"description": "empty"},
                "404": {"description": "not found"},
            },
        }
        spec = make_openapi_spec()
        assert _is_boolean_check_operation(operation, spec) is False

    def test_fetch_with_200_ref_content_is_not_boolean_check(self) -> None:
        """A 200 that $refs a content-bearing response is not a boolean check."""
        operation = {
            "responses": {
                "200": {"$ref": "#/components/responses/Comment"},
                "204": {"description": "empty"},
                "404": {"description": "not found"},
            },
        }
        spec = make_openapi_spec(
            components={
                "responses": {
                    "Comment": {
                        "description": "Comment",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"},
                            },
                        },
                    },
                },
            },
        )
        assert _is_boolean_check_operation(operation, spec) is False

    def test_missing_404_is_not_boolean_check(self) -> None:
        operation = {
            "responses": {
                "204": {"description": "ok"},
            },
        }
        spec = make_openapi_spec()
        assert _is_boolean_check_operation(operation, spec) is False

    def test_missing_204_is_not_boolean_check(self) -> None:
        operation = {
            "responses": {
                "404": {"description": "not found"},
            },
        }
        spec = make_openapi_spec()
        assert _is_boolean_check_operation(operation, spec) is False

    def test_responses_not_dict_is_not_boolean_check(self) -> None:
        """A non-dict responses object is not a boolean check."""
        operation = {"responses": "not-a-dict"}
        spec = make_openapi_spec()
        assert _is_boolean_check_operation(operation, spec) is False


class TestAnnotateBooleanChecks:
    def test_skips_non_dict_path_items(self) -> None:
        """A non-dict path item is skipped without annotation."""
        spec = make_openapi_spec(paths={"/weird": "not-a-dict"})
        assert _annotate_boolean_checks(spec) == 0


class TestAnnotateWildcardPathParams:
    def test_stamps_wildcard_on_table_paths(self) -> None:
        """Table paths get x-wildcard-path-param on their GET operation."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/contents/{filepath}": {
                    "get": {"operationId": "repoGetContents"},
                },
                "/repos/{owner}/{repo}/branches/{branch}": {
                    "get": {"operationId": "repoGetBranch"},
                },
            },
        )
        annotated = _annotate_wildcard_path_params(spec)
        assert annotated == 2
        contents_op = cast(
            "dict[str, Any]",
            spec["paths"]["/repos/{owner}/{repo}/contents/{filepath}"]["get"],
        )
        assert contents_op["x-wildcard-path-param"] == "filepath"
        branch_op = cast(
            "dict[str, Any]",
            spec["paths"]["/repos/{owner}/{repo}/branches/{branch}"]["get"],
        )
        assert branch_op["x-wildcard-path-param"] == "branch"

    def test_does_not_stamp_non_table_paths(self) -> None:
        """A path with the same param-name shape but not in the table is untouched.

        ``editorconfig/{filepath}`` looks identical to ``contents/{filepath}``
        in the spec — only the router distinguishes them.  The table is the
        source of truth; the shape alone must not trigger.
        """
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/editorconfig/{filepath}": {
                    "get": {"operationId": "repoGetEditorConfig"},
                },
            },
        )
        annotated = _annotate_wildcard_path_params(spec)
        assert annotated == 0
        op = spec["paths"]["/repos/{owner}/{repo}/editorconfig/{filepath}"]["get"]
        assert "x-wildcard-path-param" not in op

    def test_missing_table_path_warns_loudly(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A table entry absent from the fetched spec warns (drift guard)."""
        spec = make_openapi_spec(paths={})
        with caplog.at_level(
            logging.WARNING, logger="gitea_mcp_server.openapi_converter.normalize"
        ):
            annotated = _annotate_wildcard_path_params(spec)
        assert annotated == 0
        assert "not found in fetched spec" in caplog.text

    def test_missing_get_operation_warns_loudly(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A table path without a GET operation warns (drift guard)."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/contents/{filepath}": {
                    "post": {"operationId": "createFile"},
                },
            },
        )
        with caplog.at_level(
            logging.WARNING, logger="gitea_mcp_server.openapi_converter.normalize"
        ):
            annotated = _annotate_wildcard_path_params(spec)
        assert annotated == 0
        assert "has no GET operation" in caplog.text


class TestNormalizeSpec:
    def test_renames_params_and_annotates_boolean_checks(self) -> None:
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/pulls/{index}/merge": {
                    "get": {
                        "operationId": "repoPullRequestIsMerged",
                        "responses": {
                            "204": {"description": "merged"},
                            "404": {"description": "not merged"},
                        },
                    },
                    "post": {
                        "operationId": "repoMergePullRequest",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["Do"],
                                        "properties": {
                                            "Do": {"type": "string"},
                                            "MergeCommitID": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    },
                },
                "/search": {
                    "get": {
                        "operationId": "repoSearch",
                        "parameters": [
                            {"name": "includeDesc", "in": "query", "schema": {"type": "boolean"}},
                        ],
                        "responses": {"200": {"description": "ok"}},
                    },
                },
            },
        )
        normalize_spec(spec)

        merge_op = cast(
            "dict[str, Any]",
            spec["paths"]["/repos/{owner}/{repo}/pulls/{index}/merge"]["post"],
        )
        assert merge_op["x-param-rename"] == {
            "do": "Do",
            "merge_commit_id": "MergeCommitID",
        }
        body_props = merge_op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert set(body_props.keys()) == {"do", "merge_commit_id"}

        is_merged_op = cast(
            "dict[str, Any]",
            spec["paths"]["/repos/{owner}/{repo}/pulls/{index}/merge"]["get"],
        )
        assert is_merged_op["x-response-transform"] == "boolean-check"

        search_op = cast("dict[str, Any]", spec["paths"]["/search"]["get"])
        assert search_op["x-param-rename"] == {"include_desc": "includeDesc"}

    def test_annotates_wildcard_path_params(self) -> None:
        """normalize_spec stamps x-wildcard-path-param on table paths."""
        spec = make_openapi_spec(
            paths={
                "/repos/{owner}/{repo}/contents/{filepath}": {
                    "get": {
                        "operationId": "repoGetContents",
                        "responses": {"200": {"description": "ok"}},
                    },
                },
            },
        )
        normalize_spec(spec)
        op = cast(
            "dict[str, Any]",
            spec["paths"]["/repos/{owner}/{repo}/contents/{filepath}"]["get"],
        )
        assert op["x-wildcard-path-param"] == "filepath"

    def test_merges_with_existing_rename_map(self) -> None:
        """Normalization merges into an existing x-param-rename (collision map)."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner"},
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "Do": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    },
                },
            },
        )
        normalize_spec(spec)
        op = cast("dict[str, Any]", spec["paths"]["/test/{owner}"]["post"])
        assert op["x-param-rename"] == {
            "body_owner": "owner",
            "do": "Do",
        }

    def test_empty_spec_noop(self) -> None:
        spec = make_openapi_spec()
        normalize_spec(spec)
        assert spec["paths"] == {}

    def test_ref_body_without_path_params_normalized(self) -> None:
        """``normalize_spec`` normalizes a ``$ref`` body with no path params.

        Regression for ``POST /markdown``/``POST /markup``: collision
        resolution never visits operations without path parameters, so the
        ``$ref`` body stayed unresolved and Rule A skipped it — the tools
        exposed PascalCase params (``Context``/``Mode``/``Text``/``Wiki``).
        """
        spec = make_openapi_spec(
            paths={
                "/markdown": {
                    "post": {
                        "operationId": "renderMarkdown",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/MarkdownOption",
                                    },
                                },
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    },
                },
            },
            components={
                "schemas": {
                    "MarkdownOption": {
                        "type": "object",
                        "properties": {
                            "Context": {"type": "string"},
                            "Mode": {"type": "string"},
                            "Text": {"type": "string"},
                            "Wiki": {"type": "boolean"},
                        },
                    },
                },
            },
        )
        normalize_spec(spec)
        op = cast("dict[str, Any]", spec["paths"]["/markdown"]["post"])
        assert op["x-param-rename"] == {
            "context": "Context",
            "mode": "Mode",
            "text": "Text",
            "wiki": "Wiki",
        }
        schema = cast(
            "dict[str, Any]",
            op["requestBody"]["content"]["application/json"]["schema"],
        )
        assert set(schema["properties"].keys()) == {"context", "mode", "text", "wiki"}
        # The shared component is untouched.
        comp = spec["components"]["schemas"]["MarkdownOption"]
        assert set(comp["properties"].keys()) == {"Context", "Mode", "Text", "Wiki"}

    def test_skips_non_dict_path_items(self) -> None:
        """A non-dict path item is skipped without error."""
        spec = make_openapi_spec(paths={"/weird": "not-a-dict"})
        normalize_spec(spec)
        assert spec["paths"]["/weird"] == "not-a-dict"

    def test_internal_error_is_logged_not_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An internal failure is logged and swallowed, never propagated."""
        spec = make_openapi_spec(
            paths={"/x": {"get": {"operationId": "x", "responses": {}}}},
        )

        def _boom(_spec: Any) -> int:
            boom_msg = "boom"
            raise RuntimeError(boom_msg)

        monkeypatch.setattr(
            "gitea_mcp_server.openapi_converter.normalize._annotate_boolean_checks",
            _boom,
        )
        with caplog.at_level(logging.ERROR):
            normalize_spec(spec)  # must not raise
        assert "Failed to normalize spec quirks" in caplog.text

    def test_one_bad_operation_does_not_abort_the_pass(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A failure on one operation is logged and skipped; the rest normalize.

        Without the per-operation guard, a single malformed operation would
        abort normalization of the whole spec, leaving every later operation
        unnormalized.
        """
        spec = make_openapi_spec(
            paths={
                "/good": {
                    "post": {
                        "operationId": "good",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"Do": {"type": "string"}},
                                    },
                                },
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    },
                },
                "/bad": {
                    "post": {
                        "operationId": "bad",
                        "responses": {"200": {"description": "ok"}},
                    },
                },
            },
        )

        def _boom(operation: dict[str, Any]) -> dict[str, str]:
            if operation.get("operationId") == "bad":
                boom_msg = "boom"
                raise RuntimeError(boom_msg)
            return {}

        monkeypatch.setattr(
            "gitea_mcp_server.openapi_converter.normalize._normalize_operation_parameters",
            _boom,
        )
        with caplog.at_level(logging.ERROR):
            normalize_spec(spec)  # must not raise

        # The good operation still normalized.
        good_op = cast(
            "dict[str, Any]",
            spec["paths"]["/good"]["post"],
        )
        assert good_op["x-param-rename"] == {"do": "Do"}
        # The bad operation was skipped, not fatal.
        assert "Failed to normalize operation POST /bad" in caplog.text
