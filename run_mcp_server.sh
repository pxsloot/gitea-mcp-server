#!/usr/bin/env bash

podman kill gitea-mcp-server &> /dev/null && sleep 1
podman run -d \
  --rm \
  -e $(grep '^GITEA_TOKEN=' .env) \
  -e $(grep '^GITEA_URL=' .env) \
  -e TRANSPORT_TYPE=http \
  -p 8080:8080 \
  --name gitea-mcp-server \
  localhost/gitea-mcp-server:latest

