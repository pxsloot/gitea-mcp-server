---
title: Output Format
description: How tool and resource output is shaped -- formats, detail, envelopes, $ref markers, errors.
tags: [output, format, detail, concise, pagination, errors, ref]
source: gitea-mcp-server agent-facing contract
---

# Output Format

Every tool and resource returns its result through the same display pipeline.
This guide is the reference for what those results look like: the `format` and
`detail` options, the pagination envelope, `$ref` markers, and the error shapes
you will meet. The server instructions carry the essentials; come here when you
need the exact shape.

## The three formats

`format` selects the channel. `markdown` is the default.

| Format | What you get |
|--------|--------------|
| `markdown` | A curated, schema-aware rendering. Best for reading. |
| `json` | The complete API data, as a serialized `{"result": ...}` envelope. Best for programmatic extraction. |
| `raw` | The same complete data in the deterministic `{"result": ...}` envelope -- valid JSON text, never a Python `repr`. |

`read_resource` accepts `format` too. When it reads a Gitea `ContentsResponse`,
the base64 content is always decoded before you see it, so `raw`/`json` show the
decoded text, not the base64 blob.

## Markdown vs json

For a tool bound to a response type, `markdown` is a **curated projection**, not
a field-for-field copy:

- Scalars are complete.
- `$ref`-backed relations (user, milestone, repository, ...) are compacted to an
  identity or a `$ref` marker.
- Collection views omit noise and per-item detail; a single-resource (detail)
  read renders the full payload.

`format=json` and `format=raw` are always the **complete API data**. If you need
every field, use `json`; use `markdown` when you want to read.

## detail: full vs concise

`detail` applies to `json` and `markdown` (`raw` is always full).

| detail | Effect |
|--------|--------|
| `full` (default) | Complete object expansion. |
| `concise` | Root objects and root-list items are summarized: scalar fields (title, state, dates, body, ...) stay intact while nested `$ref`-backed fields collapse to a marker. |

Nested `$ref` fields become `{"$ref": "TypeName"}`; a collapsed list becomes
`{"$ref": "TypeName", "count": N}`. In `markdown` these render as
`$ref:TypeName` and `$ref:TypeName[N]`. The marker replaces data -- read the real
fields at `detail="full"`. Scalar-result tools show a bare primitive instead
(`true`, `0`, `"example"`).

To inspect a type behind a marker, call `resolve_type` with the marker's `$ref`
value, or read `gitea://types/{TypeName}`. `resolve_type` also lists which tools
return or accept the type.

## Content is the contract

The text channel (`content`) is authoritative and always present;
`structured_content` mirrors it. For `json` and `raw`, the text is the
serialized envelope dict -- `{"result": ...}` -- so the two channels never
disagree.

Paginated tools add the envelope **in the text** alongside `result`:

```json
{"result": [], "message": "...", "has_more": false, "next_offset": null, "total_count": 0}
```

An empty or out-of-range page keeps that shape (empty `result`, `has_more`
false, `next_offset` null, `total_count` as known) -- it is not an error.

## Pagination

List and search tools take `page` (1-based) and `limit`. Without `fetch_all`,
loop `page` upward until you get an empty page -- a short page is not
necessarily the last one. On synthetic search/list tools, `fetch_all=true`
returns every match in one response (in-memory skip-slice, no HTTP loop).

`read_doc` also pages with `page` and `limit` (line-based). For `format=json`
its envelope travels in the text. An out-of-range page keeps the object-shaped
`result` (empty `content`) and adds a `message` such as
`"Page N is out of range (total results: M)"`.

## Errors and edge cases

- **Empty list is `[]`, not an error.** A list/search that matches nothing
  returns an empty result (in `json`, the zero-envelope above). It means *no
  matches* -- not a hidden tool.
- **`APINotFound` is ambiguous.** The same error fires for a non-existent repo,
  a wrong `index`, or a repo your token cannot see. To disambiguate: if
  `user_current_list_repos` lists the repo, the 404 is a bad id; if it is not
  listed, it is scope/visibility.
- **Unknown label names fail loudly.** Creating an issue or PR with a label that
  does not exist returns the unknown names *and* the repository's available
  labels, with a pointer to `issue_list_labels` or
  `gitea://repos/{owner}/{repo}/labels`. Prefer integer label IDs and confirm
  labels first.
- **Unknown tool** -- the name is wrong; use `search_tools` to find it.
- **No search results** -- simplify to one keyword, or the tool is
  scope-filtered out.
- **`"Only administrators allowed to sudo"`** -- your token lacks the
  `sudo`/`all` scope; the `sudo` parameter is correctly hidden.
- **Need a full schema** -- `tool_info(name, detail="full")`, or read
  `gitea://tool/{name}/schema`.
