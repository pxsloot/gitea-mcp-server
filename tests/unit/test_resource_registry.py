"""Tests for the resource_registry module."""


from gitea_mcp_server.resource_registry import ResourceRegistry


class TestResourceRegistry:
    """Tests for ResourceRegistry class."""

    def test_init_creates_empty_registry(self):
        """Test that a new registry is empty."""
        registry = ResourceRegistry()
        assert registry.list_resources() == []

    def test_record_adds_resource(self):
        """Test that record() adds a resource to the registry."""
        registry = ResourceRegistry()

        def dummy_func():
            return "test"

        registry.record(
            uri="gitea://test/{id}",
            func=dummy_func,
            mime_type="text/plain",
            tags={"test", "example"},
            meta={"cache_ttl": 60},
        )

        resources = registry.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == "gitea://test/{id}"
        assert resources[0].func is dummy_func
        assert resources[0].mime_type == "text/plain"
        assert resources[0].tags == {"test", "example"}
        assert resources[0].meta == {"cache_ttl": 60}

    def test_record_overwrites_existing(self):
        """Test that record() with same URI replaces previous entry."""
        registry = ResourceRegistry()

        def func1():
            return "old"

        def func2():
            return "new"

        registry.record(
            uri="gitea://same",
            func=func1,
            mime_type="text/plain",
            tags={"old"},
        )

        assert len(registry.list_resources()) == 1

        registry.record(
            uri="gitea://same",
            func=func2,
            mime_type="application/json",
            tags={"new"},
        )

        resources = registry.list_resources()
        assert len(resources) == 1
        assert resources[0].func is func2
        assert resources[0].mime_type == "application/json"
        assert resources[0].tags == {"new"}

    def test_get_by_uri_returns_correct_resource(self):
        """Test get_by_uri() retrieves resource by URI."""
        registry = ResourceRegistry()

        def sample_func():
            return "sample"

        registry.record(
            uri="gitea://find/me",
            func=sample_func,
            mime_type="text/plain",
            tags=set(),
        )

        resource = registry.get_by_uri("gitea://find/me")
        assert resource is not None
        assert resource.uri == "gitea://find/me"
        assert resource.func is sample_func

    def test_get_by_uri_returns_none_for_missing(self):
        """Test get_by_uri() returns None when URI not found."""
        registry = ResourceRegistry()
        resource = registry.get_by_uri("gitea://missing")
        assert resource is None

    def test_get_by_tag_returns_matching_resources(self):
        """Test get_by_tag() returns only resources with the specified tag."""
        registry = ResourceRegistry()

        def func_a():
            return "a"

        def func_b():
            return "b"

        def func_c():
            return "c"

        registry.record(
            uri="gitea://resource/a",
            func=func_a,
            mime_type="text/plain",
            tags={"wrapper", "repository"},
        )
        registry.record(
            uri="gitea://resource/b",
            func=func_b,
            mime_type="text/plain",
            tags={"wrapper", "issues"},
        )
        registry.record(
            uri="gitea://resource/c",
            func=func_c,
            mime_type="application/json",
            tags={"api", "raw"},
        )

        wrapper_resources = registry.get_by_tag("wrapper")
        assert len(wrapper_resources) == 2
        uris = {r.uri for r in wrapper_resources}
        assert uris == {"gitea://resource/a", "gitea://resource/b"}

        api_resources = registry.get_by_tag("api")
        assert len(api_resources) == 1
        assert api_resources[0].uri == "gitea://resource/c"

    def test_get_by_tag_returns_empty_list_when_no_match(self):
        """Test get_by_tag() returns empty list if no resources have the tag."""
        registry = ResourceRegistry()

        def func():
            return "test"

        registry.record(
            uri="gitea://test",
            func=func,
            mime_type="text/plain",
            tags={"other"},
        )

        results = registry.get_by_tag("nonexistent")
        assert results == []

    def test_list_resources_returns_all_resources(self):
        """Test list_resources() returns all registered resources."""
        registry = ResourceRegistry()

        def func1():
            return "1"

        def func2():
            return "2"

        registry.record(
            uri="gitea://first",
            func=func1,
            mime_type="text/plain",
            tags={"tag1"},
        )
        registry.record(
            uri="gitea://second",
            func=func2,
            mime_type="application/json",
            tags={"tag2"},
            meta={"cache_ttl": 120},
        )

        resources = registry.list_resources()
        assert len(resources) == 2
        uris = {r.uri for r in resources}
        assert uris == {"gitea://first", "gitea://second"}

    def test_record_without_meta(self):
        """Test that record() works correctly when meta is None."""
        registry = ResourceRegistry()

        def dummy_func():
            return "test"

        registry.record(
            uri="gitea://test",
            func=dummy_func,
            mime_type="application/json",
            tags={"test"},
            meta=None,
        )

        resource = registry.get_by_uri("gitea://test")
        assert resource is not None
        assert resource.meta is None
