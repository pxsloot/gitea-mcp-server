# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
for the **server interface** (configuration, CLI, setup API). The auto-generated
tool surface from Gitea's OpenAPI spec is excluded from the semver contract --
tools may appear, change, or disappear as Gitea evolves.

## [0.3.0] — 2026-07-20

### Added

- Initial public release
- ~200 auto-generated tools from Gitea/Forgejo OpenAPI spec
- BM25 lazy-loading tool discovery (no context pollution from listing all tools)
- Scope-based permission filtering on every tool and resource
- 16 workflow guides explaining Forgejo features beyond the API
- Tool annotations (read-only, destructive, idempotent hints)
- MCP resources for cached, formatted reads (`gitea://repos/{owner}/{repo}`, etc.)
- Customization via YAML overrides (`mcp_extensions.yaml`) without code changes
- Token scope gating with automatic `sudo` parameter visibility
- Virtual parameter system for agent-facing params (e.g., `sudo`, `format`)
- Label string-to-ID auto-conversion
- Cache invalidation middleware for write tools
- OpenTelemetry observability spans
- Unified search across tools, docs, and resources
- `$ref` type resolution tool + resource
- HTTP/stdio transport with CORS support
- Docker multi-stage build (builder → runner → CI targets)
- Local development Gitea via docker-compose
- Comprehensive test suite: 1729 tests at 98% coverage
