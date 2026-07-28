#!/usr/bin/env python3
"""
Script to add type annotations to untyped function parameters in test files.

Uses ast + targeted text insertion. Run from project root:

    uv run python scripts/add_param_types.py

After each run, re-check with:
    mypy --no-error-summary --disallow-untyped-defs --config-file /dev/null tests/ | grep "no-untyped-def" | wc -l

Iterate: each run handles more patterns. When gain diminishes (< 50 per pass),
switch to manual.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Types that don't need an import (built-in or already available)
BUILTIN_TYPES = {"str", "int", "float", "bool", "type", "None", "list", "dict"}

# ── Parameter name → type annotation mapping ──────────────────────────────
# (annotation_str, needs_import_flag, import_source)
# needs_import_flag: "yes" | "no" (builtin) | "pytest" (available via import pytest)
PARAM_TYPE_MAP: dict[str, tuple[str, str, str | None]] = {
    # MCP / server fixtures
    "mcp_server": ("FastMCP", "yes", "fastmcp"),
    "search_mcp_server": ("FastMCP", "yes", "fastmcp"),
    "captured_app": ("FastMCP", "yes", "fastmcp"),
    # pytest built-in fixtures
    "monkeypatch": ("pytest.MonkeyPatch", "pytest", None),
    "caplog": ("pytest.LogCaptureFixture", "pytest", None),
    "tmp_path": ("Path", "yes", "pathlib"),
    "request": ("pytest.FixtureRequest", "pytest", None),
    # OpenTelemetry
    "trace_exporter": ("InMemorySpanExporter", "yes",
                       "opentelemetry.sdk.trace.export.in_memory_span_exporter"),
    # Domain objects
    "label_service": ("LabelService", "yes",
                      "gitea_mcp_server.label_service"),
    "_label_service": ("LabelService", "yes",
                       "gitea_mcp_server.label_service"),
    "transform": ("LabelTransform", "yes",
                  "gitea_mcp_server.tools.label_transform"),
    "ns": ("GiteaNamespace", "yes",
           "gitea_mcp_server.tools.namespace"),
    "api": ("GiteaAPI", "yes", "gitea_mcp_server.client"),
    "transport": ("HTTPTransport", "yes", "gitea_mcp_server.client"),
    "test_config": ("SimpleConfig", "yes", "tests.conftest"),
    "scope_filter_info": ("dict[str, Any]", "yes", "typing"),
    "exclude_filter_info": ("dict[str, Any]", "yes", "typing"),
    "deprecated_filter_info": ("dict[str, Any]", "yes", "typing"),
    # Mock instances
    "mock_gitea_client_str": ("AsyncMock", "yes", "unittest.mock"),
    "mock_gitea_client": ("AsyncMock", "yes", "unittest.mock"),
    "mock_mcp": ("MagicMock", "yes", "unittest.mock"),
    "mock_client": ("AsyncMock", "yes", "unittest.mock"),
    "gitea_client": ("AsyncMock", "yes", "unittest.mock"),
    "_gitea_client": ("AsyncMock", "yes", "unittest.mock"),
    # Spec/fixture dicts
    "base_spec": ("dict[str, Any]", "yes", "typing"),
    "valid_spec": ("dict[str, Any]", "yes", "typing"),
    "text_spec": ("dict[str, Any]", "yes", "typing"),
    "empty_body_spec": ("dict[str, Any]", "yes", "typing"),
    "minimal_spec": ("dict[str, Any]", "yes", "typing"),
    "spec_with_one_endpoint": ("dict[str, Any]", "yes", "typing"),
    "openapi_spec_with_get": ("dict[str, Any]", "yes", "typing"),
    "spec": ("dict[str, Any]", "yes", "typing"),
    "captured_resources": ("dict[str, Any]", "yes", "typing"),
    "issues_resource": ("dict[str, Any]", "yes", "typing"),
    # Primitive/scalar types
    "spec_url": ("str", "no", None),
    "name": ("str", "no", None),
    "key": ("str", "no", None),
    "exc_cls": ("type", "no", None),
    "exc_msg": ("str", "no", None),
    "detail": ("str", "no", None),
    "response_format": ("str", "no", None),
    "field": ("str", "no", None),
    "page": ("int", "no", None),
    "per_page": ("int", "no", None),
    "version": ("str", "no", None),
    # Generic fallbacks
    "value": ("Any", "yes", "typing"),
    "data": ("Any", "yes", "typing"),
    "kwargs": ("Any", "yes", "typing"),
    "parent": ("Any", "yes", "typing"),
    "schema": ("Any", "yes", "typing"),
    "setup": ("Any", "yes", "typing"),
    "init": ("Any", "yes", "typing"),
    "base": ("Any", "yes", "typing"),
    "expected": ("Any", "yes", "typing"),
    "result": ("Any", "yes", "typing"),
    "items": ("Any", "yes", "typing"),
}


def find_untyped_params(filepath: str) -> dict[int, list[str]]:
    """Return {lineno: [untyped_param_names]} for functions with untyped
    parameters, using mypy + ast."""
    result = subprocess.run(
        [
            "mypy", "--no-error-summary",
            "--disallow-untyped-defs", "--disallow-incomplete-defs",
            "--config-file", "/dev/null", filepath,
        ],
        capture_output=True, text=True, timeout=60,
    )

    line_params: dict[int, list[str]] = {}
    for line in result.stdout.split("\n"):
        if "no-untyped-def" not in line or "type annotation for" not in line:
            continue
        m = re.match(r"^(tests/\S+):(\d+):", line)
        if not m:
            continue
        lineno = int(m.group(2))

        try:
            with open(filepath) as f:
                content = f.read()
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.lineno != lineno:
                    continue
                untyped = []
                for arg in node.args.args:
                    if arg.arg in ("self", "cls"):
                        continue
                    if arg.annotation is None:
                        untyped.append(arg.arg)
                for arg in node.args.kwonlyargs:
                    if arg.annotation is None:
                        untyped.append(arg.arg)
                if untyped:
                    line_params[lineno] = untyped
        except (FileNotFoundError, SyntaxError):
            pass

    return line_params


def add_annotations_to_file(filepath: str) -> int:
    """Add type annotations to untyped params in a single file.

    Uses AST to find exact parameter positions, handling multi-line
    function signatures and complex parameter lists safely.
    Returns number of parameters annotated.
    """
    line_params = find_untyped_params(filepath)
    if not line_params:
        return 0

    with open(filepath) as f:
        content = f.read()
        lines = content.splitlines(keepends=True)

    # Parse AST to find exact parameter positions and function boundaries
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0

    # Build: lineno -> (func_end_lineno, {param_name: (line, col)})
    func_params: dict[int, dict[str, tuple[int, int]]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        untyped_params: dict[str, tuple[int, int]] = {}
        for arg in node.args.args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                untyped_params[arg.arg] = (arg.lineno, arg.col_offset)
        for arg in node.args.kwonlyargs:
            if arg.annotation is None:
                untyped_params[arg.arg] = (arg.lineno, arg.col_offset)
        if untyped_params:
            func_params[node.lineno] = untyped_params

    types_needing_import: dict[str, tuple[str, str]] = {}
    params_added = 0

    # Collect edits: (line_idx, insert_pos, text_to_insert)
    # Process in reverse to preserve positions
    edits: list[tuple[int, int, str]] = []
    imports_needed: list[tuple[str, str, str]] = []  # (ann_str, module, name)

    for lineno, param_names in sorted(line_params.items(), reverse=True):
        untyped = func_params.get(lineno)
        if untyped is None:
            continue
        for param_name in param_names:
            pos = untyped.get(param_name)
            if pos is None:
                continue
            info = PARAM_TYPE_MAP.get(param_name)
            if info is None:
                continue
            ann_str, import_flag, import_source = info
            param_line, param_col = pos

            line_idx = param_line - 1  # 0-indexed
            insert_pos = param_col + len(param_name)
            # Ensure space before default value if followed by =
            line = lines[line_idx]
            after = line[insert_pos:].lstrip()
            if after.startswith("="):
                annotation_text = f": {ann_str} "
            else:
                annotation_text = f": {ann_str}"
            edits.append((line_idx, insert_pos, annotation_text))

            if import_flag == "yes" and import_source:
                # For compound types like "dict[str, Any]", import the
                # non-builtin components (e.g., "Any" from "typing").
                if ann_str.startswith("dict[str,") or ann_str.startswith("list["):
                    # Extract types inside brackets, import those
                    inner = ann_str[ann_str.index("[")+1:ann_str.rindex("]")]
                    parts = [p.strip() for p in inner.split(",")]
                    for p in parts:
                        if p not in BUILTIN_TYPES:
                            imports_needed.append((p, import_source, p))
                else:
                    imports_needed.append((ann_str, import_source, ann_str))
            elif import_flag == "pytest":
                imports_needed.append(("pytest", "pytest", "pytest"))

    # Apply edits in reverse order (stable positions when same line)
    edits.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for line_idx, insert_pos, ann_text in edits:
        line = lines[line_idx]
        if len(line) >= insert_pos:
            lines[line_idx] = line[:insert_pos] + ann_text + line[insert_pos:]
            params_added += 1

    # Add needed imports
    if imports_needed:
        # Find existing imports using AST
        try:
            tree2 = ast.parse("".join(lines))
            existing_names: set[str] = set()
            last_import_line = 0
            for node in ast.iter_child_nodes(tree2):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            existing_names.add(alias.name)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            existing_names.add(alias.name)
                    last_import_line = max(last_import_line, node.end_lineno or 0)
        except SyntaxError:
            existing_names = set()
            last_import_line = 0

        new_import_lines: list[str] = []
        for ann_str, module, name in sorted(imports_needed):
            if name in existing_names:
                continue
            imp = f"import {module}\n" if name == module else f"from {module} import {name}\n"
            if imp not in new_import_lines:
                new_import_lines.append(imp)

        if new_import_lines:
            # Insert after the last import (ast positions are 1-indexed)
            insert_idx = last_import_line
            lines[insert_idx:insert_idx] = new_import_lines

    if params_added > 0:
        with open(filepath, "w") as f:
            f.writelines(lines)

    return params_added


def main():
    result = subprocess.run(
        [
            "mypy", "--no-error-summary",
            "--disallow-untyped-defs", "--disallow-incomplete-defs",
            "--config-file", "/dev/null", "tests/",
        ],
        capture_output=True, text=True, timeout=120,
    )

    files: dict[str, int] = {}
    for line in result.stdout.split("\n"):
        if "no-untyped-def" not in line or "type annotation for" not in line:
            continue
        m = re.match(r"^(tests/\S+?):\d+:", line)
        if m:
            files[m.group(1)] = files.get(m.group(1), 0) + 1

    if not files:
        print("No untyped parameter errors found — all done!")
        return

    total_initial = sum(files.values())
    print(f"Found {total_initial} untyped parameters across {len(files)} files.")

    total_added = 0
    for filepath, count in sorted(files.items(), key=lambda x: x[1]):
        if not Path(filepath).exists():
            print(f"  SKIP {filepath}")
            continue
        print(f"  {filepath} ({count} params)...", end=" ", flush=True)
        try:
            added = add_annotations_to_file(filepath)
            total_added += added
            if added > 0:
                print(f"annotated {added}")
            else:
                print("no known patterns found")
        except Exception as e:
            print(f"ERROR: {e}")

    # Recheck
    print(f"\nAnnotated {total_added} parameters in this pass.")
    result2 = subprocess.run(
        [
            "mypy", "--no-error-summary",
            "--disallow-untyped-defs", "--disallow-incomplete-defs",
            "--config-file", "/dev/null", "tests/",
        ],
        capture_output=True, text=True, timeout=120,
    )
    remaining = 0
    for line in result2.stdout.split("\n"):
        if "no-untyped-def" in line:
            remaining += 1
    print(f"Remaining no-untyped-def errors: {remaining}")
    print(f"Reduction: {total_initial} → {remaining}")
    print(f"Fixed this pass: {total_initial - remaining}")
    print(f"\nRemaining files:")
    for line in result2.stdout.split("\n"):
        if "no-untyped-def" in line:
            m = re.match(r"^(tests/\S+?):", line)
            if m:
                file = m.group(1)
                print(f"  {file}")


if __name__ == "__main__":
    main()
