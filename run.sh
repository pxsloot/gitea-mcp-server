#!/usr/bin/env bash
# Development helper script for Gitea MCP Server

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

# Check if .env exists
if [[ ! -f .env ]]; then
    log_warn ".env file not found. Copying from .env.example..."
    cp .env.example .env
    log_warn "Please edit .env with your Gitea credentials before running."
fi

source .env
export TRANSPORT_TYPE=stdio

# Check if virtual environment exists
if [[ ! -d .venv ]]; then
    log_info "Creating virtual environment..."
    uv venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies if needed
if ! python -c "import fastmcp" 2>/dev/null; then
    log_info "Installing dependencies..."
    uv sync --dev
fi

log_info "Starting Gitea MCP Server..."
python -m gitea_mcp_server.server

exit
## 
## docker kill gitea_mcp_server || true
## 
## docker run -d \
##   --name gitea_mcp_server \
##   -e $(grep '^GITEA_TOKEN=' .env) \
##   -e $(grep '^GITEA_URL=' .env) \
##   -e TRANSPORT_TYPE=http \
##   -p 8080:8080 \
##   localhost/gitea-mcp-server:latest
