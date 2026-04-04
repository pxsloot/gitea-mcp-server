"""Unit tests for input validation functionality."""

import asyncio
import re

import pytest
from unittest.mock import AsyncMock, MagicMock

from gitea_mcp_server.exceptions import ValidationError
from gitea_mcp_server.validation import (
    OWNER_REPO_PATTERN,
    FILEPATH_PATTERN,
    REF_PATTERN,
    USERNAME_PATTERN,
    SHA_PATTERN,
    validate_owner_repo,
    validate_filepath,
    validate_ref,
    validate_username,
    validate_sha,
    validate_labels,
    validate_pagination,
    validate_state,
    augment_schema_with_validation,
    inject_validation_wrapper,
    SINGLE_VALIDATORS,
    SCHEMA_CONSTRAINTS,
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
    def test_valid_patterns(self, value):
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
    def test_invalid_patterns(self, value):
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
    def test_valid_patterns(self, value):
        assert re.fullmatch(FILEPATH_PATTERN, value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "/absolute/path",
            "/etc/passwd",
            "..",
            "../parent",
            "name|with*special",
            "name?with:question colon",
            "name\\with/slash",  # backslash not allowed
            "name;with;semicolon",
        ],
    )
    def test_invalid_patterns(self, value):
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
    def test_valid_patterns(self, value):
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
    def test_invalid_patterns(self, value):
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
    def test_valid_patterns(self, value):
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
    def test_invalid_patterns(self, value):
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
    def test_valid_shas(self, value):
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
    def test_invalid_shas(self, value):
        assert re.fullmatch(SHA_PATTERN, value) is None


class TestValidateOwnerRepo:
    """Tests for the validate_owner_repo function."""

    @pytest.mark.parametrize(
        "value",
        ["owner", "my-repo", "test_123", "Org.Name", "a", "name.with.dots"],
    )
    def test_valid(self, value):
        validate_owner_repo(value, field="owner")  # should not raise
        validate_owner_repo(value, field="repo")

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
            123,
            None,
        ],
    )
    def test_invalid(self, value):
        with pytest.raises(ValidationError) as exc:
            validate_owner_repo(value, field="owner")
        assert exc.value.field == "owner"
        assert (
            "must be a string" in str(exc.value)
            or "cannot be empty" in str(exc.value)
            or "invalid characters" in str(exc.value)
        )

        with pytest.raises(ValidationError) as exc:
            validate_owner_repo(value, field="repo")
        assert exc.value.field == "repo"


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
    def test_valid(self, value):
        validate_filepath(value, field="filepath")

    def test_rejects_absolute_path(self):
        with pytest.raises(ValidationError) as exc:
            validate_filepath("/absolute/path", field="filepath")
        assert exc.value.field == "filepath"
        assert "relative path" in str(exc.value)

    @pytest.mark.parametrize(
        "value",
        [
            "../escape",
            "sub/../../etc",
            "../../parent",
            "path/..",
        ],
    )
    def test_rejects_parent_traversal(self, value):
        with pytest.raises(ValidationError) as exc:
            validate_filepath(value, field="filepath")
        assert exc.value.field == "filepath"
        assert ".." in str(exc.value)

    @pytest.mark.parametrize("value", ["", " ", 123, None])
    def test_rejects_invalid_type_or_empty(self, value):
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
    def test_valid(self, value):
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
    def test_invalid(self, value):
        with pytest.raises(ValidationError) as exc:
            validate_ref(value, field="ref")
        assert exc.value.field == "ref"


class TestValidateUsername:
    """Tests for the validate_username function."""

    @pytest.mark.parametrize(
        "value",
        ["user", "john_doe", "jane-doe", "admin.user", "AUser123"],
    )
    def test_valid(self, value):
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
    def test_invalid(self, value):
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
    def test_valid(self, value):
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
    def test_invalid(self, value):
        with pytest.raises(ValidationError) as exc:
            validate_sha(value, field="sha")
        assert exc.value.field == "sha"


