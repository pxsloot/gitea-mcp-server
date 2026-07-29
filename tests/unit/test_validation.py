"""Unit tests for input validation functionality."""

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.validation import (
    FILEPATH_PATTERN,
    OWNER_REPO_PATTERN,
    REF_PATTERN,
    SHA_PATTERN,
    USERNAME_PATTERN,
    _collect_enum_values,
    _find_string_schema,
    _infer_enum_from_description,
    _inject_enum_into_defs,
    _resolve_local_refs,
    _validate_enum_from_schema,
    augment_schema_with_validation,
    validate_filepath,
    validate_labels,
    validate_owner_repo,
    validate_pagination,
    validate_ref,
    validate_sha,
    validate_username,
)


class TestOwnerRepoPattern:
    """Test the OWNER_REPO_PATTERN regex."""

    @pytest.mark.parametrize(
        "value",
        [
            "owner",
            "my-repo",
            "test_123",
            "Org.Name",
            "a",
            "A",
            "0",
            "name.with.dots",
            "name_with_underscores",
            "Name-With-Multiple",
            "x" * 50,
        ],
    )
    def test_valid_patterns(self, value: Any) -> None:
        assert re.fullmatch(OWNER_REPO_PATTERN, value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "-invalid",
            "invalid-",
            "in..valid",
            "in/v?lid",
            "name with spaces",
            "name@at",
            "name!exclaim",
            "name#hash",
            "name$dollar",
            "name%percent",
            "name^caret",
            "name&and",
            "name*star",
            "name+plus",
            "name=equals",
            "name[bracket",
            "name}brace",
            "name\\backslash",
            "name|pipe",
            "name;semicolon",
            "name:colon",
            "name'quote",
            'name"doublequote',
            "name<less",
            "name>greater",
            "name,comma",
            "name?question",
        ],
    )
    def test_invalid_patterns(self, value: Any) -> None:
        assert re.fullmatch(OWNER_REPO_PATTERN, value) is None


class TestFilepathPattern:
    """Test the FILEPATH_PATTERN regex."""

    @pytest.mark.parametrize(
        "value",
        [
            "path/to/file.txt",
            "folder/sub/file.md",
            "README.md",
            "file with spaces.txt",
            "a/b/c/d/e",
            "folder-name/file_name.txt",
            "folder.name/file.ext",
            "relative/path/../file",  # This pattern might allow ".." but we block in validator separately
            "a",
            "a.txt",
            "a/b",
        ],
    )
    def test_valid_patterns(self, value: Any) -> None:
        assert re.fullmatch(FILEPATH_PATTERN, value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "/absolute/path",
            "/etc/passwd",
            "name|with*special",
            "name?with:question colon",
            "name\\with/slash",  # backslash not allowed
            "name;with;semicolon",
        ],
    )
    def test_invalid_patterns(self, value: Any) -> None:
        assert re.fullmatch(FILEPATH_PATTERN, value) is None


class TestRefPattern:
    """Test the REF_PATTERN regex."""

    @pytest.mark.parametrize(
        "value",
        [
            "main",
            "master",
            "feature/branch",
            "v1.0",
            "release-2023",
            "heads/main",
            "tags/v1",
            "fix/issue-123",
            "patch~1",
            "branch^merge",
            "user@method",  # '@' is allowed in ref names (e.g., 'refs/heads/branch')
            "a" * 255,
        ],
    )
    def test_valid_patterns(self, value: Any) -> None:
        assert re.fullmatch(REF_PATTERN, value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "ref with spaces",
            "name?question",
            "name*star",
            "name[bracket",
            "name{brace",
            "name\\backslash",
            "name|pipe",
            "name;semicolon",
        ],
    )
    def test_invalid_patterns(self, value: Any) -> None:
        assert re.fullmatch(REF_PATTERN, value) is None


class TestUsernamePattern:
    """Test the USERNAME_PATTERN regex."""

    @pytest.mark.parametrize(
        "value",
        [
            "user",
            "john_doe",
            "jane-doe",
            "admin.user",
            "AUser123",
            "x" * 50,
        ],
    )
    def test_valid_patterns(self, value: Any) -> None:
        assert re.fullmatch(USERNAME_PATTERN, value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "-user",
            "user-",
            "user..name",
            "user/name",
            "user@domain",
            "user name",
        ],
    )
    def test_invalid_patterns(self, value: Any) -> None:
        assert re.fullmatch(USERNAME_PATTERN, value) is None


