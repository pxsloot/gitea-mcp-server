# Gitea MCP Server

[![CI](https://github.com/pxsloot/gitea-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/pxsloot/gitea-mcp-server/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13%20|%203.14-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Model Context Protocol server that provides ~200 auto-generated tools and
resources for LLM agents to interact with **Gitea** and **Forgejo** instances.
Built with [FastMCP](https://gofastmcp.com) 3.x.

## How it works

```
Your Gitea/Forgejo instance
       │
       ▼  (Swagger/OpenAPI spec)
  gitea-mcp-server
       │  ┌────────────────────────────┐
       │  │ Auto-generates ~200 tools  │
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

## Key Features

- **Auto-generated tools** from Gitea's Swagger spec (converted 2.0 → 3.1)
- **Lazy loading** — BM25 search-based tool discovery, not all 200 tools listed upfront
- **Permission filtering** — tools hidden based on token scopes
- **Workflow guides** — 16 guides explaining Gitea/Forgejo concepts beyond the API
- **MCP Resources** — cached, URI-based data access (`gitea://repos/{owner}/{repo}`)
- **Tool annotations** — read-only/destructive/idempotent hints per tool
- **mcp_extensions.yaml** — customize tool metadata without code
- **HTTP/stdio transport**, Docker support, OpenTelemetry observability

## Development

```bash
# Tests
uv run pytest tests/unit/ -x -q

# Lint & type-check
uv run ruff check gitea_mcp_server/
uv run mypy gitea_mcp_server/

# Coverage
uv run pytest --cov=gitea_mcp_server
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.
The [AGENTS.md](AGENTS.md) has detailed rules for agent contributors.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Security

Report vulnerabilities to **gitea-mcp-server@pxsloot.nl** — see [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
