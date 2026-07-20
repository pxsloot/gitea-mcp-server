# syntax=docker/dockerfile:1.4

ARG PYTHON_IMAGE=python:3.14-slim-bookworm

################################################################################
# Stage 1: builder - installs deps + project into a venv
################################################################################
FROM ${PYTHON_IMAGE} AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies: build tools for potential C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock ./

# Install production dependencies (cached layer unless deps change)
RUN uv sync --locked --no-dev --no-install-project --no-editable

# Copy the project source
COPY . .

# Install the project (non-editable, so .venv can be copied independently)
RUN uv sync --locked --no-dev --no-editable

################################################################################
# Stage 2: runner - minimal runtime image (no source code, no build deps)
################################################################################
FROM ${PYTHON_IMAGE} AS runner

# Install uv for any runtime uv operations
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create non-privileged user
RUN useradd --create-home --shell /bin/bash app

WORKDIR /app

# Copy the virtual environment, not the source code (minimal size)
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}"
ENV SSL_CERT_DIR=/etc/ssl/certs
ENV TRANSPORT_TYPE=http
ENV HTTP_PATH=/
ENV HTTP_HOST=0.0.0.0
ENV HTTP_PORT=8080

# Switch to non-root user for security
USER app

EXPOSE 8080

CMD ["gitea-mcp"]

################################################################################
# Stage 3: CI image - adds dev dependencies for lint/test/typecheck
################################################################################
FROM runner AS ci

USER root

# Copy source code for linting/testing
COPY --from=builder /app /app

# Install dev dependencies using the lock file
RUN uv sync --locked --no-editable

ENV GITEA_TOKEN=test
ENV GITEA_URL=https://test.example.com
