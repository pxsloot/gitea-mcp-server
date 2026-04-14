# syntax=docker/dockerfile:1.4

ARG PYTHON_IMAGE=registry.home.lan/local/python-slim:3.14

# Stage 1: builder - builds the wheel
FROM ${PYTHON_IMAGE} AS builder

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
RUN python -m pip install --root-user-action ignore --upgrade pip build && \
    python -m build --wheel

# Stage 2: install runtime dependencies and package
FROM ${PYTHON_IMAGE} AS runner

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
ENV SSL_CERT_DIR=/etc/ssl/certs
ENV TRANSPORT_TYPE=http
ENV HTTP_PATH=/
ENV HTTP_HOST=0.0.0.0
ENV HTTP_PORT=8080

# Switch to non-root user for security
USER app

# Expose HTTP port when running in HTTP mode (default: 8080)
EXPOSE 8080

# Default entrypoint (reads GITEA_URL, GITEA_TOKEN from environment)
#ENTRYPOINT ["gitea-mcp"]
CMD ["gitea-mcp"]

# Stage 3: CI image - adds dev dependencies for lint/test/typecheck
FROM runner AS ci

COPY --from=builder /app /app
USER root
WORKDIR /app
RUN /opt/venv/bin/pip install --no-cache-dir ".[dev]" && \
    rm -rf /tmp/*.whl
