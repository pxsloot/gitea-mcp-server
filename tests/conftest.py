"""Pytest configuration and fixtures.

This file provides test infrastructure shared across the entire test suite:
``SimpleConfig`` (canonical test config), session-scoped event loop,
OpenTelemetry setup, and temp workspace.

Helper utilities (mock factories, output parsers, spec fixtures) live in
``tests/helpers/`` — see that package for ``make_mock_tool``,
``make_mock_route``, ``extract_tool_names``, ``base_spec``, and
``minimal_spec``.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest


class SimpleConfig:
    """Canonical test config stub, mirrors essential Config behavior.

    All test files should import this from conftest rather than defining
    their own copy. Test-specific defaults can use ``functools.partial``
    or pass keyword arguments at instantiation.
    """

    def __init__(
        self,
        url="https://git.example.com",
        token="test_token",
        *,
        verify_ssl=False,
        ssl_cert_file=None,
        log_level="ERROR",
        log_format="text",
        tool_filtering_enabled=False,
        enable_lazy_loading=False,
        tool_prefix="gitea_",
        transport_type="stdio",
        http_host="127.0.0.1",
        http_port=8080,
        http_path="/mcp",
        http_cors=None,
        exclude_config_path=None,
        response_format="markdown",
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.ssl_cert_file = ssl_cert_file
        self.log_level = log_level
        self.log_format = log_format
        self.tool_filtering_enabled = tool_filtering_enabled
        self.enable_lazy_loading = enable_lazy_loading
        self.tool_prefix = tool_prefix
        self.transport_type = transport_type
        self.http_host = http_host
        self.http_port = http_port
        self.http_path = http_path
        self.http_cors = http_cors
        self.exclude_config_path = exclude_config_path
        self.response_format = response_format

    @property
    def base_url(self) -> str:
        """Get the API base URL."""
        return f"{self.url}/api/v1"

# Configure logging for tests
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace with sample files."""
    return tmp_path


# ---------------------------------------------------------------------------
# OpenTelemetry - InMemorySpanExporter (session-scoped, shared across modules)
# ---------------------------------------------------------------------------

# OpenTelemetry 1.43+ enforces a set-once guard on the global
# TracerProvider, so we use a session-scoped autouse fixture to
# install the InMemorySpanExporter once for the whole test run.
_TRACE_EXPORTER: Any = None


@pytest.fixture(scope="session", autouse=True)
def _init_otel_exporter() -> None:
    """Set the global TracerProvider with an InMemorySpanExporter (once).

    OpenTelemetry 1.43+ enforces a set-once guard on
    ``set_tracer_provider()``, so we must do this once per session
    rather than in a per-test fixture that saves/restores.
    """
    global _TRACE_EXPORTER  # noqa: PLW0603
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    _TRACE_EXPORTER = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_TRACE_EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture
def trace_exporter() -> Any:
    """Return the shared InMemorySpanExporter, cleared between tests."""
    _TRACE_EXPORTER.clear()
    return _TRACE_EXPORTER


@pytest.fixture
def swagger_spec_fixture() -> dict[str, Any]:
    """Load the swagger spec for tests."""
    spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
    if not spec_path.exists():
        pytest.skip("swagger.v1.json not found")

    with spec_path.open() as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _reset_module_contexts() -> None:
    """Reset module-level ContextVars before each test.

    Prevents cross-test pollution via module-level ContextVars that act as
    side channels between httpx event hooks and the tool wrapping pipeline.

    Currently resets:
    - ``pagination_ctx`` (``pagination.py``): carries ``total_count`` from
      Gitea's ``X-Total-Count`` response header.
    - ``sudo_context`` (``tools/virtual_params.py``): carries the sudo
      username for admin operations.

    These ContextVars are intentionally module-level — that is their design
    (they bridge httpx hooks and FastMCP's transform pipeline without coupling
    to framework internals).  But tests that set them need a safety net.

    Current ``asyncio_default_test_loop_scope = "function"`` provides natural
    isolation for async tests (each gets its own event loop, thus its own
    ``contextvars.Context``).  This fixture adds a deterministic reset for
    every test regardless of sync/async status, making the suite robust
    against future loop-scope changes.
    """
    from gitea_mcp_server.pagination import pagination_ctx
    from gitea_mcp_server.tools.virtual_params import sudo_context

    pagination_ctx.set({})
    sudo_context.set(None)
