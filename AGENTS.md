# Agent Guidelines

**Welcome.** You are building gitea-mcp-server — an MCP server whose primary
consumers are agents like you. Every design decision — lazy loading, concise
results, discovery-first patterns, tool naming conventions — optimises for
agent clarity and token economy.

## What we build

This project auto-generates ~400 tools and resources from Gitea/Forgejo's
Swagger/OpenAPI spec using FastMCP. We work *with* FastMCP, not around it:
when the framework's API lacks something, we add a clean transform layer
that can be removed when FastMCP catches up.

The source Swagger is fetched from the Gitea server at startup, converted
from OpenAPI v2 to v3, then transformed into FastMCP tools and resources.
A small set of synthetic tools (search, tool_info, resolve_type, call_tool)
add discovery and convenience on top.

## Developer handbook

All project conventions, workflows, checklists, common tasks, and FAQ live
in **`docs/SKILL.md`**. Read it before your first change.

The full documentation map — audience, type, topic ownership for every doc
— is in **`docs/INDEX.md`**.

## FastMCP documentation

This project uses FastMCP extensively. Always use
https://gofastmcp.com/llms.txt for current docs — training-memory FastMCP
will be stale.
