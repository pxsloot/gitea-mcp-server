"""Tests for ResourceRegistry."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from fastmcp import FastMCP

from gitea_mcp_server.resource_registry import ResourceDef, ResourceRegistry


class TestResourceDef:
    """Tests for ResourceDef dataclass."""

    def test_create(self):
        """Test creating a ResourceDef."""

        def dummy_func():
            pass

        meta = {"cache_ttl": 60}
        rd = ResourceDef(
            uri="gitea://test",
            func=dummy_func,
            mime_type="text/plain",
            tags={"test"},
            meta=meta,
        )
        assert rd.uri == "gitea://test"
        assert rd.func is dummy_func
        assert rd.mime_type == "text/plain"
        assert rd.tags == {"test"}
        assert rd.meta == meta


class TestResourceRegistry:
    """Tests for ResourceRegistry class."""

    def test_init(self):
        """Test registry starts empty."""
        registry = ResourceRegistry()
        assert registry.list_resources() == []

    def test_register(self):
        """Test registering a resource."""
        registry = ResourceRegistry()

        def func():
            return "test"

        registry.register("gitea://test", func, "text/plain", {"tag"})
        resources = registry.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == "gitea://test"
        assert resources[0].func is func
        assert resources[0].mime_type == "text/plain"
        assert resources[0].tags == {"tag"}

    def test_register_duplicate_without_allow_override_raises(self):
        """Test duplicate registration raises error by default."""
        registry = ResourceRegistry()

        def func():
            return "test"

        registry.register("gitea://test", func, "text/plain", {"tag"})
        with pytest.raises(ValueError, match="already registered"):
            registry.register("gitea://test", func, "text/plain", {"tag"})

    def test_register_allow_override(self):
        """Test allow_override replaces existing resource."""
        registry = ResourceRegistry()

        def func1():
            return "first"

        def func2():
            return "second"

        registry.register("gitea://test", func1, "text/plain", {"tag"})
        registry.register("gitea://test", func2, "text/plain", {"tag"}, allow_override=True)
        resources = registry.list_resources()
        assert len(resources) == 1
        assert resources[0].func is func2

    def test_get(self):
        """Test getting a resource by URI."""
        registry = ResourceRegistry()

        def func():
            return "test"

        registry.register("gitea://test", func, "text/plain", {"tag"})
        found = registry.get("gitea://test")
        assert found is not None
        assert found.uri == "gitea://test"
        assert registry.get("gitea://nonexistent") is None

    def test_list_resources(self):
        """Test listing all resources."""
        registry = ResourceRegistry()

        def func1():
            return "1"

        def func2():
            return "2"

        registry.register("gitea://one", func1, "text/plain", {"tag1"})
        registry.register("gitea://two", func2, "text/plain", {"tag2"})
        resources = registry.list_resources()
        assert len(resources) == 2
        uris = {r.uri for r in resources}
        assert uris == {"gitea://one", "gitea://two"}

    def test_list_templates(self):
        """Test listing template URIs (containing '{')."""
        registry = ResourceRegistry()

        def func():
            return "test"

        registry.register("gitea://repos/{owner}/{repo}", func, "application/json", {"api"})
        registry.register("gitea://version", func, "text/plain", {"misc"})
        templates = registry.list_templates()
        assert len(templates) == 1
        assert templates[0].uri == "gitea://repos/{owner}/{repo}"

    def test_get_by_tag(self):
        """Test filtering resources by tag."""
        registry = ResourceRegistry()

        def func1():
            return "1"

        def func2():
            return "2"

        registry.register("gitea://one", func1, "text/plain", {"api", "auto"})
        registry.register("gitea://two", func2, "text/plain", {"wrapper", "repository"})
        api_resources = registry.get_by_tag("api")
        assert len(api_resources) == 1
        assert api_resources[0].uri == "gitea://one"
        wrapper_resources = registry.get_by_tag("wrapper")
        assert len(wrapper_resources) == 1
        assert wrapper_resources[0].uri == "gitea://two"

    def test_apply_to(self):
        """Test applying registry to FastMCP registers all resources."""
        registry = ResourceRegistry()
        results = []

        def make_tracker(uri, **kwargs):
            def decorator(func):
                results.append((uri, func))
                return func

            return decorator

        # Create a mock FastMCP with a resource method that acts as decorator
        mcp = MagicMock(spec=FastMCP)
        mcp.resource = MagicMock(side_effect=make_tracker)

        def func1():
            return "test1"

        def func2():
            return "test2"

        registry.register("gitea://one", func1, "text/plain", {"tag1"}, meta={"cache_ttl": 30})
        registry.register("gitea://two", func2, "application/json", {"tag2"})

        registry.apply_to(mcp)

        assert mcp.resource.call_count == 2
        # Check that calls were made with correct arguments
        call_args_list = mcp.resource.call_args_list
        # First call
        args, kwargs = call_args_list[0]
        assert args[0] == "gitea://one"
        assert kwargs["mime_type"] == "text/plain"
        assert kwargs["tags"] == {"tag1"}
        # meta should be passed as a dict
        assert kwargs["meta"] == {"cache_ttl": 30}
        # The decorator returns the function unchanged; we can verify the passed function is func1
        assert results[0][1] is func1
        assert results[1][1] is func2
