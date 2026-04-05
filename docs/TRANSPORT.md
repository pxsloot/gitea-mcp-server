# Transport Configuration Guide

## Overview

Gitea MCP Server supports two transport mechanisms:

1. **stdio** (default): Standard input/output for MCP communication. Used when the server runs as a subprocess of the MCP client (e.g., Claude Desktop, OpenCode).
2. **streamable-http**: HTTP-based transport where the server exposes an HTTP endpoint. Useful for containerized deployments, remote access, or when the server needs to run independently.

## Comparison

| Feature | stdio | streamable-http |
|---------|-------|----------------|
| **Communication** | Unix pipes (stdin/stdout) | HTTP (Server-Sent Events or JSON) |
| **Default** | Yes (default) | No (must enable) |
| **Port** | N/A | Configurable (default: 8080) |
| **CORS** | N/A | Auto-configured from `GITEA_URL` |
| **Health Check** | N/A | `/health` endpoint |
| **Stateless Mode** | N/A | Supported via `STATELESS_HTTP` |
| **Use Case** | Local subprocess | Container, remote, or separate process |
| **Performance** | Low latency (local) | HTTP overhead, but network transparent |

### When to Use Which?

- **stdio**: Use when the MCP client (Claude Desktop, OpenCode) can spawn the server as a subprocess. This is the standard MCP pattern and offers the best performance and simplest setup.
- **streamable-http**: Use when:
  - Running the server in a Docker container separate from the client
  - Need to access the server over the network
  - Want to use HTTP load balancers or reverse proxies
  - Need health monitoring via HTTP
  - Want stateless sessions for better scalability

## Configuration Options

### Core Settings

| Environment Variable | Default | Required | Description |
|---------------------|---------|----------|-------------|
| `TRANSPORT_TYPE` | `stdio` | No | Transport type. Valid: `stdio`, `streamable-http`. |
| `HOST` | `127.0.0.1` | No | Host interface to bind HTTP server to (HTTP transport only). |
| `PORT` | `8080` | No | Port to bind HTTP server to (HTTP transport only). Range: 1-65535. |
| `HTTP_PATH` | `/mcp` | No | Path for the MCP endpoint (HTTP transport only). |
| `STATELESS_HTTP` | `false` | No | Enable stateless mode. Each request creates a new session (HTTP transport only). |
| `JSON_RESPONSE` | `null` | No | Force JSON response format. `true` = JSON only, `false` = SSE, `null` = auto-detect. |
| `CORS_ORIGINS` | (auto) | No | Comma-separated list of allowed CORS origins. If not set, automatically derived from `GITEA_URL`. |

### Inheritance

All standard configuration variables still apply:

- `GITEA_URL`: Gitea instance URL (required)
- `GITEA_TOKEN`: API token (required)
- `GITEA_VERIFY_SSL`: SSL verification (default: `true`)
- `SSL_CERT_FILE`: Custom CA bundle path
- `LOG_LEVEL`: Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- `LOG_FORMAT`: Log format (`json` or `text`)
- `TOOL_FILTERING_ENABLED`: Enable permission-based tool filtering (default: `true`)
- `ENABLE_LAZY_LOADING`: Enable lazy loading via search transform (default: `true`)

## Enabling HTTP Transport

### 1. Set Environment Variables

```bash
export TRANSPORT_TYPE=streamable-http
export PORT=8080  # optional, default is 8080
export HOST=0.0.0.0  # optional, to listen on all interfaces
```

### 2. Run the Server

```bash
uv run gitea-mcp
```

The server will start listening on `http://HOST:PORT/HTTP_PATH` (default: `http://127.0.0.1:8080/mcp`).

### 3. Configure MCP Client

In your MCP client configuration (e.g., `claude_desktop_config.json`), use:

```json
{
  "mcpServers": {
    "gitea": {
      "type": "streamable-http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

**Note**: The exact configuration format depends on your MCP client. Check client documentation for "streamable-http" support.

## CORS Configuration

CORS is automatically configured based on your `GITEA_URL`. If `GITEA_URL=https://gitea.example.com`, the server automatically allows requests from `https://gitea.example.com`.

### Override CORS Origins

If you need to allow additional origins (e.g., different domains or localhost for development), set `CORS_ORIGINS` explicitly:

```bash
export CORS_ORIGINS=http://localhost:3000,http://app.example.com
```

This overrides the auto-derivation from `GITEA_URL`.

## Health Check Endpoint

When running with HTTP transport, a health check endpoint is available at `/health`:

```bash
curl http://localhost:8080/health
# Response: OK (HTTP 200)
```

Use this for:

- **Docker health checks**: `HEALTHCHECK --interval=30s CMD curl -f http://localhost:8080/health`
- **Kubernetes liveness/readiness probes**
- **Load balancer health monitoring**
- **Container orchestration**

### Health Check Behavior

- Returns `200 OK` with body `OK`
- No authentication required
- Does not depend on Gitea connection
- Returns quickly (no external calls)

## Security Considerations

### Network Exposure

