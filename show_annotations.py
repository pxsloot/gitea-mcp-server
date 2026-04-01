#!/usr/bin/env python3
"""Connect to running MCP server and display tool annotations."""

import asyncio
from fastmcp import Client


async def main():
    """Connect to the stdio MCP server and inspect tools."""
    # Connect to the server running via stdio
    async with Client("src/gitea_mcp_server/server.py") as client:
        print("Connected to Gitea MCP Server\n")

        # Get all tools
        tools = await client.list_tools()
        print(f"Total tools: {len(tools)}\n")

        # Display tools with their annotations
        print("=== Tools with Annotations ===\n")

        # Group by category if possible
        categorized = {}
        for tool in tools:
            name = tool.name
            annotations = getattr(tool, "annotations", None)
            tags = getattr(tool, "tags", set())

            # Determine category from tags
            category = None
            if tags:
                for tag in tags:
                    if tag in [
                        "repository",
                        "issue",
                        "pull_request",
                        "user",
                        "organization",
                        "admin",
                        "misc",
                    ]:
                        category = tag
                        break

            if category not in categorized:
                categorized[category] = []
            categorized[category].append((name, annotations))

        # Print by category
        for category in sorted(categorized.keys(), key=lambda x: x or "uncategorized"):
            print(f"\n### {category.upper() if category else 'NO CATEGORY'} ###")
            for name, ann in categorized[category][:5]:  # Show first 5 per category
                title = getattr(ann, "title", None) if ann else None
                display = f"- {name}"
                if title:
                    display += f": {title}"
                print(display)
            if len(categorized[category]) > 5:
                print(f"  ... and {len(categorized[category]) - 5} more")


if __name__ == "__main__":
    asyncio.run(main())
