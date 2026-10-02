"""Tests for gitea_mcp_server.param_rename — the x-param-rename contract."""

from gitea_mcp_server.param_rename import read_param_rename
from tests.helpers.spec_fixtures import make_openapi_spec


class TestReadParamRename:
    """Tests for reading the ``x-param-rename`` from the spec."""

    def test_reads_rename_map(self) -> None:
        """``x-param-rename`` is read correctly."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner"},
                    },
                },
            },
        )
        assert read_param_rename(spec, "/test/{owner}", "POST") == {"body_owner": "owner"}

    def test_reads_hyphenated_rename_map(self) -> None:
        """A kebab-case path rename is read (issue #783)."""
        spec = make_openapi_spec(
            paths={
                "/activitypub/repository-id/{repository-id}": {
                    "get": {
                        "operationId": "activitypubGetRepository",
                        "x-param-rename": {"repository_id": "repository-id"},
                    },
                },
            },
        )
        assert read_param_rename(spec, "/activitypub/repository-id/{repository-id}", "get") == {
            "repository_id": "repository-id"
        }

    def test_method_is_case_insensitive(self) -> None:
        """The method lookup lowercases its argument."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "get": {"operationId": "test", "x-param-rename": {"a": "b"}},
                },
            },
        )
        assert read_param_rename(spec, "/test/{owner}", "GET") == {"a": "b"}

    def test_no_rename_map(self) -> None:
        """No ``x-param-rename`` returns None."""
        spec = make_openapi_spec(
            paths={"/test/{owner}": {"post": {"operationId": "test"}}},
        )
        assert read_param_rename(spec, "/test/{owner}", "POST") is None

    def test_empty_rename_map_returns_none(self) -> None:
        """An empty ``x-param-rename`` is treated as absent."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {"operationId": "test", "x-param-rename": {}},
                },
            },
        )
        assert read_param_rename(spec, "/test/{owner}", "POST") is None

    def test_wrong_method(self) -> None:
        """Wrong method returns None."""
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {"operationId": "test", "x-param-rename": {"a": "b"}},
                },
            },
        )
        assert read_param_rename(spec, "/test/{owner}", "GET") is None

    def test_nonexistent_path(self) -> None:
        """Nonexistent path returns None."""
        spec = make_openapi_spec()
        assert read_param_rename(spec, "/nonexistent", "POST") is None

    def test_none_spec_returns_none(self) -> None:
        """A ``None`` spec returns None (the factory supports no-spec registration)."""
        assert read_param_rename(None, "/test/{owner}", "POST") is None

    def test_non_dict_path_item_returns_none(self) -> None:
        """A non-dict path item degrades to None without raising."""
        spec = make_openapi_spec(paths={"/weird": "not-a-dict"})
        assert read_param_rename(spec, "/weird", "POST") is None