class TestValidateLabels:
    """Tests for the validate_labels function."""

    def test_valid_list_of_strings(self):
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
    def test_invalid_not_list(self, value):
        with pytest.raises(ValidationError) as exc:
            validate_labels(value, field="labels")
        assert exc.value.field == "labels"

    def test_invalid_item_type(self):
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
    def test_empty_or_whitespace_string_not_allowed(self, value):
        with pytest.raises(ValidationError) as exc:
            validate_labels(value, field="labels")
        assert "whitespace" in str(exc.value) or "Empty" in str(exc.value)

    def test_string_too_long(self):
        long_label = "a" * 101
        with pytest.raises(ValidationError) as exc:
            validate_labels([long_label], field="labels")
        assert "exceeds maximum length" in str(exc.value)

    def test_negative_int_id(self):
        with pytest.raises(ValidationError) as exc:
            validate_labels([-1], field="labels")
        assert "positive" in str(exc.value) or "negative" in str(exc.value)


class TestValidatePagination:
    """Tests for the validate_pagination function."""

    def test_valid_none(self):
        validate_pagination()  # no error
        validate_pagination(page=None, per_page=None)

    @pytest.mark.parametrize(
        "page, per_page",
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
    def test_valid_combinations(self, page, per_page):
        validate_pagination(page=page, per_page=per_page)

    @pytest.mark.parametrize(
        "page, per_page",
        [
            (0, 10),
            (-1, 10),
            (0, None),
        ],
    )
    def test_invalid_page(self, page, per_page):
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page=page, per_page=per_page)
        assert exc.value.field == "page"

    @pytest.mark.parametrize(
        "page, per_page",
        [
            (1, 0),
            (1, -5),
            (1, 101),
            (2, 200),
            (None, 0),
            (None, 101),
        ],
    )
    def test_invalid_per_page(self, page, per_page):
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page=page, per_page=per_page)
        assert exc.value.field == "per_page"

    def test_invalid_page_type(self):
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page="1", per_page=10)
        assert "must be an integer" in str(exc.value)

    def test_invalid_per_page_type(self):
        with pytest.raises(ValidationError) as exc:
            validate_pagination(page=1, per_page="10")
        assert "must be an integer" in str(exc.value)


class TestValidateState:
    """Tests for the validate_state function."""

    @pytest.mark.parametrize("value", ["open", "closed", "all"])
    def test_valid_states(self, value):
        validate_state(value, field="state")

    @pytest.mark.parametrize(
        "value,expected_msg",
        [
            ("pending", "must be one of"),
            ("merged", "must be one of"),
            ("openclosed", "must be one of"),
            ("", "must be one of"),
            (" ", "must be one of"),
            (123, "must be a string"),
            (None, "must be a string"),
        ],
    )
    def test_invalid_states(self, value, expected_msg):
        with pytest.raises(ValidationError) as exc:
            validate_state(value, field="state")
        assert exc.value.field == "state"
        assert expected_msg in str(exc.value)


class TestAugmentSchemaWithValidation:
    """Tests for the augment_schema_with_validation function."""

    def test_adds_constraints_for_owner(self):
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

    def test_adds_constraints_for_multiple_params(self):
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
        # State
        assert props["state"]["enum"] == ["open", "closed", "all"]
        # Page
        assert props["page"]["minimum"] == 1
        # Per page
        assert props["per_page"]["minimum"] == 1
        assert props["per_page"]["maximum"] == 100

    def test_preserves_existing_constraints(self):
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

    def test_skips_if_no_parameters(self):
        component = MagicMock()
        component.parameters = None
        # Should not raise
        augment_schema_with_validation(component)

    def test_skips_if_empty_parameters(self):
        component = MagicMock()
        component.parameters = {}
        # Should not raise, just return
        augment_schema_with_validation(component)

    def test_skips_unknown_properties(self):
        component = MagicMock()
        component.parameters = {"properties": {"some_other_param": {"type": "string"}}}
        # Should not add any constraints to unknown param
        augment_schema_with_validation(component)
        assert "some_other_param" in component.parameters["properties"]
        assert component.parameters["properties"]["some_other_param"] == {"type": "string"}


