"""Unit tests for spec normalization (openapi_converter/normalize.py).

Tests the shape-driven normalization rules:
- Rule A: snake_case parameter/body-property renames (query/header/cookie/body).
- Rule B: boolean-check response annotation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

from gitea_mcp_server.openapi_converter.normalize import (
    _is_boolean_check_operation,
    _is_snake_case,
    _normalize_operation_body,
    _normalize_operation_parameters,
    normalize_spec,
)
from tests.helpers.spec_fixtures import make_openapi_spec


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
        rename_map = _normalize_operation_body(operation)
        assert rename_map == {
            "do": "Do",
            "merge_commit_id": "MergeCommitID",
        }
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
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
        assert _normalize_operation_body(operation) == {}

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
        assert _normalize_operation_body(operation) == {}


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

        merge_op = spec["paths"]["/repos/{owner}/{repo}/pulls/{index}/merge"]["post"]
        assert merge_op["x-param-rename"] == {
            "do": "Do",
            "merge_commit_id": "MergeCommitID",
        }
        body_props = merge_op["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert set(body_props.keys()) == {"do", "merge_commit_id"}

        is_merged_op = spec["paths"]["/repos/{owner}/{repo}/pulls/{index}/merge"]["get"]
        assert is_merged_op["x-response-transform"] == "boolean-check"

        search_op = spec["paths"]["/search"]["get"]
        assert search_op["x-param-rename"] == {"include_desc": "includeDesc"}

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
        op = spec["paths"]["/test/{owner}"]["post"]
        assert op["x-param-rename"] == {
            "body_owner": "owner",
            "do": "Do",
        }

    def test_empty_spec_noop(self) -> None:
        spec = make_openapi_spec()
        normalize_spec(spec)
        assert spec["paths"] == {}
