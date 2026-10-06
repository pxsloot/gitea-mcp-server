# Gitea MCP Server

[![CI](https://github.com/pxsloot/gitea-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/pxsloot/gitea-mcp-server/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13%20|%203.14-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Model Context Protocol server that provides ~400 auto-generated tools and
resources for LLM agents to interact with **Gitea** and **Forgejo** instances.
Built with [FastMCP](https://gofastmcp.com) 3.x.

## How it works

```
Your Gitea/Forgejo instance
       │
       ▼  (Swagger/OpenAPI spec)
  gitea-mcp-server
       │  ┌────────────────────────────┐
       │  │ Auto-generates ~400 tools  │
       │  │ from the API spec          │
       │  │ Adds lazy loading, scope   │
       │  │ filtering, annotations,    │
       │  │ workflow guides, resources │
       │  └────────────────────────────┘
       │
       ▼  (MCP protocol: stdio or HTTP)
  Your LLM agent
       │
       ├─ call_tool("gitea_issue_create_issue", ...)
       ├─ read_resource("gitea://repos/owner/repo")
       └─ search_tools("list pull requests")
```

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (package manager)
- A **Gitea** or **Forgejo** instance (local or remote)
- An **API token** with sufficient scopes (Settings → Applications → Generate Token)

## Quick Start

```bash
git clone https://github.com/pxsloot/gitea-mcp-server.git && cd gitea-mcp-server
cp .env.example .env              # then edit GITEA_URL and GITEA_TOKEN
uv sync
uv run python -m gitea_mcp_server
```

### Install from git (pip)

```bash
pip install git+https://github.com/pxsloot/gitea-mcp-server.git
gitea-mcp
```

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `GITEA_URL` | -- | Base URL of your Gitea/Forgejo instance |
| `GITEA_TOKEN` | -- | API token (Settings → Applications → Generate Token) |
| `GITEA_VERIFY_SSL` | `true` | Set `false` for self-signed certs |
| `SSL_CERT_FILE` | -- | Custom CA bundle path |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` or `text` |
| `TRANSPORT_TYPE` | `stdio` | `stdio` or `http` |
| `TOOL_PREFIX` | `gitea_` | Prefix for all tool names |
| `TOOL_FILTERING_ENABLED` | `true` | Hide tools the token's scopes cannot use |
| `ENABLE_LAZY_LOADING` | `true` | Hide tools from `list_tools`; discover them via `search_tools` |
| `EXCLUDE_CONFIG_PATH` | -- | YAML file with tool/resource exclude/include patterns |
| `DEFAULT_RESPONSE_FORMAT` | `markdown` | Default `format` for tool/resource output: `markdown`, `json`, or `raw` |

HTTP transport settings (`TRANSPORT_TYPE=http`):
- `HTTP_HOST` — default `127.0.0.1` (set `HTTP_HOST=0.0.0.0` for remote access)
- `HTTP_PORT` — default 8080
- `HTTP_PATH` — default `/mcp`
- `HTTP_CORS` — defaults to origin from `GITEA_URL`

## Usage

### Stdio (CLI clients)

```bash
uv run python -m gitea_mcp_server
```

### HTTP (server mode)

```bash
TRANSPORT_TYPE=http uv run python -m gitea_mcp_server
# Health check: http://localhost:8080/health
# MCP endpoint: http://localhost:8080/mcp
```

### Docker

```bash
docker build --progress=plain -t gitea-mcp-server:latest .
docker run --rm -e GITEA_URL=... -e GITEA_TOKEN=... gitea-mcp-server:latest
```

For a local test Gitea instance: `docker compose -f docker-compose.gitea.yml up -d`

## Design highlights

Most MCP servers hand-wrap a handful of endpoints. This one is generated from
your instance's own API spec and shaped for how agents actually work: it stays
in lockstep with Gitea, hides what your token cannot use, and spends the minimum
context to be discovered.

**Generated, not hand-wrapped**
- Tools and resources are generated from your instance's Swagger spec
  (converted 2.0 → 3.1) — there is no endpoint list to fall out of date — with
  a small hand-written discovery layer on top.
- Tool and resource metadata ride one typed registration record, checkable over
  the raw MCP transport.

**Built for agent context**
- **Lazy loading** — ~400 tools are found through BM25 search, not listed upfront.
- **Succinct by default** — `format` and `detail` let an agent ask for exactly
  what it needs; large reads stay cheap.
- **Scope-aware** — tools and resources your token cannot use are hidden, not
  discovered as failures at call time.

**Views that stay honest**
- Markdown views are **anchored to the response schema**, so a new or changed
  type renders correctly without a hand-written formatter.
- **One result pipeline** writes both output channels, so every tool returns a
  consistent shape.

**Gitea-native operations**
- Cached, URI-addressed **MCP Resources** for reads
  (`gitea://repos/{owner}/{repo}`).
- **Workflow guides** — 16 guides for the concepts the API alone does not explain.
- **`mcp_extensions.yaml`** — override tool metadata without code.
- stdio + HTTP transports, Docker, and OpenTelemetry observability.

The reasoning behind these choices — and the patterns to follow when extending
the server — is in [docs/DESIGN.md](docs/DESIGN.md).

## Development

```bash
# Tests
uv run pytest tests/unit/ -x -q

# Lint & format
uv run ruff check gitea_mcp_server/
uv run ruff format --check .

# Type-check
uv run mypy gitea_mcp_server/

# Coverage
uv run pytest --cov=gitea_mcp_server
```

See [docs/DESIGN.md](docs/DESIGN.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.
Start with [AGENTS.md](AGENTS.md) for project onboarding. The
[docs/SKILL.md](docs/SKILL.md) has the developer handbook with conventions,
workflows, and checklists for agent contributors.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Security

Report vulnerabilities to **gitea-mcp-server@pxsloot.nl** — see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
