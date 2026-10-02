"""Tests for gitea_mcp_server.param_rename — the x-param-rename contract."""

from gitea_mcp_server.openapi_types import OpenAPISpec
from gitea_mcp_server.param_rename import path_param_map, read_param_rename
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


class TestPathParamMap:
    """Tests for path_param_map — the shared wire↔normalized view."""

    def _spec(self) -> OpenAPISpec:
        return make_openapi_spec(
            paths={
                "/activitypub/repository-id/{repository-id}": {
                    "get": {
                        "operationId": "activitypubGetRepository",
                        "x-param-rename": {"repository_id": "repository-id"},
                    },
                },
            },
        )

    def test_both_directions(self) -> None:
        """arg_to_wire and wire_to_arg are inverses for a renamed placeholder."""
        result = path_param_map(
            self._spec(),
            "/activitypub/repository-id/{repository-id}",
            "get",
            ["repository-id"],
        )
        assert result.arg_to_wire == {"repository_id": "repository-id"}
        assert result.wire_to_arg == {"repository-id": "repository_id"}

    def test_identity_for_unchanged_placeholder(self) -> None:
        """A placeholder with no rename gets an identity entry in both directions."""
        result = path_param_map(
            self._spec(),
            "/activitypub/repository-id/{repository-id}",
            "get",
            ["repository-id", "owner"],
        )
        assert result.arg_to_wire["owner"] == "owner"
        assert result.wire_to_arg["owner"] == "owner"

    def test_restricts_to_placeholders(self) -> None:
        """A rename whose wire name is not a placeholder is excluded.

        ``other`` -> ``other-wire`` names a wire value that is not in the
        template, so it must not appear in either direction.
        """
        spec = make_openapi_spec(
            paths={
                "/test/{owner}": {
                    "post": {
                        "operationId": "test",
                        "x-param-rename": {"body_owner": "owner", "other": "other-wire"},
                    },
                },
            },
        )
        result = path_param_map(spec, "/test/{owner}", "POST", ["owner"])
        # ``owner`` is a placeholder; its normalized arg is ``body_owner``.
        assert result.arg_to_wire == {"body_owner": "owner"}
        assert result.wire_to_arg == {"owner": "body_owner"}
        # ``other-wire`` is not a placeholder — excluded.
        assert "other" not in result.arg_to_wire
        assert "other-wire" not in result.wire_to_arg

    def test_none_spec_is_identity(self) -> None:
        """A None spec yields an identity map."""
        result = path_param_map(None, "/test/{owner}", "POST", ["owner", "repo"])
        assert result.arg_to_wire == {"owner": "owner", "repo": "repo"}
        assert result.wire_to_arg == {"owner": "owner", "repo": "repo"}

    def test_no_placeholders_is_empty(self) -> None:
        """No placeholders yields empty maps."""
        result = path_param_map(self._spec(), "/x", "get", [])
        assert result.arg_to_wire == {}
        assert result.wire_to_arg == {}
