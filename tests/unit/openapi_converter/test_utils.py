"""Unit tests for OpenAPI converter - utility functions."""

import pytest

from gitea_mcp_server.openapi_converter import camel_to_snake


class TestCamelToSnake:
    """Tests for the camel_to_snake conversion function."""

    def test_simple_camelcase(self):
        assert camel_to_snake("getAllRepos") == "get_all_repos"

    def test_simple_pascalcase(self):
        assert camel_to_snake("CreateIssue") == "create_issue"

    @pytest.mark.parametrize("input_str,expected", [
        ("issueCreateIssue", "issue_create_issue"),
        ("repoGetBranch", "repo_get_branch"),
    ])
    def test_multiple_camel_phrases(self, input_str, expected):
        assert camel_to_snake(input_str) == expected

    def test_consecutive_uppercase(self):
        assert camel_to_snake("GetURL") == "get_url"
        assert camel_to_snake("listAPIKeys") == "list_api_keys"

    def test_single_word(self):
        assert camel_to_snake("get") == "get"
        assert camel_to_snake("GET") == "get"

    def test_with_numbers(self):
        assert camel_to_snake("getV1") == "get_v1"
        assert (
            camel_to_snake("list2FA") == "list2_fa"
        )  # Digit not separated from following uppercase

    def test_already_snake_case(self):
        assert camel_to_snake("already_snake") == "already_snake"

    def test_edge_cases(self):
        assert camel_to_snake("") == ""
        assert camel_to_snake("A") == "a"
        assert camel_to_snake("AB") == "ab"
