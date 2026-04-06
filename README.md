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
- **Tool Customization via Extensions**: Override tool titles, descriptions, and parameter hints using a local `mcp_extensions.yaml` file (no code changes needed)

## MCP Extensions

Sometimes auto-generated tool metadata needs enhancement for clarity. MCP Extensions let you customize tool titles, descriptions, and parameter details **without modifying code**.

### Creating an Extensions File

Create `mcp_extensions.yaml` in your project root:

```yaml
tool_names:
  create_issue:
    title: "Create Issue with Best Practices"
    description: |
      Create a new issue in a Gitea repository.
      
      ## Guidelines
      - Use a clear, descriptive title (max 255 chars)
      - Provide a detailed body explaining the problem or feature
      - Assign appropriate labels and milestones
    parameters:
      - name: title
        description: "Issue title (must be capitalized, max 255 characters)"
        examples:
          - "Bug: Application crashes on startup"
          - "Feature: Add dark mode support"
```

### How It Works

1. On startup, the server loads `mcp_extensions.yaml` (if present)
2. Extensions are applied by matching tool names (snake_case)
3. Customizations override auto-generated metadata:
   - `title` → updates tool's summary/title
   - `description` → replaces tool description
   - `parameters[].description` → updates parameter descriptions
   - `parameters[].examples` → adds usage examples
4. Extensions are removed after application (clean spec)

### Finding Tool Names

To determine which tool name to use:
- Tool names are snake_case versions of operation IDs (e.g., `create_issue`)
- List available tools with `search_tools()` or inspect the running server

### Tips

- Extensions are optional; omit any field you don't want to change
- Use multi-line YAML strings (`|`) for rich descriptions
- All changes are backward compatible and toggleable via config file presence
- Invalid extensions are logged but don't crash the server

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

## Server Instructions & Agent Context

When an MCP client connects, the server provides comprehensive instructions to help agents understand how to use the Gitea MCP Server effectively. These instructions include:

- **Purpose**: What the server does and how it connects to Gitea
- **Authentication**: Setup requirements and environment variables
- **Common Workflows**: Step-by-step patterns for frequent tasks (search issues, create PRs, list repos)
- **Tool Naming Conventions**: Explanation of prefixes (`issue_*`, `repo_*`, `pr_*`, `user_*`, `org_*`)
- **Resource Discovery**: Guidance on using `*_list` tools before acting
- **Lazy Loading Notice**: Information about upcoming performance improvements

Agents can access these instructions during the MCP `initialize` handshake, ensuring they have context before discovering tools.

### Current Limitations & Future Improvements

The server currently exposes all 400+ tools directly, which can impact token usage and selection accuracy. We are planning to implement **lazy loading** using FastMCP search transforms (requires FastMCP 3.x):

- `list_tools()` will return only a synthetic search interface (`search_tools`, `call_tool`) plus a few essential tools
- Agents will first search for relevant tools by keyword or natural language
- Only matching tools will be loaded with full schemas, reducing initial overhead by ~99%

See issue #47 for details and progress.

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

### HTTP Transport (Server Mode)

By default, the server runs in stdio mode (for MCP clients like Claude Desktop). To run as an HTTP server:

```env
# Optional: HTTP transport settings (default: stdio transport)
TRANSPORT_TYPE=http           # Default: stdio
HTTP_HOST=0.0.0.0            # Default: 0.0.0.0
HTTP_PORT=8080               # Default: 8080
HTTP_PATH=/mcp              # Default: /mcp
HTTP_CORS=                  # Optional: comma-separated origins; defaults to GITEA_URL origin
```

- `TRANSPORT_TYPE=http` enables HTTP server mode
- `HTTP_CORS` defaults to the origin derived from `GITEA_URL` (e.g., `https://git.example.com`). Set to `*` to allow all origins (development only).
- The server exposes:
  - `HTTP_PATH` (default `/mcp`) for MCP requests
  - `/health` for health checks (returns `{"status": "ok"}`)

Example usage with `curl`:

```bash
curl -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}' \
     http://localhost:8080/mcp
```

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
