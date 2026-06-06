"""Unit tests for OpenAPI converter - operationId normalization."""

from gitea_mcp_server.openapi_converter import convert_paths


class TestOperationIdNormalization:
    """Tests for operationId snake_case conversion in convert_paths."""

    def test_camelcase_operation_id_converted(self):
        """CamelCase operationId should be converted to snake_case."""
        paths = {
            "/repos": {
                "get": {
                    "operationId": "getAllRepos",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/repos"]["get"]["operationId"] == "get_all_repos"

    def test_pascalcase_operation_id_converted(self):
        """PascalCase operationId should be converted to snake_case."""
        paths = {
            "/issues": {
                "post": {
                    "operationId": "CreateIssue",
                    "responses": {"201": {"description": "Created"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/issues"]["post"]["operationId"] == "create_issue"

    def test_mixed_operation_id_converted(self):
        """Mixed-case operationId with repo prefix should be converted to snake_case."""
        paths = {
            "/repos/{owner}/{repo}/branches": {
                "get": {
                    "operationId": "repoGetBranches",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/repos/{owner}/{repo}/branches"]["get"]["operationId"] == "repo_get_branches"

    def test_generated_operation_id_is_snake_case(self):
        """Test that auto-generated operationIds are also snake_case."""
        paths = {
            "/users/{id}": {
                "put": {
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        op_id = result["/users/{id}"]["put"]["operationId"]
        # Generated: method + path with slashes replaced (keeps {param} syntax)
        assert op_id == "put_users_{id}"
        # Verify it's snake_case (no uppercase letters)
        assert op_id == op_id.lower()

    def test_complex_operation_id_with_acronyms(self):
        """OperationId with acronyms and mixed case should be properly converted."""
        paths = {
            "/orgs/{org}/ teams": {
                "get": {
                    "operationId": "getOrgTeams",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/orgs/{org}/ teams"]["get"]["operationId"] == "get_org_teams"

    def test_preserves_snake_case_operation_id(self):
        """Test that already snake_case operationIds remain unchanged."""
        paths = {
            "/test": {
                "get": {
                    "operationId": "already_snake_case",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
        result = convert_paths(paths)
        assert result["/test"]["get"]["operationId"] == "already_snake_case"