- By default, HTTP server binds to `127.0.0.1` (localhost only). Change `HOST=0.0.0.0` to listen on all interfaces.
- Use a reverse proxy (nginx, Traefik, Caddy) for:
  - TLS termination
  - Authentication (basic auth, OAuth)
  - Rate limiting
  - Request logging
- Do not expose directly to the internet without a proxy and proper authentication.

### Authentication

The MCP endpoint **does not** implement its own authentication. Access control should be handled by:

1. **Network-level**: Firewall rules, private networks
2. **Reverse proxy**: Basic auth, client certificates, OAuth
3. **VPN**: Private network access only

The server authenticates to Gitea via `GITEA_TOKEN`, but this is separate from client access to the MCP endpoint.

### Stateless vs Stateful Mode

- **Stateful** (`STATELESS_HTTP=false`, default): Each client connection maintains session state (handshake, capabilities). Better performance for persistent connections.
- **Stateless** (`STATELESS_HTTP=true`): Each request is independent. More scalable for serverless or load-balanced scenarios, but slightly higher overhead.

Choose stateless if:
- Deploying to serverless platforms (AWS Lambda, Cloudflare Workers)
- Using a load balancer without sticky sessions
- Wanting simpler horizontal scaling

### CORS Security

- Only allow origins you trust. The auto-configured origin from `GITEA_URL` is typically safe (your Gitea instance).
- If manually setting `CORS_ORIGINS`, be specific: `https://app.example.com` not `*`.

## Docker Deployment

### Dockerfile Example

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir uv && uv pip install --system .

COPY src/ ./src/
COPY swagger.v1.json ./

EXPOSE 8080

ENV TRANSPORT_TYPE=streamable-http
ENV PORT=8080

CMD ["gitea-mcp"]
```

### Docker Compose Example

```yaml
version: '3.8'

services:
  gitea-mcp:
    build: .
    environment:
      - GITEA_URL=https://gitea.example.com
      - GITEA_TOKEN=${GITEA_TOKEN}
      - TRANSPORT_TYPE=streamable-http
      - PORT=8080
      - LOG_LEVEL=INFO
      - LOG_FORMAT=json
    ports:
      - "8080:8080"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### Kubernetes Example

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gitea-mcp
spec:
  replicas: 2
  selector:
    matchLabels:
      app: gitea-mcp
  template:
    metadata:
      labels:
        app: gitea-mcp
    spec:
      containers:
      - name: gitea-mcp
        image: gitea-mcp:latest
        ports:
        - containerPort: 8080
        env:
        - name: GITEA_URL
          value: "https://gitea.example.com"
        - name: GITEA_TOKEN
          valueFrom:
            secretKeyRef:
              name: gitea-mcp-secret
              key: token
        - name: TRANSPORT_TYPE
          value: "streamable-http"
        - name: PORT
          value: "8080"
        - name: STATELESS_HTTP
          value: "true"
        env:
        - name: LOG_LEVEL
          value: "INFO"
        - name: LOG_FORMAT
          value: "json"
        ports:
        - containerPort: 8080
          name: http
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
```

## Troubleshooting

### "Connection refused" when connecting to HTTP endpoint

1. Verify server is running: `curl http://localhost:8080/health`
2. Check `HOST` and `PORT` settings (server binds to `HOST:PORT`)
3. Ensure firewall allows connections (if not localhost)
4. Check server logs for startup errors

### CORS errors in browser

1. Check `CORS_ORIGINS` includes your frontend's origin
2. Verify that you're not overriding auto-configuration unintentionally
3. Ensure `TRANSPORT_TYPE=streamable-http` is set

### Health check fails

1. Confirm server is listening on expected port
2. Check that `/health` endpoint is accessible
3. Review server logs for errors
4. Verify `TRANSPORT_TYPE=streamable-http` is set (health check only exists for HTTP transport)

### "Invalid TRANSPORT_TYPE" error

Valid values are `stdio` and `streamable-http`. Check for typos.

### Port already in use

Change `PORT` to an available port (e.g., `PORT=8081`).

### MyPy/type checking errors with starlette

Ensure `starlette` is installed. Run `uv pip install starlette`.

## Advanced Configuration

### JSON vs SSE Response Format

The `JSON_RESPONSE` variable controls the response format:

- `null` (default): Auto-detect based on client Accept header. Prefers SSE but falls back to JSON.
- `true`: Force JSON responses only. Use if client doesn't support SSE.
- `false`: Force Server-Sent Events (SSE). Better for streaming large responses.

```bash
export JSON_RESPONSE=true  # JSON only
export JSON_RESPONSE=false # SSE only
```

### Stateless Mode Tradeoffs

| Aspect | Stateful | Stateless |
|--------|----------|-----------|
| Performance | Faster (session reuse) | Slower (handshake per request) |
| Memory | Per-connection state | Minimal |
| Scalability | Needs sticky sessions | Horizontally scalable |
| Use Case | Single server, persistent clients | Load balancers, serverless |

## References

- [FastMCP Documentation](https://gofastmcp.com)
- [MCP Specification](https://github.com/modelcontextprotocol/specification)
- [Server-Sent Events (MDN)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
- [CORS (MDN)](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS)
