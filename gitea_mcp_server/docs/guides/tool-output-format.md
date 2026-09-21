---
title: Tool Output Format
description: Which format to ask for, how to read compact results, paging, and what errors look like.
tags: [output, format, detail, concise, pagination, errors, ref]
source: gitea-mcp-server agent guide
---

# Tool Output Format

Every tool and resource hands you back data. This guide shows how to read it
well: which format to ask for, how to keep results small, how paging works, and
what errors look like.

## Reading vs extracting

`format` picks the shape of the result. The default is `markdown`.

- `markdown` — read. It shows the key fields of each item, not every field, and
  collection views are more compact than single-item views.
- `json` — extract. It gives you the complete API data; prefer it when you need
  specific fields or will feed a result to code.
- `raw` — the API payload as JSON text.

Start with the default; reach for `json` when you want to pull specific fields,
and `raw` when you need the payload exactly as the API sent it. For file
resources such as a README, `read_resource` returns the decoded text directly.

## Keeping results small

`detail` controls how much nesting is expanded:

| detail | Result |
|--------|--------|
| `full` (default) | Everything is expanded. |
| `concise` | Each item keeps its scalar fields (title, state, dates, body, ...) but nested objects -- user, milestone, repository, ... -- are replaced by a marker. |

A marker looks like `{"$ref": "TypeName"}` in `json` and `$ref:TypeName` in
`markdown`. A collapsed list is `{"$ref": "TypeName", "count": N}`
(`$ref:TypeName[N]`). The marker stands in for data, so if you need those fields
read the result at `detail="full"`, or resolve the type with `resolve_type` /
`gitea://types/TypeName`.

Use `detail="concise"` when you are scanning many items and only need the gist.

## Paging through lists

List and search tools take `page` (starting at 1) and `limit`. Read a page,
then ask for the next until a page comes back empty -- a short page is not
automatically the last one.

On the search and discovery tools, `fetch_all=true` returns every match in one
go. That is convenient for small result sets; for large ones, page instead.

In `json` and `raw`, paginated results tell you where you are: `has_more`,
`next_offset`, and `total_count`. Reading a page past the end is not an error
-- you get an empty page with a message saying so. (`read_doc` pages a guide in
line chunks the same way.)

## Reading errors

- **An empty list is not a failure.** No matches returns an empty result. It
  does not mean the tool was hidden.
- **`APINotFound` is ambiguous.** It can mean the thing does not exist, the id
  is wrong, or it is outside your token's visibility. If
  `user_current_list_repos` lists the repo, the id is wrong; if it does not, it
  is a visibility problem.
- **Bad labels tell you the options.** Creating an issue or PR with an unknown
  label returns the unknown names and the repository's available labels. Prefer
  label IDs and check `issue_list_labels` first.
- **Unknown tool** -- check the name with `search_tools`.
- **"Only administrators allowed to sudo"** -- your token does not have the
  `sudo` scope, so the `sudo` parameter is hidden.
- **Need a full schema** -- `tool_info(name, detail="full")`, or read
  `gitea://tool/{name}/schema`.
