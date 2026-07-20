# Contributing to Gitea MCP Server

First off, thanks for taking the time to contribute! 🎉

## Where to start

- **Issues**: Check [open issues](https://github.com/pxsloot/gitea-mcp-server/issues) for things that need work.
  If you have an idea or found a bug, open an issue first — don't start coding
  without discussion.
- **Discussions**: Use GitHub Discussions for questions, ideas, or help getting
  started (if enabled).

## Development workflow

This project follows a **issue → branch → PR** workflow.

### 1. Create an issue first

Before writing code, open an issue describing what you want to do. This avoids
wasted effort on changes that won't be accepted.

### 2. Branch from latest `main`

```bash
git checkout main && git pull
git checkout -b type/XX-short-description
```

Branch naming: `<type>/<issue-number>-<short-description>`

| Type | When to use |
|------|-------------|
| `feature` | New functionality |
| `fix` | Bug fix |
| `refactor` | Code reorganization without behaviour change |
| `docs` | Documentation changes |
| `chore` | Maintenance, CI, tooling |

Examples: `feature/42-add-milestone-resource`, `fix/99-handle-empty-body`

### 3. Make your changes

- Keep changes focused on the issue — one PR, one concern.
- Follow the project's code conventions (ruff and mypy enforce these).
- Add or update tests. The project aims for 85%+ coverage.
- Update documentation if your change affects user-visible behaviour.

### 4. Run the checks locally

```bash
make test           # ruff → mypy → pytest with coverage
```

All checks must pass before a PR is accepted.

### 5. Open a pull request

- PR title should summarise the change (imperative mood, no period).
- PR body must include `Fixes #XX` referencing the issue.
- Fill in the PR template if one is provided.

## Development environment

```bash
# Prerequisites: Python 3.11+, uv
uv sync
cp .env.example .env   # then edit GITEA_URL and GITEA_TOKEN
uv run python -m gitea_mcp_server
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed setup instructions,
including Docker, testing, and OpenTelemetry.

## Testing

```bash
# All tests
uv run pytest

# Unit tests only (fast)
uv run pytest tests/unit/ -x -q

# Integration tests (mocked HTTP)
uv run pytest tests/integration/

# With coverage
uv run pytest --cov=gitea_mcp_server
```

The test suite is comprehensive (1729 tests, 98% coverage). Always add tests
for new code. See [docs/TESTING_STANDARDS.md](docs/TESTING_STANDARDS.md) for
patterns and conventions.

## Code of conduct

This project follows a [Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you are expected to uphold this code.

## Questions?

Open a Discussion or issue on GitHub.
