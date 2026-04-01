"""Pytest configuration and fixtures."""

import asyncio
import logging
from pathlib import Path

import pytest

# Configure logging for tests
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with sample files."""
    return tmp_path


@pytest.fixture
def swagger_spec_fixture():
    """Load the swagger spec for tests."""
    spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
    if not spec_path.exists():
        pytest.skip("swagger.v1.json not found")
    import json

    with open(spec_path) as f:
        return json.load(f)
