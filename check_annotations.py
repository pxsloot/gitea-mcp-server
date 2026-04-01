#!/usr/bin/env python3
"""Test that tool annotations are visible in the running MCP server."""

import asyncio
from fastmcp import Client
from gitea_mcp_server.client import GiteaClient
from gitea_mcp_server.config import Config
import json


async def check_tool_annotations():
    """Connect to the MCP server and inspect tool annotations."""
    print("Connecting to MCP server...")

    # Create a client that connects to the server via stdio
    # Since the server is running, we need to connect to it
    # We'll use the FastMCP client to connect

    # First, let's check if we can import and create a client directly
    try:
        from gitea_mcp_server.server import create_mcp_server
        from gitea_mcp_server.client import GiteaClient

        # Create a config (will use env vars)
        config = Config.get()
        gitea_client = GiteaClient(config)

        # Create the server
        mcp = await create_mcp_server(gitea_client)

        # Get tools
        tools = await mcp.get_tools()

        print(f"\nTotal tools: {len(tools)}")

        # Inspect a few tools for annotations
        tool_list = list(tools.values()) if isinstance(tools, dict) else tools

        print("\n=== Sample Tools with Annotations ===\n")

        # Check tools from different categories
        categories_seen = set()
        for tool in tool_list[:20]:  # Check first 20
            if hasattr(tool, "name") and hasattr(tool, "annotations"):
                print(f"Tool: {tool.name}")
                if tool.annotations:
                    print(f"  Title: {getattr(tool.annotations, 'title', 'N/A')}")
                    # Show all annotation hints
                    for hint in [
                        "readOnlyHint",
                        "destructiveHint",
                        "idempotentHint",
                        "openWorldHint",
                    ]:
                        value = getattr(tool.annotations, hint, None)
                        if value is not None:
                            print(f"  {hint}: {value}")
                if hasattr(tool, "tags"):
                    print(f"  Tags: {tool.tags}")
                    categories_seen.update(tool.tags)
                print()

        print(f"=== Categories found: {sorted(categories_seen)} ===")

        # Verify we have all expected categories
        expected = {"repository", "issue", "pull_request", "user", "organization", "admin", "misc"}
        missing = expected - categories_seen
        if missing:
            print(f"WARNING: Missing categories: {missing}")
        else:
            print("✓ All expected categories are present!")

        await gitea_client.close()

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(check_tool_annotations())