class TestSHAPattern:
    """Test the SHA_PATTERN regex."""

    @pytest.mark.parametrize(
        "value",
        [
            "a" * 40,
            "A" * 40,
            "0123456789abcdef0123456789abcdef01234567",
        ],
    )
    def test_valid_shas(self, value: Any) -> None:
        assert re.fullmatch(SHA_PATTERN, value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "a" * 39,
            "a" * 41,
            "g" * 40,  # invalid hex character
            "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",  # not hex
            "0123456789abcdef0123456789abcdef0123456",  # 39 chars
            "0123456789abcdef0123456789abcdef012345678",  # 41 chars
            "12345",  # too short
            "abcd1234",  # too short
        ],
    )
    def test_invalid_shas(self, value: Any) -> None:
        assert re.fullmatch(SHA_PATTERN, value) is None


class TestValidateOwnerRepo:
    """Tests for the validate_owner_repo function."""

    @pytest.mark.parametrize(
        ("value", "field"),
        [
            ("owner", "owner"),
            ("my-repo", "owner"),
            ("test_123", "repo"),
            ("Org.Name", "repo"),
            ("a", "owner"),
            ("name.with.dots", "repo"),
        ],
    )
    def test_valid(self, value: Any, field: str) -> None:
        validate_owner_repo(value, field=field)

    @pytest.mark.parametrize(
        ("value", "field"),
        [
            ("", "owner"),
            (" ", "repo"),
            ("-invalid", "owner"),
            ("invalid-", "repo"),
            ("in..valid", "owner"),
            ("in/v?lid", "repo"),
            ("name with spaces", "owner"),
            ("name@at", "repo"),
            (123, "owner"),
            (None, "repo"),
        ],
    )
    def test_invalid(self, value: Any, field: str) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_owner_repo(value, field=field)
        assert exc.value.field == field
        assert (
            "must be a string" in str(exc.value)
            or "cannot be empty" in str(exc.value)
            or "invalid characters" in str(exc.value)
        )


