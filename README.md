# Gitea MCP Server

MCP server that provides ~200 tools and resources for LLM agents to
interact with Gitea/Forgejo instances. Built with FastMCP 3.x.

## Quick Start

```bash
git clone <repo-url> && cd gitea-mcp-server
cp .env.example .env          # then edit GITEA_URL and GITEA_TOKEN
uv sync
uv run python -m gitea_mcp_server
```

See [docs/](docs/) for setup details and [DEVELOPMENT.md](docs/DEVELOPMENT.md) for contributing.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `GITEA_URL` | -- | Base URL of your Gitea instance |
| `GITEA_TOKEN` | -- | API token (Settings → Applications → Generate Token) |
| `GITEA_VERIFY_SSL` | `true` | Set `false` for self-signed certs |
| `SSL_CERT_FILE` | -- | Custom CA bundle path |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` or `text` |
| `TRANSPORT_TYPE` | `stdio` | `stdio` or `http` |
| `TOOL_PREFIX` | `gitea_` | Prefix for all tool names |

HTTP transport settings (`TRANSPORT_TYPE=http`): `HTTP_HOST`, `HTTP_PORT` (default 8080),
`HTTP_PATH` (default `/mcp`), `HTTP_CORS` (defaults to origin from `GITEA_URL`).

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

## Key Features

- **Auto-generated tools** from Gitea's Swagger spec (converted 2.0 → 3.1)
- **Lazy loading** -- BM25 search-based tool discovery, not all 200 tools listed upfront
- **Permission filtering** -- tools hidden based on token scopes
- **MCP Resources** -- cached, URI-based data access (`gitea://repos/{owner}/{repo}`)
- **Tool annotations** -- read-only/destructive/idempotent hints per tool
- **mcp_extensions.yaml** -- customize tool metadata without code
- **TOML logging**, HTTP/stdio transport, Docker support

## Contributing

1. Create an issue first
2. Branch: `type/XX-short-description` from latest `main`
3. Changes, tests, PR with `Fixes #XX`

See [AGENTS.md](./AGENTS.md) for detailed workflow rules.

## License

MIT
