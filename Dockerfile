# syntax=docker/dockerfile:1.4

# Stage 1: builder - builds the wheel
FROM python:3.11-slim AS builder

# Install system dependencies: build tools for potential C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy all source files (needed for building the package)
COPY . .

# Build wheel (isolated from runtime image)
RUN python -m pip install --upgrade pip build && \
    python -m build --wheel

# Stage 2: install runtime dependencies and package
FROM python:3.11-slim AS runner

# Install system dependencies: build tools for potential C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    curl http://www.home.lan/home_root.crt > /usr/local/share/ca-certificates/home_root.crt \
    curl http://www.home.lan/home_intm.crt > /usr/local/share/ca-certificates/home_intm.crt \
    update-ca-certificates

# Create non-privileged user
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# Copy the built wheel from builder (only wheel, not build deps)
COPY --from=builder /app/dist/*.whl /tmp/

# Install runtime dependencies and the package into a venv
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir /tmp/*.whl && \
    /opt/venv/bin/pip uninstall -y build

ENV PATH="/opt/venv/bin:${PATH}"

# Switch to non-root user for security
USER app

# Expose HTTP port when running in HTTP mode (default: 8080)
EXPOSE 8080

# Default entrypoint (reads GITEA_URL, GITEA_TOKEN from environment)
ENTRYPOINT ["gitea-mcp"]
