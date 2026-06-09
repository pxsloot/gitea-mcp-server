#!/usr/bin/env python3
"""Audit our OpenAPI v3 converter against the official swagger.io converter.

Usage:
    uv run python scripts/compare_converter.py

This script:
  1. Loads the Swagger 2.0 spec from a Gitea instance (or uses local fixture)
  2. Converts it with our pipeline (two variants: full and "core" without
     FastMCP-specific transformations)
  3. Fetches the official conversion from converter.swagger.io
  4. Produces a structural diff report
  5. Saves all spec variants to scripts/output/ for manual inspection
"""

import json
import logging
import sys
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any

from gitea_mcp_server.openapi_converter import (
    _add_nullable_for_optional_refs_impl,
    _wrap_success_response_schemas,
    convert_swagger_to_openapi_v3,
)
from gitea_mcp_server.server_setup.spec_loader import load_openapi_spec

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# The swagger.io converter endpoint
SWAGGER_CONVERTER_URL = "https://converter.swagger.io/api/convert"


def load_local_swagger() -> dict[str, Any]:
    path = Path("swagger.v1.json")
    if not path.exists():
        path = Path("tests/swagger.v1.json")
    with path.open() as f:
        return json.load(f)


async def load_remote_swagger() -> dict[str, Any] | None:
    try:
        from gitea_mcp_server.client import GiteaClient
        from gitea_mcp_server.config import Config

        config = Config.get()
        client = GiteaClient(config)
        spec = await load_openapi_spec(client, config)
        logger.info("Loaded remote spec: %d paths", len(spec.get("paths", {})))
        return spec
    except Exception as e:
        logger.warning("Could not load remote spec: %s", e)
        return None