class TestValidateFilepath:
    """Tests for the validate_filepath function."""

    @pytest.mark.parametrize(
        "value",
        [
            "path/to/file.txt",
            "README.md",
            "folder/sub folder/file.txt",
            "a/b/c",
            "file",
        ],
    )
    def test_valid(self, value: Any) -> None:
        validate_filepath(value, field="filepath")

    def test_rejects_absolute_path(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_filepath("/absolute/path", field="filepath")
        assert exc.value.field == "filepath"
        assert "relative path" in str(exc.value)

    @pytest.mark.parametrize(
        "value",
        [
            "..",
            "../parent",
            "../escape",
            "sub/../../etc",
            "../../parent",
            "path/..",
        ],
    )
    def test_rejects_parent_traversal(self, value: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_filepath(value, field="filepath")
        assert exc.value.field == "filepath"
        assert ".." in str(exc.value)

    @pytest.mark.parametrize("value", ["", " ", 123, None])
    def test_rejects_invalid_type_or_empty(self, value: Any) -> None:
        with pytest.raises(ValidationError):
            validate_filepath(value, field="filepath")


class TestValidateRef:
    """Tests for the validate_ref function."""

    @pytest.mark.parametrize(
        "value",
        [
            "main",
            "master",
            "feature/branch",
            "v1.0",
            "release-2023",
            "heads/main",
            "tags/v1",
            "fix/issue-123",
            "patch~1",
            "branch^merge",
            "user@method",
        ],
    )
    def test_valid(self, value: Any) -> None:
        validate_ref(value, field="ref")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "ref with spaces",
            "name?question",
            "name*star",
            "name[bracket",
            "name{brace",
            "name\\backslash",
            "name|pipe",
            "name;semicolon",
            123,
            None,
        ],
    )
    def test_invalid(self, value: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_ref(value, field="ref")
        assert exc.value.field == "ref"


class TestValidateUsername:
    """Tests for the validate_username function."""

    @pytest.mark.parametrize(
        "value",
        ["user", "john_doe", "jane-doe", "admin.user", "AUser123"],
    )
    def test_valid(self, value: Any) -> None:
        validate_username(value, field="username")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ",
            "-user",
            "user-",
            "user..name",
            "user/name",
            "user@domain",
            "user name",
        ],
    )
    def test_invalid(self, value: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_username(value, field="username")
        assert exc.value.field == "username"


class TestValidateSHA:
    """Tests for the validate_sha function."""

    @pytest.mark.parametrize(
        "value",
        [
            "a" * 40,
            "A" * 40,
            "0123456789abcdef0123456789abcdef01234567",
        ],
    )
    def test_valid(self, value: Any) -> None:
        validate_sha(value, field="sha")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "a" * 39,
            "a" * 41,
            "g" * 40,
            "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            12345,
            None,
        ],
    )
    def test_invalid(self, value: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_sha(value, field="sha")
        assert exc.value.field == "sha"


class TestValidateLabels:
    """Tests for the validate_labels function."""

    def test_valid_list_of_strings(self) -> None:
        validate_labels(["bug", "enhancement"], field="labels")
        validate_labels(["label with spaces"], field="labels")
        validate_labels([123, "bug"], field="labels")
        validate_labels([1, 2, 3], field="labels")
        validate_labels([], field="labels")  # empty list is ok

    @pytest.mark.parametrize(
        "value",
        [
            "not a list",
            123,
            None,
            {"key": "value"},
        ],
    )
    def test_invalid_not_list(self, value: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_labels(value, field="labels")
        assert exc.value.field == "labels"

    def test_invalid_item_type(self) -> None:
        with pytest.raises(ValidationError):
            validate_labels([3.14], field="labels")
        with pytest.raises(ValidationError):
            validate_labels([None], field="labels")
        with pytest.raises(ValidationError):
            validate_labels([True], field="labels")

    @pytest.mark.parametrize(
        "value",
        [
            [""],
            ["   "],
        ],
    )
    def test_empty_or_whitespace_string_not_allowed(self, value: Any) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_labels(value, field="labels")
        assert "whitespace" in str(exc.value) or "Empty" in str(exc.value)

    def test_string_too_long(self) -> None:
        long_label = "a" * 101
        with pytest.raises(ValidationError) as exc:
            validate_labels([long_label], field="labels")
        assert "exceeds maximum length" in str(exc.value)

    def test_negative_int_id(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_labels([-1], field="labels")
        assert "positive" in str(exc.value) or "negative" in str(exc.value)


class TestValidatePagination:
    """Tests for the validate_pagination function."""

    def test_valid_none(self) -> None:
        validate_pagination()  # no error
        validate_pagination(page=None, per_page=None)

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (1, 1),
            (1, 10),
            (5, 100),
            (10, 1),
            (100, 100),
            (None, 50),
            (50, None),
        ],
    )
    def test_valid_combinations(self, page: int, per_page: int) -> None:
        validate_pagination(page=page, per_page=per_page)

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (0, 10),
            (-1, 10),
            (0, None),
        ],
    )
    def test_invalid_page(self, page: int, per_page: int) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page=page, per_page=per_page)
        assert exc.value.field == "page"

    @pytest.mark.parametrize(
        ("page", "per_page"),
        [
            (1, 0),
            (1, -5),
            (1, 101),
            (2, 200),
            (None, 0),
            (None, 101),
        ],
    )
    def test_invalid_per_page(self, page: int, per_page: int) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page=page, per_page=per_page)
        assert exc.value.field == "per_page"

    def test_invalid_page_type(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page="1", per_page=10)
        assert "must be an integer" in str(exc.value)

    def test_invalid_per_page_type(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page=1, per_page="10")
        assert "must be an integer" in str(exc.value)


class TestCollectEnumValues:
    """Tests for _collect_enum_values."""

    def test_top_level_enum(self) -> None:
        schema = {"type": "string", "enum": ["a", "b"]}
        assert _collect_enum_values(schema) == ["a", "b"]

    def test_enum_in_anyof(self) -> None:
        schema = {
            "anyOf": [
                {"type": "string", "enum": ["pending", "success"]},
                {"type": "null"},
            ]
        }
        assert _collect_enum_values(schema) == ["pending", "success"]

    def test_enum_in_oneof(self) -> None:
        schema = {
            "oneOf": [
                {"type": "string", "enum": ["x", "y"]},
                {"type": "integer"},
            ]
        }
        assert _collect_enum_values(schema) == ["x", "y"]

    def test_no_enum(self) -> None:
        schema = {"type": "string"}
        assert _collect_enum_values(schema) is None

    def test_empty_schema(self) -> None:
        assert _collect_enum_values({}) is None

    def test_top_level_enum_non_list_returns_none(self) -> None:
        """Defensive: if enum exists but is not a list, return None."""
        assert _collect_enum_values({"enum": "not_a_list"}) is None

    def test_anyof_enum_non_list_returns_none(self) -> None:
        """Defensive: if anyOf branch has enum but it's not a list, return None."""
        schema = {
            "anyOf": [
                {"type": "string", "enum": "bad_value"},
                {"type": "null"},
            ]
        }
        assert _collect_enum_values(schema) is None

    def test_enum_through_unresolved_ref_returns_none(self) -> None:
        """REGRESSION: _collect_enum_values can't see through unresolved $ref.

        When a param schema uses ``anyOf`` with ``$ref`` branches (e.g.
        ``gitea_repo_create_status``'s ``state`` param has ``anyOf`` with
        ``{$ref: CommitStatusState}``), the $ref is unresolved at schema-
        augmentation time.  _collect_enum_values cannot follow $ref to
        find the enum on the referenced type.

        This regression means the description-to-enum inference in
        ``augment_schema_with_validation`` skips these params because
        ``_collect_enum_values`` returns ``None`` (no enum found), but
        ``_infer_enum_from_description`` also fails because
        ``_find_string_schema`` can't follow $ref either.

        See:
            https://git.home.lan/mcp-server/gitea-mcp-server/issues/596
            (state validation fix follow-up: $ref resolution before inference)
        """
        schema = {
            "anyOf": [
                {"$ref": "#/$defs/CommitStatusState"},
                {"type": "null"},
            ]
        }
        assert _collect_enum_values(schema) is None


