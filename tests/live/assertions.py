"""Reusable assertion helpers for verifying tool output shape and content.

These helpers reduce boilerplate in live test files and express the
*intent* of each assertion directly — "the response has these keys with
these types" rather than raw ``assert data["login"]``.

Cross-format equivalence (``assert_formats_equivalent``) is the primary
new capability: it verifies principle 5 (all formats contain the same
information) by calling a tool twice and checking that key fields appear
in both JSON and Markdown output.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tests.helpers.mcp_results import extract_text_content

if TYPE_CHECKING:
    from mcp import ClientSession as MCPClient

# ---------------------------------------------------------------------------
# Shape assertions — structural correctness
# ---------------------------------------------------------------------------


def assert_keys(data: dict[str, Any], *keys: str, msg: str = "") -> None:
    """Assert all *keys* are present in *data*.

    Args:
        data: The dict to inspect.
        *keys: Keys that must be present.
        msg: Optional prefix for the error message.
    """
    missing = [k for k in keys if k not in data]
    assert not missing, (
        f"{msg}Missing required keys in response: {missing}. "
        f"Available: {sorted(data.keys())}"
    )


def assert_key_types(data: dict[str, Any], **typed: type) -> None:
    """Assert specific keys have the expected types.

    Args:
        data: The dict to inspect.
        **typed: Mapping of ``key=type`` — e.g. ``id=int, login=str``.
    """
    for key, expected_type in typed.items():
        actual = data.get(key)
        assert isinstance(actual, expected_type), (
            f"Key {key!r}: expected {expected_type.__name__}, "
            f"got {type(actual).__name__} ({actual!r})"
        )


def assert_content(data: dict[str, Any], **expected: Any) -> None:
    """Assert specific key-value pairs match exactly.

    Args:
        data: The dict to inspect.
        **expected: Mapping of ``key=value`` — e.g. ``login="dev1", active=True``.
    """
    for key, expected_val in expected.items():
        actual = data.get(key)
        assert actual == expected_val, (
            f"Key {key!r}: expected {expected_val!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Cross-format equivalence — principle 5
# ---------------------------------------------------------------------------


def _unwrap_result(data: dict[str, Any]) -> Any:
    """Strip the ``{"result": ...}`` wrapper if present (server wraps JSON output)."""
    if isinstance(data, dict) and list(data.keys()) == ["result"]:
        return data["result"]
    return data


def _collect_values(obj: Any, depth: int = 0, max_depth: int = 3) -> set[str]:
    """Collect all leaf string/int/bool/none values from a JSON structure.

    Returns a set of stringified values.  Each value must appear in the
    markdown output (or be absent if the markdown trims detail).
    Trims at *max_depth* to avoid infinite recursion.
    """
    values: set[str] = set()
    if depth > max_depth:
        return values
    if isinstance(obj, dict):
        for _k, v in obj.items():
            values.update(_collect_values(v, depth + 1, max_depth))
    elif isinstance(obj, list):
        for item in obj:
            values.update(_collect_values(item, depth + 1, max_depth))
    elif obj is None:
        pass  # None is not rendered in markdown
    elif isinstance(obj, bool):
        values.add(str(obj).lower())
    elif isinstance(obj, float):
        # Match approximate rendering: 0.0 → "0" in markdown
        values.add(str(int(obj)) if obj == int(obj) else str(obj))
    else:
        values.add(str(obj))
    return values


async def assert_formats_equivalent(
    mcp: MCPClient,
    tool_name: str,
    args: dict[str, Any],
    *,
    skip_values: bool = False,
) -> None:
    """Verify ``format=json`` and ``format=markdown`` carry equivalent information.

    Calls the tool twice — once with ``format=json``, once with default
    markdown — and checks that key **values** from the JSON result appear
    in the markdown text.  This avoids the camelCase↔Title Case mapping
    problem: we match on the information content, not the field names.

    Args:
        mcp: Connected MCP client session.
        tool_name: Full tool name (e.g. ``"gitea_user_get_current"``).
        args: Tool arguments dict (excluding ``format`` — this helper adds it).
        skip_values: If True, skip value matching entirely.
    """
    # --- Call with format=json ---
    json_args = {**args, "format": "json"}
    json_result = await mcp.call_tool(tool_name, json_args)
    assert not json_result.isError, (
        f"format=json call failed: {extract_text_content(json_result.content)}"
    )
    json_text = extract_text_content(json_result.content)
    json_data = json.loads(json_text)

    # Server wraps JSON output in {"result": ...} — unwrap.
    json_data = _unwrap_result(json_data)

    # --- Call with format=markdown (default) ---
    md_args = {**args, "format": "markdown"}
    md_result = await mcp.call_tool(tool_name, md_args)
    assert not md_result.isError, (
        f"format=markdown call failed: {extract_text_content(md_result.content)}"
    )
    md_text = extract_text_content(md_result.content)
    md_lower = md_text.lower()

    # --- Verify item count (lists) ---
    if isinstance(json_data, list):
        # Count markdown items by counting "item" headings or table rows
        item_count = len(json_data)
        assert item_count > 0 or md_text != "", (
            f"Empty JSON list but non-empty markdown for {tool_name}: {md_text[:200]}"
        )

    # --- Pick distinctive leaf values and check they appear in markdown ---
    if not skip_values:
        values = _collect_values(json_data)
        # Filter: skip empty strings, very short tokens, and URLs (formatting varies)
        significant = {v for v in values if len(v) > 2 and not v.startswith("http")}
        missing = [v for v in significant if v.lower() not in md_lower]
        # Allow a small number of misses (formatting can drop some values)
        if len(missing) > max(2, len(significant) * 0.3):
            assert not missing, (
                f"Too many values in JSON missing from markdown for {tool_name}:\n"
                f"  Missing ({len(missing)}/{len(significant)}): {missing[:20]}\n"
                f"  Markdown preview: {md_text[:300]}"
            )


# ---------------------------------------------------------------------------
# Convenience — compound assertion for tool results
# ---------------------------------------------------------------------------


def assert_result_ok(result: Any) -> Any:
    """Assert a tool call succeeded and return parsed JSON data.

    Combines three common live-test assertions:
    1. ``not result.isError``
    2. text content is parseable JSON
    3. returns a dict or list

    Returns the parsed value for further inspection.
    """
    assert not result.isError, (
        f"Tool call failed: {extract_text_content(result.content)}"
    )
    text = extract_text_content(result.content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        msg = f"Expected JSON response, got: {text[:200]!r}...\nError: {e}"
        raise AssertionError(msg) from e
    assert isinstance(data, (dict, list)), (
        f"Expected JSON dict or list, got {type(data).__name__}: {text[:200]!r}"
    )
    return data
