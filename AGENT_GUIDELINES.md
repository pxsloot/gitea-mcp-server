# Agent Guidelines: Using MCP Resources

## Overview

This guide provides best practices for agent developers using the Gitea MCP server's resource system. Resources provide efficient, discoverable, and cacheable access to Gitea data.

## Core Principle: Prefer Resources for Reads

**Recommendation**: Use `mcp_read_resource()` for fetching data. Use tools only for operations that modify state (create, update, delete) or require complex parameterization.

### ✅ Recommended: Using Resources

```python
# Read repository info
repo = await mcp_read_resource("gitea://repos/mcp-server/gitea-mcp-server")

# Read issues
issues = await mcp_read_resource("gitea://repos/mcp-server/gitea-mcp-server/issues")

# Read README
readme = await mcp_read_resource("gitea://repos/mcp-server/gitea-mcp-server/readme")
```

### ❌ Not Recommended: Abusing Tools for Reads

```python
# DON'T: Use tool calls for simple data retrieval
repo = await gitea_get_repo(owner='mcp-server', repo='gitea-mcp-server')
issues = await gitea_list_issues(owner='mcp-server', repo='gitea-mcp-server')
```

## Why Resources?

1. **Discoverability**: `mcp_list_resources()` reveals all available data sources dynamically
2. **Caching**: Resources have built-in caching (TTL varies by resource, typically 30s-10min)
3. **Consistency**: Standard URI-based access pattern simplifies code
4. **Format variety**: Choose between raw JSON or formatted Markdown
5. **Simpler parameters**: URI templates are often simpler than tool parameters

## Resource Categories

### Wrapper Resources
- **Tags**: Include `"wrapper"`
- **MIME type**: Usually `text/markdown` or `text/plain`
- **Use case**: Display to users, reading by humans
- **Example**: `gitea://repos/{owner}/{repo}` returns formatted Markdown

### Raw Resources
- **Tags**: Include `"raw"` or `"api"`
- **MIME type**: `application/json`
- **Use case**: Data processing, filtering, aggregation
- **Example**: `gitea://repos/{owner}/{repo}/stats/contributors` returns JSON

## The Discovery-Read Pattern

Always discover resources before reading:

```python
async def get_repo_summary(owner: str, repo: str):
    """Get a comprehensive repository summary."""

    # Step 1: Discover available resources (optional but recommended)
    # This helps with validation and understanding what's available
    all_resources = await mcp_list_resources()

    # Step 2: Build URIs for needed resources
    repo_uri = f"gitea://repos/{owner}/{repo}"
    issues_uri = f"{repo_uri}/issues/open"
    prs_uri = f"{repo_uri}/pulls/open"
    readme_uri = f"{repo_uri}/readme"

    # Step 3: Read all needed resources (can be parallelized)
    repo_data, issues_data, prs_data, readme = await asyncio.gather(
        mcp_read_resource(repo_uri),
        mcp_read_resource(issues_uri),
        mcp_read_resource(prs_uri),
        mcp_read_resource(readme_uri),
    )

    # Step 4: Use the data
    return {
        "repository": repo_data,  # Markdown
        "open_issues": issues_data,  # Markdown
        "open_prs": prs_data,  # Markdown
        "readme": readme,  # Plain text
    }
```

## Parameter Substitution

Templates use `{param}` placeholders. Substitute with:

```python
# Method 1: str.format()
uri = "gitea://repos/{owner}/{repo}/issues".format(owner='user', repo='project')

# Method 2: f-string (preferred for clarity)
owner = 'user'
repo = 'project'
uri = f"gitea://repos/{owner}/{repo}/issues"

# Method 3: Replace manually (if needed)
template = "gitea://repos/{owner}/{repo}"
uri = template.replace("{owner}", owner).replace("{repo}", repo)
```

## When to Use Tools Instead

Despite the preference for resources, some operations require tools:

| Use Tools For | Use Resources For |
|---------------|-------------------|
| Creating/updating/deleting data | Reading data |
| Operations requiring complex auth or scopes | Standard GET operations |
| Non-standard API calls | Standard API endpoints |
| Actions with side effects | Cached, read-only data |

**Example - Creating an issue requires a tool:**

```python
# MUST use tool (modifies state)
issue = await gitea_create_issue(
    owner='mcp-server',
    repo='gitea-mcp-server',
    title='Bug report',
    body='Description...'
)
```

**Example - Reading issues uses resources:**

```python
# PREFER resource (read-only)
issues = await mcp_read_resource("gitea://repos/mcp-server/gitea-mcp-server/issues")
```

## Common Patterns

### Pattern 1: Get Entity Summary

```python
async def get_entity_summary(entity_type: str, name: str):
    """Get a formatted summary of a Gitea entity."""

    if entity_type == 'repository':
        uri = f"gitea://repos/{name}"
    elif entity_type == 'user':
        uri = f"gitea://users/{name}"
    elif entity_type == 'organization':
        uri = f"gitea://orgs/{name}"
    else:
        raise ValueError(f"Unknown entity type: {entity_type}")

    return await mcp_read_resource(uri)  # Always Markdown
```