def convert_core(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert WITHOUT FastMCP-specific transformations.

    This produces a spec more comparable to the official converter output:
    - No ``_wrap_success_response_schemas``
    - No ``_add_nullable_for_optional_refs_impl``
    - Everything else is the same
    """
    from gitea_mcp_server.exceptions import SpecError

    if not isinstance(spec, dict):
        msg = "Invalid spec: must be a dictionary"
        raise SpecError(msg)
    if spec.get("swagger") != "2.0":
        msg = f"Expected Swagger 2.0, got {spec.get('swagger')}"
        raise SpecError(msg)

    s = deepcopy(spec)
    s = convert_swagger_to_openapi_v3(s)
    # Undo the FastMCP-specific transformations by re-running the spec
    # through a fresh conversion with those steps skipped.
    # Instead, we do a fresh conversion minus those two steps:
    s2 = deepcopy(spec)
    _convert_without_wrapping(s2)
    return s2


def _convert_without_wrapping(spec: dict[str, Any]) -> dict[str, Any]:
    """Run the conversion pipeline but skip wrapping and nullable."""
    from gitea_mcp_server.openapi_converter import (
        BasePathToServerConverter,
        ReferenceFixer,
        SpecVersionUpdater,
        _convert_components,
        _update_info_version,
        convert_paths,
    )

    SpecVersionUpdater().update(spec)
    _update_info_version(spec)
    BasePathToServerConverter().convert(spec)

    components = _convert_components(spec)
    if components:
        spec["components"] = components

    if "paths" in spec:
        spec["paths"] = convert_paths(spec["paths"])

    spec.pop("consumes", None)
    spec.pop("produces", None)
    spec.pop("schemes", None)

    spec = ReferenceFixer().fix(spec)
    return spec


def fetch_official_conversion(spec_url: str | None = None, spec_data: dict | None = None) -> dict[str, Any] | None:
    """Fetch the official conversion from converter.swagger.io.

    Can either pass a URL (publicly accessible) or POST the raw spec data.
    """
    if spec_data:
        # POST the spec directly
        data = json.dumps(spec_data).encode("utf-8")
        req = urllib.request.Request(
            SWAGGER_CONVERTER_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    elif spec_url:
        req = urllib.request.Request(f"{SWAGGER_CONVERTER_URL}?url={urllib.parse.quote(spec_url)}")
    else:
        return None

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        logger.info("Fetched official conversion: %s version",
                     result.get("openapi", result.get("swagger", "unknown")))
        return result
    except Exception as e:
        logger.warning("Could not fetch official conversion: %s", e)
        return None


def normalize_for_comparison(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a spec for side-by-side comparison.

    Strips metadata that will always differ between converters:
    - openapi version (3.0 vs 3.1)
    - info details
    - server URLs
    - x-* extensions (unless they carry semantic meaning)
    """
    s = deepcopy(spec)
    s.pop("openapi", None)
    s.pop("swagger", None)
    s.pop("info", None)
    s.pop("servers", None)

    # Strip all x-* extensions from the whole spec
    _strip_extensions(s)

    return s


def _strip_extensions(obj: Any) -> None:
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key.startswith("x-"):
                del obj[key]
            else:
                _strip_extensions(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_extensions(item)


def compare_paths(our_spec: dict, official_spec: dict) -> dict:
    """Compare path-level structure between two specs."""
    our_paths = our_spec.get("paths", {})
    official_paths = official_spec.get("paths", {})

    our_set = set(our_paths.keys())
    official_set = set(official_paths.keys())

    missing_in_our = official_set - our_set
    extra_in_our = our_set - official_set
    common = our_set & official_set

    method_diffs = []
    for path in sorted(common):
        our_item = our_paths[path]
        off_item = official_paths[path]
        our_methods = set(k for k in our_item if k in ("get", "post", "put", "delete", "patch", "options", "head"))
        off_methods = set(k for k in off_item if k in ("get", "post", "put", "delete", "patch", "options", "head"))
        if our_methods != off_methods:
            method_diffs.append({
                "path": path,
                "our_methods": sorted(our_methods),
                "official_methods": sorted(off_methods),
            })

    return {
        "total_our": len(our_paths),
        "total_official": len(official_paths),
        "common": len(common),
        "missing_in_our": sorted(missing_in_our),
        "extra_in_our": sorted(extra_in_our),
        "method_diffs": method_diffs,
    }


def compare_definitions(our_spec: dict, official_spec: dict) -> dict:
    """Compare schema definitions between two specs."""
    our_schemas = our_spec.get("components", {}).get("schemas", {})
    official_schemas = official_spec.get("components", {}).get("schemas", {})

    if not our_schemas:
        our_schemas = our_spec.get("definitions", {})
    if not official_schemas:
        official_schemas = official_spec.get("definitions", {})

    our_set = set(our_schemas.keys())
    official_set = set(official_schemas.keys())

    return {
        "our_total": len(our_schemas),
        "official_total": len(official_schemas),
        "missing_in_our": sorted(official_set - our_set),
        "extra_in_our": sorted(our_set - official_set),
        "common": len(our_set & official_set),
    }


def compare_content_types(our_spec: dict, official_spec: dict) -> list[dict]:
    """Compare response content types for common paths/methods.

    Finds endpoints where content types differ (e.g. we say text/plain
    but official says application/json, or vice versa).
    """
    diffs = []
    our_paths = our_spec.get("paths", {})
    official_paths = official_spec.get("paths", {})

    for path in sorted(set(our_paths) & set(official_paths)):
        our_item = our_paths[path]
        off_item = official_paths[path]
        for method in ("get", "post", "put", "patch", "delete"):
            our_op = our_item.get(method)
            off_op = off_item.get(method)
            if not our_op or not off_op:
                continue
            our_resp = our_op.get("responses", {}).get("200", {})
            off_resp = off_op.get("responses", {}).get("200", {})
            our_cts = set(our_resp.get("content", {}).keys()) if isinstance(our_resp, dict) else set()
            off_cts = set(off_resp.get("content", {}).keys()) if isinstance(off_resp, dict) else set()
            if our_cts != off_cts:
                diffs.append({
                    "path": path,
                    "method": method,
                    "our_content_types": sorted(our_cts),
                    "official_content_types": sorted(off_cts),
                })
    return diffs


def summarize_schema_diffs(our_spec: dict, official_spec: dict, limit: int = 10) -> list[dict]:
    """Deep-compare a sample of common schemas to find structural differences.

    Only inspects a limited number of schemas to avoid verbosity.
    """
    our_schemas = our_spec.get("components", {}).get("schemas", {})
    off_schemas = official_spec.get("components", {}).get("schemas", {})
    if not our_schemas or not off_schemas:
        return []

    common = sorted(set(our_schemas) & set(off_schemas))[:limit]
    diffs = []
    for name in common:
        our = our_schemas[name]
        off = off_schemas[name]
        # Compare top-level type and property keys
        our_props = set((our.get("properties") or {}).keys()) if isinstance(our, dict) else set()
        off_props = set((off.get("properties") or {}).keys()) if isinstance(off, dict) else set()
        if our_props != off_props:
            diffs.append({
                "schema": name,
                "our_props": sorted(our_props),
                "official_props": sorted(off_props),
                "missing_in_our": sorted(off_props - our_props),
                "extra_in_our": sorted(our_props - off_props),
            })
    return diffs


def print_report(results: dict) -> None:
    """Print a formatted comparison report."""
    print("=" * 70)
    print("  OPENAPI CONVERTER AUDIT REPORT")
    print("=" * 70)

    print(f"\nSource spec: {results.get('source', 'unknown')}")
    print(f"  Paths: {results['stats']['paths']}")
    print(f"  Definitions: {results['stats']['definitions']}")

    print(f"\n--- Path Comparison ---")
    pc = results["paths"]
    print(f"  Our total paths:     {pc['total_our']}")
    print(f"  Official total paths: {pc['total_official']}")
    print(f"  Common paths:         {pc['common']}")
    if pc["missing_in_our"]:
        print(f"  ⚠ Missing in our output: {len(pc['missing_in_our'])}")
        for p in pc["missing_in_our"][:10]:
            print(f"    - {p}")
        if len(pc["missing_in_our"]) > 10:
            print(f"    ... and {len(pc['missing_in_our']) - 10} more")
    if pc["extra_in_our"]:
        print(f"  (+) Extra in our output: {len(pc['extra_in_our'])}")
        for p in pc["extra_in_our"][:10]:
            print(f"    + {p}")
        if len(pc["extra_in_our"]) > 10:
            print(f"    ... and {len(pc['extra_in_our']) - 10} more")
    if pc["method_diffs"]:
        print(f"  ⚠ Method diffs: {len(pc['method_diffs'])}")
        for d in pc["method_diffs"][:10]:
            print(f"    {d['path']}: our={d['our_methods']} official={d['official_methods']}")

    print(f"\n--- Definition Comparison ---")
    dc = results["definitions"]
    print(f"  Our definitions:      {dc['our_total']}")
    print(f"  Official definitions:  {dc['official_total']}")
    print(f"  Common:                {dc['common']}")
    if dc["missing_in_our"]:
        print(f"  ⚠ Missing in our: {len(dc['missing_in_our'])}")
        for d in dc["missing_in_our"][:10]:
            print(f"    - {d}")
    if dc["extra_in_our"]:
        print(f"  (+) Extra in our: {len(dc['extra_in_our'])}")
        for d in dc["extra_in_our"][:10]:
            print(f"    + {d}")

    print(f"\n--- Content Type Diffs ---")
    ctd = results["content_type_diffs"]
    if ctd:
        print(f"  ⚠ {len(ctd)} endpoints have different content types:")
        for d in ctd[:15]:
            print(f"    {d['method']} {d['path']}")
            print(f"      our:      {d['our_content_types']}")
            print(f"      official: {d['official_content_types']}")
        if len(ctd) > 15:
            print(f"    ... and {len(ctd) - 15} more")
    else:
        print("  ✅ All match")

    print(f"\n--- Schema Structure Diffs (sample) ---")
    sds = results["schema_diffs"]
    if sds:
        print(f"  ⚠ {len(sds)} schemas have property diffs:")
        for d in sds[:10]:
            print(f"    {d['schema']}:")
            if d["missing_in_our"]:
                print(f"      our missing: {d['missing_in_our']}")
            if d["extra_in_our"]:
                print(f"      our extra:   {d['extra_in_our']}")
    else:
        print("  ✅ All match (in sampled schemas)")

    print(f"\n--- Official Converter Status ---")
    off_status = results.get("official_status", "not attempted")
    print(f"  {off_status}")

    print(f"\n--- Files Saved ---")
    for path in results.get("files_saved", []):
        print(f"  {path}")

    print("\n" + "=" * 70)
    if results.get("has_diffs"):
        print("  ⚠ DETECTED DIFFERENCES — review the files above for details.")
    else:
        print("  ✅ No structural differences found.")
    print("=" * 70)


async def main() -> None:
    import time

    logger.info("=== OpenAPI Converter Audit ===")

    # 1. Load the spec
    spec = load_local_swagger()
    source = "local swagger.v1.json"
    logger.info("Loaded local spec: %s", source)

    stats = {
        "paths": len(spec.get("paths", {})),
        "definitions": len(spec.get("definitions", {})),
    }
    logger.info("Stats: %d paths, %d definitions", stats["paths"], stats["definitions"])

    # 2. Convert with full pipeline
    logger.info("Converting with full pipeline...")
    our_full = convert_swagger_to_openapi_v3(deepcopy(spec))
    _save_spec(our_full, "our_full.json")

    # 3. Convert with core pipeline (no wrapping, no nullable)
    logger.info("Converting with core pipeline (no wrapping/nullable)...")
    our_core = _convert_without_wrapping(deepcopy(spec))
    _save_spec(our_core, "our_core.json")

    # 4. Try fetching official conversion
    logger.info("Fetching official conversion from converter.swagger.io...")
    official = fetch_official_conversion(spec_data=spec)
    if official:
        off_status = "✅ Fetched successfully"
        _save_spec(official, "official.json")
    else:
        off_status = "⚠ Could not fetch (see diagnostics below)"
        logger.warning(
            "Official converter not available. Try:\n"
            "  1. Make sure swagger.io is reachable from your network\n"
            "  2. Use a local snapshot:\n"
            "     curl -X POST https://converter.swagger.io/api/convert \\\n"
            "       -H 'Content-Type: application/json' \\\n"
            "       -d @scripts/output/our_core.json \\\n"
            "       -o scripts/output/official.json\n"
            "  3. Re-run this script after placing official.json in scripts/output/"
        )

    # 5. Run comparisons if we have both specs
    files_saved = [
        str(OUTPUT_DIR / "our_full.json"),
        str(OUTPUT_DIR / "our_core.json"),
    ]
    result: dict[str, Any] = {
        "source": source,
        "stats": stats,
        "official_status": off_status,
        "files_saved": files_saved,
        "has_diffs": False,
    }

    if official:
        official_normalized = normalize_for_comparison(official)
        our_full_normalized = normalize_for_comparison(our_core)

        _save_spec(official_normalized, "official_normalized.json")
        _save_spec(our_full_normalized, "our_core_normalized.json")
        files_saved += [
            str(OUTPUT_DIR / "official_normalized.json"),
            str(OUTPUT_DIR / "our_core_normalized.json"),
        ]

        result["paths"] = compare_paths(our_full_normalized, official_normalized)
        result["definitions"] = compare_definitions(our_full_normalized, official_normalized)
        result["content_type_diffs"] = compare_content_types(our_full_normalized, official_normalized)
        result["schema_diffs"] = summarize_schema_diffs(our_full_normalized, official_normalized, limit=20)

        has_diffs = bool(
            result["paths"].get("missing_in_our")
            or result["paths"].get("extra_in_our")
            or result["paths"].get("method_diffs")
            or result["definitions"].get("missing_in_our")
            or result["definitions"].get("extra_in_our")
            or result["content_type_diffs"]
            or result["schema_diffs"]
        )
        result["has_diffs"] = has_diffs

    result["files_saved"] = files_saved
    print_report(result)


def _save_spec(spec: dict[str, Any], filename: str) -> Path:
    path = OUTPUT_DIR / filename
    with path.open("w") as f:
        json.dump(spec, f, indent=2)
    logger.info("Saved: %s", path)
    return path


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