class TestInjectValidationWrapper:
    """Tests for the inject_validation_wrapper function."""

    @pytest.fixture
    def mock_tool(self):
        """Create a mock OpenAPITool with an async run method."""
        tool = MagicMock()
        tool.parameters = {"properties": {}}  # not used by wrapper directly
        tool.run = AsyncMock(return_value={"result": "ok"})
        return tool

    def test_wrapper_calls_validators_for_recognized_params(self, mock_tool):
        # Capture the original run method before injection
        original_run = mock_tool.run
        inject_validation_wrapper(mock_tool)
        args = {
            "owner": "valid-owner",
            "repo": "valid-repo",
            "filepath": "path/file.txt",
            "ref": "main",
            "username": "user",
            "sha": "a" * 40,
            "state": "open",
            "page": 2,
            "per_page": 50,
            "labels": ["bug"],
        }
        # Should not raise
        result = asyncio.run(mock_tool.run(args))
        assert result == {"result": "ok"}
        original_run.assert_called_once_with(args)

    def test_wrapper_raises_for_invalid_owner(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"owner": "-invalid"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "owner"

    def test_wrapper_raises_for_invalid_repo(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"repo": "invalid-"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "repo"

    def test_wrapper_raises_for_invalid_filepath(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"filepath": "/absolute/path"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "filepath"

    def test_wrapper_raises_for_invalid_ref(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"ref": "ref with spaces"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "ref"

    def test_wrapper_raises_for_invalid_username(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"username": "-user"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "username"

    def test_wrapper_raises_for_invalid_sha(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"sha": "abc123"}  # too short
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "sha"

    def test_wrapper_raises_for_invalid_state(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"state": "pending"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "state"

    def test_wrapper_raises_for_invalid_pagination_page(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"page": 0}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "page"

    def test_wrapper_raises_for_invalid_pagination_per_page(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"per_page": 200}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "per_page"

    def test_wrapper_raises_for_invalid_labels_type(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"labels": "not a list"}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "labels"

    def test_wrapper_raises_for_invalid_label_item(self, mock_tool):
        inject_validation_wrapper(mock_tool)
        args = {"labels": [3.14]}
        with pytest.raises(ValidationError) as exc:
            asyncio.run(mock_tool.run(args))
        assert exc.value.field == "labels"

    def test_wrapper_ignores_unknown_parameters(self, mock_tool):
        original_run = mock_tool.run
        inject_validation_wrapper(mock_tool)
        args = {"unknown_param": "value", "another": 123}
        # Should pass through unknown parameters without validation
        result = asyncio.run(mock_tool.run(args))
        assert result == {"result": "ok"}
        original_run.assert_called_once_with(args)

    def test_wrapper_calls_original_run_with_arguments(self, mock_tool):
        original_run = mock_tool.run
        inject_validation_wrapper(mock_tool)
        args = {"owner": "valid", "repo": "valid", "extra": 42}
        asyncio.run(mock_tool.run(args))
        original_run.assert_called_once_with(args)

    def test_original_run_not_called_if_validation_fails(self):
        tool = MagicMock()
        tool.parameters = {"properties": {}}
        original_run = AsyncMock(return_value={"ok": True})
        tool.run = original_run
        inject_validation_wrapper(tool)
        args = {"owner": "-bad"}
        with pytest.raises(ValidationError):
            asyncio.run(tool.run(args))
        # Original run should not have been called because wrapper raised before awaiting
        original_run.assert_not_called()

    def test_wrapper_validates_all_arguments_before_calling(self):
        # If multiple invalid args, should fail on first encountered; but at least original not called
        tool = MagicMock()
        tool.parameters = {"properties": {}}
        original_run = AsyncMock(return_value={"ok": True})
        tool.run = original_run
        inject_validation_wrapper(tool)
        args = {"owner": "-bad", "repo": "-also-bad"}
        with pytest.raises(ValidationError):
            asyncio.run(tool.run(args))
        original_run.assert_not_called()