### Pattern 2: Raw Data Processing

```python
import json

async def analyze_contributors(owner: str, repo: str):
    """Analyze contributor statistics from raw JSON."""

    # Use raw JSON resource for data processing
    uri = f"gitea://repos/{owner}/{repo}/stats/contributors"
    contributors_json = await mcp_read_resource(uri)
    contributors = json.loads(contributors_json)

    # Process the data
    total_commits = sum(c['total'] for c in contributors)
    top_contributor = max(contributors, key=lambda c: c['total'])

    return {
        'contributor_count': len(contributors),
        'total_commits': total_commits,
        'top_contributor': top_contributor['author']['login'],
        'top_commits': top_contributor['total'],
    }
```

### Pattern 3: Content Search

```python
def find_issue_uris(owner: str, repo: str, state: str = 'open') -> list[str]:
    """Build URIs for issue-related resources."""
    base = f"gitea://repos/{owner}/{repo}/issues"
    if state == 'open':
        return [base, f"{base}/open"]
    elif state == 'closed':
        return [f"{base}/closed"]
    else:
        return [base]

async def get_issues(owner: str, repo: str, state: str = None) -> str:
    """Get issues in specified state."""
    if state:
        uri = f"gitea://repos/{owner}/{repo}/issues/{state}"
    else:
        uri = f"gitea://repos/{owner}/{repo}/issues"
    return await mcp_read_resource(uri)
```

## Error Handling

Resources may fail due to:
- Invalid URI (missing parameters, unknown resource)
- Non-existent entity (404)
- Network errors
- Permission errors

**Always wrap calls:**

```python
try:
    content = await mcp_read_resource(uri)
    # Process content...
except ValueError as e:
    print(f"Resource error: {e}")
    # Fallback or error handling...
```

### Distinguishing Error Types

```python
try:
    content = await mcp_read_resource(uri)
except ValueError as e:
    error_msg = str(e)

    if "not found" in error_msg.lower() or "404" in error_msg:
        # Entity doesn't exist
        handle_missing_entity()
    elif "missing required path parameter" in error_msg.lower():
        # Programming error - wrong URI
        handle_invalid_uri()
    else:
        # Network or API error
        handle_api_error()
```

## Anti-Patterns

### ❌ Anti-Pattern 1: Ignoring Discovery

```python
# BAD: Hardcoding URIs without checking availability
uri = "gitea://some/path"  # May not exist or may have changed
content = await mcp_read_resource(uri)  # Could fail
```

```python
# GOOD: Discover first or validate URI existence
resources = await mcp_list_resources()
if any(r['uri'] == uri for r in resources['resources']):
    content = await mcp_read_resource(uri)
else:
    # Handle missing resource
    pass
```

### ❌ Anti-Pattern 2: Mixing Formats

```python
# BAD: Assuming all repo resources return the same format
repo_info = await mcp_read_resource("gitea://repos/owner/repo")  # Markdown
contributors = await mcp_read_resource("gitea://repos/owner/repo/stats/contributors")  # JSON

# Processing Markdown as JSON will fail
data = json.loads(repo_info)  # ERROR: repo_info is Markdown, not JSON
```

```python
# GOOD: Check MIME type before processing
resources = await mcp_list_resources()
repo_meta = next(r for r in resources['resources'] if r['uri'] == target_uri)

if repo_meta['mimeType'] == 'application/json':
    data = json.loads(content)
elif repo_meta['mimeType'] == 'text/markdown':
    # Process as Markdown
    pass
```

### ❌ Anti-Pattern 3: Recursive Discovery in Loops

```python
# BAD: Calling mcp_list_resources in a tight loop
for repo in repo_list:
    resources = await mcp_list_resources()  # Redundant!
    uri = f"gitea://repos/{repo['owner']}/{repo['name']}"
    # ...
```

```python
# GOOD: Discover once, reuse
resources = await mcp_list_resources()
for repo in repo_list:
    uri = f"gitea://repos/{repo['owner']}/{repo['name']}"
    content = await mcp_read_resource(uri)
    # ...
```

### ❌ Anti-Pattern 4: Unnecessary Parameterization

```python
# BAD: Using a template when you could use a specific resource
template = "gitea://repos/{owner}/{repo}/issues"
for issue_number in [1, 2, 3]:
    # Issues are not parameterized by number in resources
    # Use gitea_get_issue tool instead
    issue = await mcp_read_resource(template.format(owner='o', repo='r'))  # Wrong!
```

```python
# GOOD: Use tools for single-item access
for issue_number in [1, 2, 3]:
    issue = await gitea_get_issue(owner='owner', repo='repo', index=issue_number)
```

## Caching Considerations