class TestValidateEnumFromSchema:
    """Tests for _validate_enum_from_schema."""

    def test_valid_value(self) -> None:
        _validate_enum_from_schema("a", field="test", enum_values=["a", "b", "c"])

    def test_invalid_value(self) -> None:
        with pytest.raises(ValidationError, match="must be one of"):
            _validate_enum_from_schema("z", field="test", enum_values=["a", "b", "c"])

    def test_case_sensitive(self) -> None:
        with pytest.raises(ValidationError):
            _validate_enum_from_schema("A", field="test", enum_values=["a", "b"])


class TestFindStringSchema:
    """Tests for _find_string_schema."""

    def test_flat_string(self) -> None:
        assert _find_string_schema({"type": "string"}) == {"type": "string"}

    def test_non_string_type(self) -> None:
        assert _find_string_schema({"type": "integer"}) is None

    def test_through_anyof(self) -> None:
        schema = {"anyOf": [{"type": "integer"}, {"type": "string", "description": "found"}]}
        result = _find_string_schema(schema)
        assert result is not None
        assert result["description"] == "found"

    def test_no_string_type(self) -> None:
        schema = {"anyOf": [{"type": "integer"}, {"type": "boolean"}]}
        assert _find_string_schema(schema) is None

    def test_empty_schema(self) -> None:
        assert _find_string_schema({}) is None

    def test_through_anyof_with_unresolved_ref_returns_none(self) -> None:
        """REGRESSION: _find_string_schema can't follow $ref branches.

        When an ``anyOf`` branch contains ``$ref`` instead of a resolved
        type (e.g. ``{"$ref": "#/$defs/CommitStatusState"}``), the
        function skips it because it has no ``type`` key.  This prevents
        description-to-enum inference from working on unresolved schemas.
        """
        schema = {
            "anyOf": [
                {"$ref": "#/$defs/CommitStatusState"},
                {"type": "null"},
            ]
        }
        assert _find_string_schema(schema) is None

    def test_through_type_as_list(self) -> None:
        """String detection works when type is a list (e.g. ["string", "null"]).

        FastMCP inlines nullable body-schema fields as ``{"type": ["string",
        "null"]}``.  ``_find_string_schema`` must handle this via
        ``schema_type_matches``.
        """
        schema = {"type": ["string", "null"]}
        result = _find_string_schema(schema)
        assert result is not None
        assert "string" in result["type"]

    def test_through_anyof_with_type_as_list(self) -> None:
        """String detection inside anyOf when branch uses type-as-list."""
        schema = {
            "anyOf": [
                {"type": ["string", "null"], "description": "found"},
                {"type": "null"},
            ]
        }
        result = _find_string_schema(schema)
        assert result is not None
        assert result["description"] == "found"


