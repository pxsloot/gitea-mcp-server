# Default to false for local dev with self-signed certs (can override via env)
GITEA_VERIFY_SSL ?= false

.PHONY: help docker-build docker-build-ci docker-push docker-run docker-run-http docker-test docker-shell clean

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

test:
	ruff check gitea_mcp_server
	mypy gitea_mcp_server
	pytest --cov=gitea_mcp_server --cov-report=xml --cov-report=term-missing

docker-test:
	docker run --rm localhost/gitea-mcp-server:ci ruff check gitea_mcp_server
	docker run --rm localhost/gitea-mcp-server:ci mypy gitea_mcp_server
	docker run --rm localhost/gitea-mcp-server:ci pytest --cov=gitea_mcp_server --cov-report=xml --cov-report=term-missing

docker-build: ## Build the Docker image locally
	docker build --progress=plain --target runner -t gitea-mcp-server:latest .

docker-build-ci: docker-build ## Build the CI image with dev dependencies
	docker build --progress=plain --target ci -t gitea-mcp-server:ci .

docker-push: ## Push image to registry.home.lan
	docker tag gitea-mcp-server:latest registry.home.lan/mcp-server/gitea-mcp-server:latest
	docker push registry.home.lan/mcp-server/gitea-mcp-server:latest

docker-push-ci: ## Push CI image to registry.home.lan
	docker tag gitea-mcp-server:ci registry.home.lan/mcp-server/gitea-mcp-server:ci
	docker push registry.home.lan/mcp-server/gitea-mcp-server:ci

docker-run: ## Run the container in stdio mode (requires GITEA_URL and GITEA_TOKEN)
	docker run --rm \
		-e GITEA_URL=$(GITEA_URL) \
		-e GITEA_TOKEN=$(GITEA_TOKEN) \
		-e GITEA_VERIFY_SSL=$(GITEA_VERIFY_SSL) \
		gitea-mcp-server:latest

docker-run-http: ## Run as HTTP server on port 8080
	docker run -d --name gitea-mcp-http \
		-p 8080:8080 \
		-e GITEA_URL=$(GITEA_URL) \
		-e GITEA_TOKEN=$(GITEA_TOKEN) \
		-e GITEA_VERIFY_SSL=$(GITEA_VERIFY_SSL) \
		-e TRANSPORT_TYPE=http \
		-e HTTP_PATH=/ \
		-e HTTP_HOST=0.0.0.0 \
		-e HTTP_PORT=8080 \
		gitea-mcp-server:latest

docker-version: ## Test the container by checking version
	docker run --rm gitea-mcp-server:latest --version

docker-shell: ## Open a shell in the container for debugging
	docker run -it --rm gitea-mcp-server:latest /bin/bash

docker-stop-http: ## Stop the HTTP container
	docker stop gitea-mcp-http || true
	docker rm gitea-mcp-http || true

clean: ## Remove built Docker image
	docker rmi gitea-mcp-server:latest || true

# Gitea development environment
dev-up: ## Start local Gitea instance with docker-compose
	docker compose -f docker-compose.gitea.yml up -d

dev-down: ## Stop local Gitea instance
	docker compose -f docker-compose.gitea.yml down

dev-logs: ## Show logs from Gitea container
	docker compose -f docker-compose.gitea.yml logs -f gitea

dev-clean: ## Remove Gitea volumes and data
	docker compose -f docker-compose.gitea.yml down -v
	rm -rf gitea_data