Resources have built-in caching with TTL (time-to-live):

- **Wrapper resources**: Typically 5-10 minutes (repos, users, orgs)
- **Dynamic resources**: Typically 30 seconds (issues, PRs)
- **Static resources**: Often longer (releases, tags)

**You don't need to cache manually** - the server handles it. However:

- Avoid calling the same resource repeatedly in quick succession
- For fresh data, wait for cache to expire or use tools (if available)
- Cache TTLs are server-configurable and may change

## Performance Tips

1. **Parallel reads**: Use `asyncio.gather()` for independent resources
   ```python
   results = await asyncio.gather(
       mcp_read_resource(uri1),
       mcp_read_resource(uri2),
       mcp_read_resource(uri3),
   )
   ```

2. **Batch when possible**: Some resources return lists; filter locally instead of fetching per-item
   ```python
   # Good: Get all issues, filter in code
   all_issues = await mcp_read_resource("gitea://repos/owner/repo/issues")
   open_issues = [i for i in parse_markdown_issues(all_issues) if i['state'] == 'open']

   # Bad: Fetch each state separately (inefficient)
   open_issues = await mcp_read_resource("gitea://repos/owner/repo/issues/open")
   closed_issues = await mcp_read_resource("gitea://repos/owner/repo/issues/closed")
   ```

3. **Discover once**: Call `mcp_list_resources()` once at startup, cache the result

4. **Use raw for processing, wrapper for display**:
   ```python
   # For computation: use raw JSON
   data_json = await mcp_read_resource(raw_uri)
   data = json.loads(data_json)
   result = compute_stats(data)

   # For user display: use wrapper Markdown
   summary = await mcp_read_resource(wrapper_uri)
   print(summary)
   ```

## Migration: Tools to Resources

If you have existing code using tools for reads:

```python
# Old pattern (tool-based)
old_code:
    issues = await gitea_list_issues(owner='o', repo='r', state='open')
    # Process issues...

# New pattern (resource-based)
new_code:
    uri = f"gitea://repos/o/r/issues/open"
    issues_markdown = await mcp_read_resource(uri)
    # Parse Markdown or just display it

    # Or use raw if you need structured data
    raw_uri = f"gitea://repos/o/r/issues"  # With state filter as query param?
    # Note: Not all query params are exposed; check available templates
```

**When migrating:**
1. Check if a resource template exists for your use case
2. Compare output formats (JSON vs Markdown)
3. Adjust parsing logic accordingly
4. Update error handling (resources raise `ValueError`, tools may raise different exceptions)

## Resource Registration Patterns

The server registers resources in two phases:

1. **Auto-generated**: All GET OpenAPI endpoints become `gitea://...` resources returning raw JSON
2. **Custom**: Manually implemented resources override auto-generated ones with same URI

This means:
- Common endpoints (repos, issues, pulls) have both raw JSON AND formatted Markdown versions
- The server picks the custom (wrapper) version when URIs match exactly
- Raw versions may have different URIs or require explicit access

See `src/gitea_mcp_server/resources.py` for the complete list.

## Summary Checklist

- ✅ Call `mcp_list_resources()` to discover available resources
- ✅ Use `mcp_read_resource()` for all read operations when possible
- ✅ Check `mimeType` and `tags` to understand content format
- ✅ Substitute template parameters correctly (`{owner}`, `{repo}`, etc.)
- ✅ Handle `ValueError` exceptions for missing resources or bad URIs
- ✅ Use `asyncio.gather()` for parallel independent reads
- ✅ Prefer wrapper resources (Markdown) for display, raw resources (JSON) for processing
- ✅ Cache discovery results; don't call `mcp_list_resources()` repeatedly
- ✅ Use tools for state-changing operations (create, update, delete)

---

## Quick Reference

| Resource URI Pattern | MIME Type | Tags | Use For |
|----------------------|-----------|------|---------|
| `gitea://repos/{owner}/{repo}` | text/markdown | wrapper, repository | Formatted repo info |
| `gitea://repos/{owner}/{repo}/readme` | text/plain | wrapper, readme | README content |
| `gitea://repos/{owner}/{repo}/issues` | text/markdown | wrapper, issues | Formatted issues list |
| `gitea://repos/{owner}/{repo}/pulls` | text/markdown | wrapper, pull_requests | Formatted PRs |
| `gitea://repos/{owner}/{repo}/files/{path}` | text/plain | wrapper, files | File contents |
| `gitea://users/{username}` | text/markdown | wrapper, user | Formatted user profile |
| `gitea://orgs/{orgname}` | text/markdown | wrapper, organization | Formatted org profile |
| `gitea://repos/{owner}/{repo}/stats/contributors` | application/json | raw, stats | Contributor statistics (JSON) |
| `gitea://version` | text/plain | raw, version | Server version |

*Note: This table shows common resources; always check `mcp_list_resources()` for the complete list.*