class TestResolveLocalRefs:
    """Tests for _resolve_local_refs."""

    def test_resolves_ref_in_anyof_branch(self) -> None:
        """$ref in anyOf branch is replaced with the defs definition."""
        schema = {
            "anyOf": [
                {"$ref": "#/$defs/CommitStatusState"},
                {"type": "null"},
            ]
        }
        defs = {
            "CommitStatusState": {
                "type": "string",
                "description": "pending, success, error",
            },
        }
        resolved = _resolve_local_refs(schema, defs)
        branch = resolved["anyOf"][0]
        assert branch["type"] == "string"
        assert "pending" in branch["description"]

    def test_skips_non_dict_branch(self) -> None:
        """Non-dict branches in anyOf are passed through unchanged."""
        schema = {
            "anyOf": [
                "not_a_dict",
                {"type": "string"},
            ]
        }
        # Use non-empty defs so _resolve_local_refs doesn't short-circuit
        # at the ``if not defs: return schema`` guard.
        defs = {"Dummy": {"type": "string"}}
        resolved = _resolve_local_refs(schema, defs)
        assert resolved["anyOf"][0] == "not_a_dict"
        assert resolved["anyOf"][1] == {"type": "string"}

    def test_returns_unchanged_when_defs_is_none(self) -> None:
        """Schema is returned as-is when no $defs are available."""
        schema = {
            "anyOf": [
                {"$ref": "#/$defs/CommitStatusState"},
                {"type": "null"},
            ]
        }
        resolved = _resolve_local_refs(schema, None)
        assert resolved is schema  # Same object — no copy needed

    def test_non_list_anyof_branch_is_skipped(self) -> None:
        """anyOf that is not a list is left untouched."""
        schema = {"anyOf": "not_a_list"}
        resolved = _resolve_local_refs(schema, {"Type": {"type": "string"}})
        assert resolved["anyOf"] == "not_a_list"

    def test_resolves_top_level_ref(self) -> None:
        """Top-level ``$ref`` (no anyOf/oneOf wrapper) is resolved directly."""
        schema = {"$ref": "#/$defs/CommitStatusState"}
        defs = {
            "CommitStatusState": {
                "type": "string",
                "description": "pending, success, error",
            },
        }
        resolved = _resolve_local_refs(schema, defs)
        assert resolved["type"] == "string"
        assert "pending" in resolved["description"]

    def test_unresolvable_top_level_ref_returns_schema(self) -> None:
        """Top-level ``$ref`` to unknown type returns schema unchanged."""
        schema = {"$ref": "#/$defs/UnknownType"}
        defs = {"CommitStatusState": {"type": "string"}}
        resolved = _resolve_local_refs(schema, defs)
        assert resolved is schema  # unchanged, not copied


class TestInjectEnumIntoDefs:
    """Tests for _inject_enum_into_defs."""

    def test_injects_into_defs_and_branch(self) -> None:
        """Enum from resolved schema is injected into $defs and $ref branch."""
        existing_schema = {
            "anyOf": [
                {"$ref": "#/$defs/CommitStatusState"},
                {"type": "null"},
            ]
        }
        resolved = {
            "anyOf": [
                {
                    "type": "string",
                    "description": 'states: "pending", "success"',
                    "enum": ["pending", "success"],
                },
                {"type": "null"},
            ]
        }
        defs: dict[str, Any] = {
            "CommitStatusState": {
                "type": "string",
                "description": 'states: "pending", "success"',
            },
        }

        _inject_enum_into_defs(existing_schema, resolved, defs)

        # Injected into $defs definition
        assert defs["CommitStatusState"]["enum"] == ["pending", "success"]

        # Injected into the $ref branch
        branch = existing_schema["anyOf"][0]
        assert branch["enum"] == ["pending", "success"]

    def test_skips_when_resolved_has_no_enum(self) -> None:
        """No-op when the resolved schema lacks an enum."""
        existing_schema: dict[str, Any] = {"type": "string"}
        resolved: dict[str, Any] = {"type": "string"}
        defs: dict[str, Any] = {}
        # Must not raise
        _inject_enum_into_defs(existing_schema, resolved, defs)

    def test_skips_when_existing_already_has_enum(self) -> None:
        """Existing enum on a non-$ref branch is not overwritten.

        The injection passes are idempotent — both check ``"enum" not in
        target`` before writing.  An existing_schema branch that already
        declares an enum is left untouched.
        """
        existing_schema: dict[str, Any] = {
            "anyOf": [
                {"type": "string", "enum": ["open", "closed"]},
                {"type": "null"},
            ]
        }
        resolved: dict[str, Any] = {
            "anyOf": [
                {
                    "type": "string",
                    "description": 'states: "open", "closed"',
                    "enum": ["open", "closed"],
                },
                {"type": "null"},
            ]
        }
        defs: dict[str, Any] = {}
        # Must not raise — injection passes skip the already-enumed branch
        _inject_enum_into_defs(existing_schema, resolved, defs)
        # Enum in existing_schema is unchanged
        assert existing_schema["anyOf"][0]["enum"] == ["open", "closed"]

    def test_non_dict_branch_in_existing_skipped(self) -> None:
        """Non-dict branches in the $ref injection pass are skipped."""
        existing_schema = {
            "anyOf": [
                {"$ref": "#/$defs/Something"},
                "not_a_dict",
            ]
        }
        resolved = {
            "anyOf": [
                {
                    "type": "string",
                    "enum": ["a", "b"],
                },
                {"type": "null"},
            ]
        }
        defs: dict[str, Any] = {
            "Something": {"type": "string"},
        }
        # Must not raise on non-dict branch
        _inject_enum_into_defs(existing_schema, resolved, defs)
        assert defs["Something"]["enum"] == ["a", "b"]


