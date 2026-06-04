"""Unit tests for required scope derivation."""

from gitea_mcp_server.resources.scope import derive_required_scope as _derive_required_scope

class TestDeriveRequiredScope:
    """Tests for the derive_required_scope function."""

    def test_admin_tag_returns_sudo(self):
        assert _derive_required_scope({"admin"}, "GET") == "sudo"
        assert _derive_required_scope({"admin"}, "POST") == "sudo"

    def test_repository_get_returns_read(self):
        assert _derive_required_scope({"repository"}, "GET") == "read:repository"

    def test_repository_post_returns_write(self):
        assert _derive_required_scope({"repository"}, "POST") == "write:repository"

    def test_issue_get_returns_read(self):
        assert _derive_required_scope({"issue"}, "GET") == "read:issue"

    def test_issue_post_returns_write(self):
        assert _derive_required_scope({"issue"}, "POST") == "write:issue"

    def test_organization_tag(self):
        assert _derive_required_scope({"organization"}, "GET") == "read:organization"
        assert _derive_required_scope({"organization"}, "PUT") == "write:organization"

    def test_user_tag(self):
        assert _derive_required_scope({"user"}, "GET") == "read:user"
        assert _derive_required_scope({"user"}, "DELETE") == "write:user"

    def test_notification_tag(self):
        assert _derive_required_scope({"notification"}, "GET") == "read:notification"

    def test_package_tag(self):
        assert _derive_required_scope({"package"}, "POST") == "write:package"

    def test_activitypub_tag(self):
        assert _derive_required_scope({"activitypub"}, "GET") == "read:activitypub"

    def test_miscellaneous_maps_to_misc(self):
        assert _derive_required_scope({"miscellaneous"}, "GET") == "read:misc"

    def test_settings_maps_to_repository(self):
        assert _derive_required_scope({"settings"}, "GET") == "read:repository"

    def test_pull_request_tag_not_in_scope_tags(self):
        """pull_request is a category tag, not a Swagger tag."""
        assert _derive_required_scope({"pull_request"}, "GET") is None

    def test_head_and_options_are_read(self):
        assert _derive_required_scope({"repository"}, "HEAD") == "read:repository"
        assert _derive_required_scope({"repository"}, "OPTIONS") == "read:repository"

    def test_put_delete_patch_are_write(self):
        assert _derive_required_scope({"repository"}, "PUT") == "write:repository"
        assert _derive_required_scope({"repository"}, "DELETE") == "write:repository"
        assert _derive_required_scope({"repository"}, "PATCH") == "write:repository"

    def test_none_tags_returns_none(self):
        assert _derive_required_scope(None, "GET") is None

    def test_empty_tags_returns_none(self):
        assert _derive_required_scope(set(), "GET") is None

    def test_missing_method_defaults_to_write(self):
        assert _derive_required_scope({"repository"}, None) == "write:repository"

    def test_first_known_tag_wins(self):
        """First matching tag in iteration order is used."""
        result = _derive_required_scope({"unknown", "repository", "user"}, "GET")
        assert result in ("read:repository", "read:user")
