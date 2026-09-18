"""Unit tests for the typed OpenAPI spec factory in tests/helpers/spec_fixtures.py."""

from __future__ import annotations

from tests.helpers.spec_fixtures import make_openapi_spec


class TestMakeOpenApiSpec:
    """Behaviour of the shared spec factory."""

    def test_defaults(self) -> None:
        """No arguments yields the minimal valid default spec."""
        spec = make_openapi_spec()
        assert spec == {
            "openapi": "3.1.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {},
        }

    def test_override_replaces_default(self) -> None:
        """A keyword override replaces the matching default key."""
        spec = make_openapi_spec(openapi="3.1.1", info={"title": "T", "version": "1"})
        assert spec["openapi"] == "3.1.1"
        assert spec["info"] == {"title": "T", "version": "1"}
        assert spec["paths"] == {}

    def test_override_adds_new_key(self) -> None:
        """Keys without a default (components, servers) are added when passed."""
        spec = make_openapi_spec(components={"schemas": {"User": {}}})
        assert spec["components"] == {"schemas": {"User": {}}}
        assert spec["paths"] == {}

    def test_include_defaults_false_is_empty(self) -> None:
        """Disabling defaults with no overrides yields an empty spec."""
        spec = make_openapi_spec(include_defaults=False)
        assert spec == {}

    def test_include_defaults_false_is_exact_key_set(self) -> None:
        """Disabling defaults keeps only explicitly passed keys (no paths)."""
        spec = make_openapi_spec(
            include_defaults=False,
            openapi="3.1.1",
            info={"title": "T", "version": "1"},
        )
        assert spec == {"openapi": "3.1.1", "info": {"title": "T", "version": "1"}}
        assert "paths" not in spec