class TestInferEnumFromDescription:
    """Tests for _infer_enum_from_description."""

    def test_commit_status_state_pattern(self) -> None:
        schema = {
            "anyOf": [
                {
                    "type": "string",
                    "description": (
                        "CommitStatusState holds the state of a CommitStatus\n"
                        'It can be "pending", "success", "error", "failure" and "warning"'
                    ),
                },
                {"type": "null"},
            ]
        }
        assert _infer_enum_from_description(schema) is True
        string_branch = schema["anyOf"][0]
        assert string_branch["enum"] == ["pending", "success", "error", "failure", "warning"]

    def test_already_has_enum(self) -> None:
        schema = {"type": "string", "enum": ["open", "closed"], "description": "something"}
        assert _infer_enum_from_description(schema) is False
        assert schema["enum"] == ["open", "closed"]  # unchanged

    def test_no_quoted_values(self) -> None:
        schema = {"type": "string", "description": "A simple name"}
        assert _infer_enum_from_description(schema) is False
        assert "enum" not in schema

    def test_single_quoted_value(self) -> None:
        schema = {"type": "string", "description": 'Defaults to "auto"'}
        assert _infer_enum_from_description(schema) is False
        assert "enum" not in schema

    def test_empty_description(self) -> None:
        schema = {"type": "string", "description": ""}
        assert _infer_enum_from_description(schema) is False

    def test_no_description(self) -> None:
        schema = {"type": "string"}
        assert _infer_enum_from_description(schema) is False

    def test_non_string_schema(self) -> None:
        schema = {"type": "integer", "description": '"a", "b" and "c"'}
        assert _infer_enum_from_description(schema) is False
        assert "enum" not in schema

    def test_deduplicates_duplicate_values(self) -> None:
        """Duplicate quoted values should be deduplicated in the output enum."""
        schema = {
            "type": "string",
            "description": 'Values: "pending", "success", "pending", "error"',
        }
        assert _infer_enum_from_description(schema) is True
        assert schema["enum"] == ["pending", "success", "error"]

    def test_through_anyof_with_unresolved_ref_returns_false(self) -> None:
        """REGRESSION: inference fails when anyOf contains $ref instead of resolved type.

        The actual ``gitea_repo_create_status`` param schema has::

            {"anyOf": [{"$ref": "#/$defs/CommitStatusState"}, {"type": "null"}]}

        Because ``_find_string_schema`` can't follow ``$ref``, the inference
        silently returns ``False`` and no enum values are extracted from the
        ``CommitStatusState`` description.  This means agents see no valid
        state values for the commit status tool.
        """
        schema = {
            "anyOf": [
                {"$ref": "#/$defs/CommitStatusState"},
                {"type": "null"},
            ]
        }
        # Fails: _find_string_schema returns None, inference returns False
        assert _infer_enum_from_description(schema) is False
        # No enum injected anywhere in the schema
        assert _collect_enum_values(schema) is None


