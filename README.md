# Gitea MCP Server

MCP (Model Context Protocol) server for interacting with Gitea/Forgejo instances. Built with FastMCP 2.0 and Python.

## Features

- **Auto-generated tools** from Gitea's OpenAPI spec (converted from Swagger 2.0)
- **Rich tool annotations**: Automatically generated metadata (read-only, destructive, idempotent hints) for better discovery and safety
- **Permission-aware**: Tools are filtered based on token capabilities
- **MCP Resources**: Efficient, on-demand data retrieval via URI templates (`gitea://...`) with both auto-generated (raw JSON) and custom-formatted (Markdown) options
- **Robust HTTP client** with retry logic and timeout handling
- **Structured logging** (JSON or text format)
- **Comprehensive test suite** with unit and integration tests
- **Containerized development**: Includes docker-compose for local Gitea instance

## Using MCP Resources

MCP resources provide a discoverable, efficient way to read data from Gitea. Unlike tools, resources are accessed via URIs and support caching, making them ideal for data retrieval tasks.

### Quick Start

Agents should typically follow this pattern:

#### 1. Discover Available Resources

```python
# List all resources and templates
result = await mcp_list_resources()
resources = result['resources']

for resource in resources:
    print(f"URI: {resource['uri']}")
    print(f"Name: {resource['name']}")
    print(f"Type: {resource['type']}")  # "resource" or "template"
    print(f"Format: {resource['mimeType']}")
    print(f"Tags: {', '.join(resource['tags'])}")
    print()
```

#### 2. Read Resources

```python
# Static resource (no parameters)
version = await mcp_read_resource("gitea://version")
print(f"Server version: {version}")

# Parameterized template (substitute values)
repo_uri = "gitea://repos/mcp-server/gitea-mcp-server"
readme = await mcp_read_resource(repo_uri + "/readme")
print(readme)  # Plain text README

# Get formatted repository info
repo_info = await mcp_read_resource(repo_uri)
print(repo_info)  # Markdown-formatted repository details
```

#### 3. Work with Templates

Discover templates, then substitute parameters:

```python
# Find all repository-related templates
resources = await mcp_list_resources()
repo_templates = [r for r in resources['resources']
                  if 'repos/{owner}/{repo}' in r['uri']]

for template in repo_templates:
    # Substitute parameters using .format() or f-strings
    uri = template['uri'].format(owner='mcp-server', repo='gitea-mcp-server')
    content = await mcp_read_resource(uri)

    # Use the content...
    if template['mimeType'] == 'text/markdown':
        print(f"=== {template['name']} ===")
        print(content)
```

### Resource Categories

Resources are tagged for easy filtering:

- **wrapper**: Human-friendly formatted content (Markdown). Use for display.
- **raw**: Raw JSON from the API. Use for data processing.
- **api**: Auto-generated from OpenAPI spec. Comprehensive but less formatted.
- **repository**, **issue**, **pull_request**, **user**, **organization**: Entity types.

### Complete Agent Workflow Example

```python
async def analyze_repository(owner: str, repo: str):
    """Comprehensive repository analysis using resources."""

    # 1. Get repository metadata (Markdown)
    repo_uri = f"gitea://repos/{owner}/{repo}"
    repo_info = await mcp_read_resource(repo_uri)
    print(repo_info)

    # 2. Get open issues (Markdown)
    issues_uri = f"gitea://repos/{owner}/{repo}/issues/open"
    issues = await mcp_read_resource(issues_uri)
    print("\nOpen Issues:")
    print(issues)

    # 3. Get pull requests (Markdown)
    prs_uri = f"gitea://repos/{owner}/{repo}/pulls/open"
    prs = await mcp_read_resource(prs_uri)
    print("\nOpen Pull Requests:")
    print(prs)

    # 4. Get README (plain text)
    readme_uri = f"gitea://repos/{owner}/{repo}/readme"
    readme = await mcp_read_resource(readme_uri)
    print("\nREADME Preview (first 500 chars):")
    print(readme[:500])

    # 5. Get releases (Markdown)
    releases_uri = f"gitea://repos/{owner}/{repo}/releases"
    releases = await mcp_read_resource(releases_uri)
    print("\nReleases:")
    print(releases)

    # 6. Get contributor info (JSON raw)
    contributors_uri = f"gitea://repos/{owner}/{repo}/stats/contributors"
    contributors_json = await mcp_read_resource(contributors_uri)
    contributors = json.loads(contributors_json)
    print(f"\nTotal contributors: {len(contributors)}")
```

### Why Resources Over Tools?

Resources offer advantages for read operations:

- **Discoverability**: List all available data sources dynamically
- **Caching**: Built-in caching reduces API calls
- **Consistency**: Standard URI-based access pattern
- **Format control**: Choose between raw JSON or formatted output

### Tips

- Use `mimeType` from `mcp_list_resources` to anticipate content format
- Wrapper resources (`tags` includes "wrapper") provide Markdown suitable for display
- Raw resources (`tags` includes "raw") return JSON for programmatic access
- Templates require exact parameter names from the URI (e.g., `{owner}`, `{repo}`)
- Missing parameters or invalid URIs raise `ValueError`

See `AGENT_GUIDELINES.md` for detailed best practices.

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
│   ├── resources.py       # MCP resources (auto-generated + custom)
│   ├── logging_config.py  # Structured logging
│   └── exceptions.py      # Custom exceptions
├── tests/
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_openapi_converter.py
│   │   ├── test_client.py
│   │   └── test_resources.py
│   ├── integration/
│   │   ├── test_server.py
│   │   └── test_resources_integration.py
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

### MCP Resources

The server provides two types of resources via URI templates (`gitea://...`):

- **Auto-generated resources**: All GET endpoints from the OpenAPI spec are automatically exposed as resources returning raw JSON. These are registered first and provide comprehensive coverage.
- **Custom resources**: Manually implemented resources with user-friendly formatting (Markdown) and convenience wrappers. These are registered after auto-generated ones and automatically override them when URI templates match.

This hybrid approach ensures:
- Complete API coverage through auto-generation
- Optimized, readable output for common use cases via custom resources
- Easy customization and extension beyond the OpenAPI spec

See `src/gitea_mcp_server/resources.py` for implementation details.

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
