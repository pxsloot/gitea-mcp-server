# Gitea MCP Server

MCP (Model Context Protocol) server for interacting with Gitea/Forgejo instances. Built with FastMCP 2.0 and Python.

## Features

- **Auto-generated tools** from Gitea's OpenAPI spec (converted from Swagger 2.0)
- **Rich tool annotations**: Automatically generated metadata (read-only, destructive, idempotent hints) for better discovery and safety
- **Permission-aware**: Tools are filtered based on token capabilities
- **Robust HTTP client** with retry logic and timeout handling
- **Structured logging** (JSON or text format)
- **Comprehensive test suite** with unit and integration tests
- **Containerized development**: Includes docker-compose for local Gitea instance

## Prerequisites

- Python 3.11+
- uv or pip (recommended: uv)
- Docker & Docker Compose (optional, for local Gitea instance)

## Installation

```bash
# Clone and install in editable mode
git clone <repo-url>
cd gitea-mcp-server
uv sync  # or: pip install -e ".[dev]"
```

## Configuration

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your Gitea credentials:

   ```env
   GITEA_URL=https://git.your-instance.com
   GITEA_TOKEN=your_api_token_here
   GITEA_VERIFY_SSL=true  # Set to false for self-signed certs
   SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt  # Optional: custom CA bundle
   LOG_LEVEL=INFO
   LOG_FORMAT=json
   ```

### Getting an API Token

1. Log into your Gitea instance
2. Go to Settings → Applications → Generate Token
3. Copy the token to `.env`

**Important**: The token needs appropriate scopes for the operations you want to perform:
- `repo`: Repository access
- `admin`: Administrative operations (if needed)
- `write:issue`, `read:user`, etc. depending on use case

## Usage

### Development (stdio transport)

```bash
# Using uv
uv run gitea-mcp

# Or directly
python -m gitea_mcp_server.server
```

The server runs on stdio and communicates via MCP protocol.

### With Docker Compose

```bash
# Start local test Gitea instance
docker-compose up -d gitea

# Wait a minute for Gitea to initialize, then:
# 1. Access http://localhost:3000
# 2. Complete initial setup (create admin user)
# 3. Generate an API token
# 4. Update .env with:
#    GITEA_URL=http://localhost:3000
#    GITEA_TOKEN=your_token
#    GITEA_VERIFY_SSL=false
#
# Then run the server:
uv run gitea-mcp
```

## Project Structure

```
gitea-mcp-server/
├── src/gitea_mcp_server/
│   ├── __init__.py
│   ├── config.py          # Configuration management
│   ├── client.py          # HTTP client with retry logic
│   ├── openapi_converter.py  # Swagger 2.0 → OpenAPI 3.1 converter
│   ├── server.py          # MCP server setup
│   ├── logging_config.py  # Structured logging
│   └── exceptions.py      # Custom exceptions
├── tests/
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_openapi_converter.py
│   │   └── test_client.py
│   ├── integration/
│   │   └── test_server.py
│   └── conftest.py
├── docs/
│   └── THOUGHTS.md        # Architecture and design notes
├── .env.example
├── pyproject.toml
├── docker-compose.yml
├── run.sh                 # Development helper script
├── swagger.v1.json        # Gitea API spec (downloaded from /swagger.v1.json)
└── README.md
```

## Development

### Running Tests

```bash
# All tests
uv run pytest

# Unit tests only
uv run pytest tests/unit/

# Integration tests
uv run pytest tests/integration/ -v

# With coverage
uv run pytest --cov=gitea_mcp_server
```

### Linting & Type Checking

```bash
# Ruff linting
uv run ruff check src/

# Auto-fix
uv run ruff check --fix src/

# MyPy type checking
uv run mypy src/
```

### Updating the Swagger Spec

The `swagger.v1.json` file is included in the repo. To update it:

```bash
# From a running Gitea instance
curl -H "Authorization: Bearer $GITEA_TOKEN" \
     $GITEA_URL/api/swagger.v1.json \
     -o swagger.v1.json
```

## Architecture

### OpenAPI Conversion Pipeline

1. Load Swagger 2.0 spec from `swagger.v1.json`
2. Convert to OpenAPI 3.1 using `openapi_converter.py`:
   - `definitions` → `components/schemas`
   - `parameters` with `in: body` → `requestBody`
   - `parameters` with `in: formData` → `requestBody` with `multipart/form-data`
   - `securityDefinitions` → `components/securitySchemes`
   - `basePath` → `servers`
   - Fix all `$ref` references
   - Remove deprecated fields (`consumes`, `produces`, `schemes`)
3. Pass converted spec to `FastMCP.from_openapi()`
4. FastMCP auto-generates tools from the spec

### HTTP Client

- Uses `httpx.AsyncClient` with connection pooling
- Automatic retry on transient failures (5xx, 429, network errors)
- Exponential backoff (2-10s, up to 3 attempts)
- Proper timeout configuration (connect/read/write/pool)

### Tool Filtering (Planned)

Tools will be filtered based on token permissions:
- Detect scopes from API response or introspection
- Hide admin tools if token lacks admin rights
- Disable wiki tools if instance has wiki disabled
- Implement via FastMCP tool callbacks

### Tool Annotations

All tools are automatically annotated with descriptive metadata:

- **Title**: Human-readable name generated from OpenAPI summary or operationId
- **Category tags**: `repository`, `issue`, `pull_request`, `user`, `organization`, `admin`, `misc`
- **Safety hints**:
  - `readOnlyHint` - Tool only reads data (GET, HEAD, OPTIONS)
  - `destructiveHint` - Tool can delete data (DELETE methods)
  - `idempotentHint` - Safe to retry (GET, PUT, DELETE, HEAD, OPTIONS)
  - `openWorldHint` - Interacts with external Gitea server (always true)

These annotations help MCP clients provide better UX (filtering, warnings, retry logic) and enable agents to make safer tool selections.

See [docs/TOOL_ANNOTATIONS.md](./docs/TOOL_ANNOTATIONS.md) for complete details.

## Error Handling

- **Configuration errors**: Raised at startup, exit with code 1
- **API errors**: `GiteaAPIError` with status code and response body
- **Spec errors**: `SpecError` for invalid or missing swagger file
- All errors are logged with structured context

## Security

- **Never commit `.env`** - it's gitignored
- Use HTTPS for production instances
- Set `GITEA_VERIFY_SSL=false` only for self-signed certificates
- Store tokens securely; they provide full API access
- `SSL_CERT_FILE` can point to custom CA bundle for internal CAs
- All secrets excluded from logs

## Contributing

1. Create an issue for the feature/bug
2. Create a feature branch: `git switch -c type/XX-short-description`
   - Types: `feature`, `fix`, `refactor`, `docs`, `test`
   - XX = issue number
3. Make changes and ensure tests pass
4. Commit with conventional messages
5. Push and open PR with `Fixes #XX` reference
6. Request review

See [AGENTS.md](./AGENTS.md) for detailed agent guidelines.

## License

MIT