class TestAugmentSchemaWithValidation:
    """Tests for the augment_schema_with_validation function."""

    def test_no_parameters_returns_early(self) -> None:
        """Component with no ``parameters`` attribute returns without error."""
        component = MagicMock()
        del component.parameters  # Simulate missing parameters
        # Must not raise
        augment_schema_with_validation(component)

    def test_empty_properties_returns_early(self) -> None:
        """Component with empty ``properties`` returns without error."""
        component = MagicMock()
        component.parameters = {"properties": {}}
        # Must not raise
        augment_schema_with_validation(component)

    def test_adds_constraints_for_owner(self) -> None:
        component = MagicMock()
        component.parameters = {"properties": {"owner": {"type": "string"}}}
        augment_schema_with_validation(component)
        owner_schema = component.parameters["properties"]["owner"]
        assert "minLength" in owner_schema
        assert owner_schema["minLength"] == 1
        assert "maxLength" in owner_schema
        assert owner_schema["maxLength"] == 50
        assert "pattern" in owner_schema
        assert owner_schema["pattern"] == OWNER_REPO_PATTERN

    def test_adds_constraints_for_multiple_params(self) -> None:
        component = MagicMock()
        component.parameters = {
            "properties": {
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "filepath": {"type": "string"},
                "ref": {"type": "string"},
                "sha": {"type": "string"},
                "username": {"type": "string"},
                "state": {"type": "string"},
                "page": {"type": "number"},
                "per_page": {"type": "number"},
            }
        }
        augment_schema_with_validation(component)
        props = component.parameters["properties"]
        # Owner
        assert props["owner"]["minLength"] == 1
        assert props["owner"]["maxLength"] == 50
        assert props["owner"]["pattern"] == OWNER_REPO_PATTERN
        # Repo
        assert props["repo"]["minLength"] == 1
        assert props["repo"]["maxLength"] == 100
        assert props["repo"]["pattern"] == OWNER_REPO_PATTERN
        # Filepath
        assert props["filepath"]["minLength"] == 1
        assert props["filepath"]["maxLength"] == 500
        assert props["filepath"]["pattern"] == FILEPATH_PATTERN
        # Ref
        assert props["ref"]["minLength"] == 1
        assert props["ref"]["maxLength"] == 255
        assert props["ref"]["pattern"] == REF_PATTERN
        # SHA
        assert props["sha"]["minLength"] == 40
        assert props["sha"]["maxLength"] == 40
        assert props["sha"]["pattern"] == SHA_PATTERN
        # Username
        assert props["username"]["minLength"] == 1
        assert props["username"]["maxLength"] == 50
        assert props["username"]["pattern"] == USERNAME_PATTERN
        # State enum comes from the spec or description inference, neither
        # of which applies to this bare ``{"type": "string"}`` fixture.
        assert "enum" not in props["state"]
        # Page
        assert props["page"]["minimum"] == 1
        # Per page
        assert props["per_page"]["minimum"] == 1
        assert props["per_page"]["maximum"] == 100

    def test_preserves_existing_constraints(self) -> None:
        component = MagicMock()
        component.parameters = {
            "properties": {
                "owner": {"minLength": 2, "description": "Owner name"},
                "page": {"minimum": 0, "type": "integer"},
            }
        }
        augment_schema_with_validation(component)
        owner_schema = component.parameters["properties"]["owner"]
        # Should keep existing minLength=2, not override with 1
        assert owner_schema["minLength"] == 2
        # Should still add maxLength and pattern if missing
        assert owner_schema["maxLength"] == 50
        assert "pattern" in owner_schema
        # Page: existing minimum 0 should be preserved, plus our type already present
        page_schema = component.parameters["properties"]["page"]
        assert page_schema["minimum"] == 0  # not overridden

    def test_skips_if_no_parameters(self) -> None:
        component = MagicMock()
        component.parameters = None
        # Should not raise
        augment_schema_with_validation(component)

    def test_skips_if_empty_parameters(self) -> None:
        component = MagicMock()
        component.parameters = {}
        # Should not raise, just return
        augment_schema_with_validation(component)

    def test_skips_if_no_properties(self) -> None:
        """augment_schema_with_validation returns early when params has no properties."""
        component = MagicMock()
        component.parameters = {"$defs": {"X": {"type": "string"}}}
        # No 'properties' key — should not raise
        augment_schema_with_validation(component)

    def test_skips_unknown_properties(self) -> None:
        component = MagicMock()
        component.parameters = {"properties": {"some_other_param": {"type": "string"}}}
        # Should not add any constraints to unknown param
        augment_schema_with_validation(component)
        assert "some_other_param" in component.parameters["properties"]
        assert component.parameters["properties"]["some_other_param"] == {"type": "string"}

    def test_skips_non_dict_existing_schema(self) -> None:
        """augment_schema_with_validation skips when existing_schema is not a dict (line 286)."""
        component = MagicMock()
        component.parameters = {
            "properties": {
                "owner": "not_a_dict",  # schema value is a string, not dict
            }
        }
        # Should not raise TypeError; the non-dict value is skipped
        augment_schema_with_validation(component)
        # The value should remain unchanged
        assert component.parameters["properties"]["owner"] == "not_a_dict"

    def test_infers_enum_through_unresolved_ref_in_state_param(self) -> None:
        """REGRESSION: state param with $ref must get enum from description inference.

        ``gitea_repo_create_status`` has a ``state`` param whose schema
        contains an ``anyOf`` with an unresolved ``$ref`` branch:

            {"anyOf": [{"$ref": "#/$defs/CommitStatusState"}, {"type": "null"}]}

        The ``$defs.CommitStatusState`` definition has a description with
        quoted values (``"pending"``, ``"success"``, etc.) but no
        machine-readable ``enum``.  ``augment_schema_with_validation`` must
        resolve the ``$ref`` before running ``_infer_enum_from_description``
        so agents see the valid commit status states.

        See:
            https://git.home.lan/mcp-server/gitea-mcp-server/issues/596
        """
        component = MagicMock()
        component.parameters = {
            "properties": {
                "state": {
                    "anyOf": [
                        {"$ref": "#/$defs/CommitStatusState"},
                        {"type": "null"},
                    ],
                },
            },
            "$defs": {
                "CommitStatusState": {
                    "type": "string",
                    "description": (
                        "CommitStatusState holds the state of a CommitStatus\n"
                        'It can be "pending", "success", "error", "failure" and "warning"'
                    ),
                },
            },
        }
        augment_schema_with_validation(component)
        state_schema = component.parameters["properties"]["state"]
        enum_vals = _collect_enum_values(state_schema)
        assert enum_vals == [
            "pending", "success", "error", "failure", "warning"
        ], f"Expected commit status states, got {enum_vals}"

    def test_preserves_existing_enum_on_issue_state_param(self) -> None:
        """Issue tools' state param must keep its spec-defined enum.

        ``gitea_issue_list_issues`` has a ``state`` query param with
        ``enum: ["closed", "open", "all"]`` from the spec.  This must
        not be overwritten or lost.
        """
        component = MagicMock()
        component.parameters = {
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["closed", "open", "all"],
                },
            },
        }
        augment_schema_with_validation(component)
        state_schema = component.parameters["properties"]["state"]
        enum_vals = _collect_enum_values(state_schema)
        assert enum_vals == ["closed", "open", "all"], (
            f"Issue state enum overwritten! Got {enum_vals}"
        )


class TestRunValidation:
    """Tests for _run_validation function."""

    def test_missing_required_raises_validation_error(self) -> None:
        """Missing required params should raise a clear validation error."""
        from gitea_mcp_server.tools.errors import (
            _run_validation,
        )

        with pytest.raises(ValidationError) as exc:
            _run_validation({"page": 1}, required_params=["owner", "repo"])
        assert "owner" in str(exc.value)
        assert "repo" in str(exc.value)

    def test_all_required_params_present_passes(self) -> None:
        """No error when all required params are provided."""
        from gitea_mcp_server.tools.errors import _run_validation

        _run_validation(
            {"owner": "test", "repo": "test", "page": 1},
            required_params=["owner", "repo"],
        )

    def test_no_required_params_list_passes(self) -> None:
        """No error when required_params is None."""
        from gitea_mcp_server.tools.errors import _run_validation

        _run_validation({"owner": "test"})

    def test_empty_required_params_list_passes(self) -> None:
        """No error when required_params is empty."""
        from gitea_mcp_server.tools.errors import _run_validation

        _run_validation({"owner": "test"}, required_params=[])

    def test_single_missing_required_param(self) -> None:
        """A single missing required param should name it."""
        from gitea_mcp_server.tools.errors import (
            _run_validation,
        )

        with pytest.raises(ValidationError) as exc:
            _run_validation({"repo": "test"}, required_params=["owner"])
        assert "owner" in str(exc.value)
        assert "Missing required parameter(s): owner" in str(exc.value)

    def test_missing_required_enum_param_includes_enum_values(self) -> None:
        """Missing required enum param should include valid values in the error."""
        from gitea_mcp_server.tools.errors import (
            _run_validation,
        )

        with pytest.raises(ValidationError) as exc:
            _run_validation(
                {"owner": "test", "repo": "test", "index": 1},
                required_params=["diffType"],
                param_properties={
                    "diffType": {
                        "type": "string",
                        "enum": ["diff", "patch"],
                    },
                },
            )
        msg = str(exc.value)
        assert "diffType" in msg
        assert "expected one of:" in msg
        assert "diff" in msg
        assert "patch" in msg

    def test_missing_required_param_without_enum_unchanged(self) -> None:
        """Missing required param without enum should not add enum hint."""
        from gitea_mcp_server.tools.errors import (
            _run_validation,
        )

        with pytest.raises(ValidationError) as exc:
            _run_validation(
                {"repo": "test"},
                required_params=["owner"],
                param_properties={
                    "owner": {"type": "string"},
                },
            )
        msg = str(exc.value)
        assert "owner" in msg
        assert "expected one of:" not in msg

    def test_validation_still_runs_on_present_params(self) -> None:
        """Existing validation for present params should still run alongside missing check."""
        from gitea_mcp_server.tools.errors import (
            _run_validation,
        )

        with pytest.raises(ValidationError) as exc:
            _run_validation(
                {"owner": "!!invalid!!", "page": 1},
                required_params=["owner"],
            )
        assert "contains invalid characters" in str(exc.value)
