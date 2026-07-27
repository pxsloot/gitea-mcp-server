"""Property-based tests for the OpenAPI converter using hypothesis.

Tests invariants of ``convert_swagger_to_openapi_v3`` that are difficult
to cover exhaustively with example-based tests alone.

Invariants tested (from #560):
  1. No $ref is ever left unresolved in the output
  2. No x-* vendor extensions leak into output schemas
  3. All success response schemas are wrapped in {"result": ...}
  4. Non-JSON responses are never wrapped
  5. Round-trip completeness — every input path survives with operations
  6. Parameter conversion preserves in, name, schema fields
  7. No crash on edge cases (null values, missing fields, malformed input)
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from gitea_mcp_server.openapi_converter import convert_swagger_to_openapi_v3

# NOTE: None of these tests use ``@settings`` yet. Default hypothesis settings
# (100 examples per @given, no deadline) are fine for the current size.  As
# the property-test suite grows, add ``@settings(max_examples=..., deadline=...)``
# to individual test classes to tune profile vs. CI time — see the
# ``@pytest.mark.slow`` guideline in TESTING_STANDARDS.md for the threshold.

# ===========================================================================
# Reactive helpers — walk converted specs for assertions
# ===========================================================================


def _walk_schemas(obj: Any) -> list[dict[str, Any]]:
    """Yield every schema-like dict in a spec tree.

    A "schema-like dict" is any dict that contains a ``type`` key, an
    ``items`` key, a ``properties`` key, or a ``$ref`` key.  This is
    intentionally broad so we catch both inline schemas and response
    wrapper objects.
    """
    found: list[dict[str, Any]] = []
    _walk(obj, found)
    return found


def _walk(obj: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        # Heuristic: a dict that looks like a schema
        if any(k in obj for k in ("type", "items", "properties", "$ref")):
            found.append(obj)
        for v in obj.values():
            _walk(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, found)


def _collect_refs(obj: Any) -> list[str]:
    """Collect all ``$ref`` string values in a spec tree."""
    refs: list[str] = []
    if isinstance(obj, dict):
        if "$ref" in obj and isinstance(obj["$ref"], str):
            refs.append(obj["$ref"])
        for v in obj.values():
            refs.extend(_collect_refs(v))
    elif isinstance(obj, list):
        for item in obj:
            refs.extend(_collect_refs(item))
    return refs


def _collect_x_keys(obj: Any) -> list[str]:
    """Collect all vendor extension keys (``x-*``) in the tree."""
    keys: list[str] = []
    if isinstance(obj, dict):
        for k in obj:
            if isinstance(k, str) and k.startswith("x-"):
                keys.append(k)
        for v in obj.values():
            keys.extend(_collect_x_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.extend(_collect_x_keys(item))
    return keys


def _has_result_wrapper(schema: dict[str, Any]) -> bool:
    """Return True if *schema* looks like a ``{"result": ...}`` wrapper."""
    return (
        schema.get("type") == "object"
        and isinstance(schema.get("properties"), dict)
        and "result" in schema["properties"]
    )


# ===========================================================================
# Hypothesis strategies
# ===========================================================================

# A few well-known Swagger types to draw from
_SCHEMA_TYPES = st.sampled_from(["string", "integer", "number", "boolean"])

# Non-JSON content-type combinations for wrapping tests
_NON_JSON_TYPES = st.sampled_from([
    ["text/plain"],
    ["text/html"],
    ["application/octet-stream"],
    ["text/plain", "application/json"],  # multiple — non-JSON priority
])

# Build a simple leaf schema (no nesting).
_leaf_schema = st.builds(
    lambda t: {"type": t},
    t=_SCHEMA_TYPES,
)


@st.composite
def swagger_schema(draw: st.DrawFn, max_depth: int = 3) -> dict[str, Any]:
    """Generate a random Swagger 2.0 schema (may be nested).

    The produced dict looks like a Swagger ``schema`` object — it can
    contain ``type``, ``properties``, ``items``, ``$ref``, and vendor
    extension keys.
    """
    depth = draw(st.integers(min_value=0, max_value=max_depth))

    if depth == 0:
        return draw(_leaf_schema)

    kind = draw(st.sampled_from(["object", "array", "primitive"]))

    if kind == "primitive":
        return draw(_leaf_schema)

    if kind == "array":
        return {
            "type": "array",
            "items": draw(swagger_schema(max_depth=depth - 1)),
        }

    # kind == "object"
    n_props = draw(st.integers(min_value=0, max_value=3))
    properties: dict[str, Any] = {}
    for _ in range(n_props):
        name = draw(st.text(min_size=1, max_size=6, alphabet=st.characters(
            whitelist_categories=["Ll", "Lu", "Nd"],
        )))
        properties[name] = draw(swagger_schema(max_depth=depth - 1))
    return {"type": "object", "properties": properties} if properties else {"type": "object"}


@st.composite
def swagger_schema_with_x(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a schema that may carry ``x-*`` vendor extension keys."""
    schema = draw(swagger_schema(max_depth=2))
    # Sprinkle x-* keys into the result — one or two at various levels.
    n_x = draw(st.integers(min_value=0, max_value=2))
    for _ in range(n_x):
        name = draw(st.text(min_size=3, max_size=12, alphabet=st.characters(
            whitelist_categories=["Ll", "Lu", "Nd", "Pd"],
        )))
        schema[f"x-{name}"] = draw(st.text(max_size=10))
    return schema


@st.composite
def swagger_schema_with_nested_refs(
    draw: st.DrawFn,
    definition_names: list[str],
    max_depth: int = 2,
) -> dict[str, Any]:
    """Generate a schema that may emit ``$ref`` at any nesting depth.

    Unlike the existing top-level-only $ref test, this strategy embeds
    ``$ref`` values inside object properties, array items, and at the
    root level — exercising ReferenceFixer across the full schema tree.
    """
    # At any recursion level, may produce a $ref instead of an inline schema
    if definition_names and draw(st.booleans()):
        target = draw(st.sampled_from(definition_names))
        return {"$ref": f"#/definitions/{target}"}

    depth = draw(st.integers(min_value=0, max_value=max_depth))
    if depth == 0:
        return draw(_leaf_schema)

    kind = draw(st.sampled_from(["primitive", "array", "object"]))
    if kind == "primitive":
        return draw(_leaf_schema)
    if kind == "array":
        return {
            "type": "array",
            "items": draw(swagger_schema_with_nested_refs(
                definition_names, max_depth=depth - 1,
            )),
        }
    # kind == "object"
    n_props = draw(st.integers(min_value=0, max_value=3))
    properties: dict[str, Any] = {}
    for _ in range(n_props):
        name = draw(st.text(min_size=1, max_size=6, alphabet=st.characters(
            whitelist_categories=["Ll", "Lu", "Nd"],
        )))
        properties[name] = draw(swagger_schema_with_nested_refs(
            definition_names, max_depth=depth - 1,
        ))
    return {"type": "object", "properties": properties} if properties else {"type": "object"}


# ===========================================================================
# Build a minimal Swagger 2.0 spec from paths + optional definitions
# ===========================================================================


def _make_spec(
    paths: dict[str, Any] | None = None,
    definitions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a minimal valid Swagger 2.0 spec with the given paths and definitions."""
    spec: dict[str, Any] = {
        "swagger": "2.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "basePath": "/api/v1",
    }
    if paths is not None:
        spec["paths"] = paths
    if definitions is not None:
        spec["definitions"] = definitions
    return spec


# ===========================================================================
# Invariant 1: No unresolved $ref in the output
# ===========================================================================


class TestNoUnresolvedRefs:
    """Every ``$ref`` in the converted spec must point to an existing target."""

    @given(st.lists(st.text(min_size=1, max_size=10, alphabet=st.characters(
        whitelist_categories=["Ll", "Lu", "Nd"],
    )), min_size=0, max_size=5, unique=True))
    def test_refs_point_to_existing_definitions(self, def_names: list[str]) -> None:
        """When definitions exist, all $ref values must resolve."""
        assume(len(def_names) >= 1)

        definitions: dict[str, Any] = {}
        paths: dict[str, Any] = {}
        for name in def_names:
            definitions[name] = {"type": "object", "properties": {"id": {"type": "integer"}}}

        # Create one endpoint referencing each definition
        for i, name in enumerate(def_names):
            paths[f"/resource_{i}"] = {
                "get": {
                    "operationId": f"getResource{i}",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"$ref": f"#/definitions/{name}"},
                        },
                    },
                },
            }

        spec = _make_spec(paths=paths, definitions=definitions)
        result = convert_swagger_to_openapi_v3(spec)
        refs = _collect_refs(result)

        # Every $ref must point to a target that exists
        for ref in refs:
            # Resolve the ref path
            parts = ref.lstrip("#/").split("/")
            target: Any = result
            try:
                for part in parts:
                    target = target[part]
            except (KeyError, TypeError):
                pytest.fail(f"Unresolved $ref: {ref}")

    @given(
        def_names=st.lists(st.text(min_size=1, max_size=10, alphabet=st.characters(
            whitelist_categories=["Ll", "Lu", "Nd"],
        )), min_size=1, max_size=5, unique=True),
        data=st.data(),
    )
    def test_nested_refs_resolve_across_schema_tree(
        self, def_names: list[str], data: st.DataObject,
    ) -> None:
        """``$ref`` inside object properties and array items must resolve.

        The existing ``test_refs_point_to_existing_definitions`` only places
        ``$ref`` at the response root.  This test embeds ``$ref`` at arbitrary
        depth inside the schema tree, exercising ``ReferenceFixer`` and
        ``convert_schema`` on nested structures.
        """
        definitions: dict[str, Any] = {n: {"type": "object"} for n in def_names}
        schema = data.draw(swagger_schema_with_nested_refs(
            def_names, max_depth=2,
        ))

        spec = _make_spec(paths={
            "/r": {"get": {
                "operationId": "getR",
                "responses": {"200": {"description": "OK", "schema": schema}},
            }},
        }, definitions=definitions)
        result = convert_swagger_to_openapi_v3(spec)
        refs = _collect_refs(result)

        for ref in refs:
            parts = ref.lstrip("#/").split("/")
            target: Any = result
            try:
                for part in parts:
                    target = target[part]
            except (KeyError, TypeError):
                pytest.fail(
                    f"Nested $ref not resolved: {ref} "
                    f"(defs={def_names}, schema={schema})"
                )


# ===========================================================================
# Invariant 2: No x-* vendor extensions in schema objects
# ===========================================================================


class TestNoVendorExtensionsInSchemas:
    """Vendor extensions (``x-*``) must be stripped from schemas during conversion."""

    @given(st.dictionaries(
        keys=st.text(min_size=3, max_size=15, alphabet=st.characters(
            whitelist_categories=["Ll", "Lu", "Nd", "Pd"],
        )).map(lambda k: f"x-{k}" if not k.startswith("x-") else k),
        values=st.text(max_size=20),
        min_size=1,
        max_size=3,
    ))
    def test_x_keys_stripped_from_top_level_schema(self, x_fields: dict[str, str]) -> None:
        """``x-*`` keys placed on a response schema must be removed after conversion."""
        schema: dict[str, Any] = {"type": "object", "properties": {"id": {"type": "integer"}}}
        schema.update(x_fields)

        spec = _make_spec(paths={
            "/item": {
                "get": {
                    "operationId": "getItem",
                    "responses": {"200": {"description": "OK", "schema": schema}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)

        # Walk all schemas in the output — none should have x-* keys
        all_schemas = _walk_schemas(result)
        for s in all_schemas:
            x_keys = [k for k in s if isinstance(k, str) and k.startswith("x-")]
            assert not x_keys, (
                f"Found x-* keys in output schema: {x_keys} "
                f"(expected stripped by convert_schema)"
            )

    def test_real_spec_x_go_fields_stripped(self) -> None:
        """Regression: the real swagger.v1.json should have no x-go-* leaks."""
        import json
        from pathlib import Path

        spec_path = Path(__file__).parent.parent.parent / "swagger.v1.json"
        if not spec_path.exists():
            return  # skip if test fixture not present

        with spec_path.open() as f:
            spec = json.load(f)

        result = convert_swagger_to_openapi_v3(spec)
        x_keys = _collect_x_keys(result)

        # Operation-level x-* (x-original-content-types, x-mcp) should survive
        allowed_prefixes = ("x-original-content-types", "x-mcp", "x-fastmcp-")
        schema_x_keys = [
            k for k in x_keys
            if not any(k.startswith(p) for p in allowed_prefixes)
        ]
        assert not schema_x_keys, (
            f"Found unexpected x-* keys in converted spec: {schema_x_keys}"
        )


# ===========================================================================
# Invariant 3: JSON success responses wrapped in {"result": ...}
# ===========================================================================


class TestJsonResponsesWrapped:
    """All application/json success responses must be wrapped in ``{"result": ...}``."""

    @given(schema=swagger_schema(max_depth=2))
    def test_every_json_200_response_wrapped(self, schema: dict[str, Any]) -> None:
        """Every 200 response with application/json must have a result wrapper."""
        spec = _make_spec(paths={
            "/resource": {
                "get": {
                    "operationId": "getResource",
                    "responses": {"200": {"description": "OK", "schema": schema}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)

        # Find the converted response schema
        resp = result["paths"]["/resource"]["get"]["responses"]["200"]
        content = resp.get("content", {})
        json_content = content.get("application/json")
        assume(json_content is not None)

        resp_schema = json_content.get("schema", {})
        assert _has_result_wrapper(resp_schema), (
            f"JSON 200 response missing result wrapper. "
            f"Input schema: {schema}, output schema: {resp_schema}"
        )

    @given(schema=swagger_schema(max_depth=2))
    def test_every_json_201_response_wrapped(self, schema: dict[str, Any]) -> None:
        """Every 201 response with application/json must have a result wrapper."""
        spec = _make_spec(paths={
            "/resource": {
                "post": {
                    "operationId": "createResource",
                    "responses": {"201": {"description": "Created", "schema": schema}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)

        resp = result["paths"]["/resource"]["post"]["responses"]["201"]
        content = resp.get("content", {})
        json_content = content.get("application/json")
        assume(json_content is not None)

        resp_schema = json_content.get("schema", {})
        assert _has_result_wrapper(resp_schema), (
            f"JSON 201 response missing result wrapper. "
            f"Input schema: {schema}, output schema: {resp_schema}"
        )


# ===========================================================================
# Invariant 4: Non-JSON responses are never wrapped
# ===========================================================================


class TestNonJsonResponsesNotWrapped:
    """Non-JSON content types must not get the ``{"result": ...}`` wrapper."""

    @given(produces=_NON_JSON_TYPES)
    def test_text_plain_response_not_wrapped(self, produces: list[str]) -> None:
        """A response with ``produces: ['text/plain']`` must NOT have a result wrapper."""
        spec = _make_spec(paths={
            "/download": {
                "get": {
                    "produces": produces,
                    "operationId": "downloadFile",
                    "responses": {
                        "200": {"description": "OK", "schema": {"type": "string"}},
                    },
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)

        resp = result["paths"]["/download"]["get"]["responses"]["200"]
        content = resp.get("content", {})

        # Non-JSON content types should not produce a result wrapper
        non_json_cts = [ct for ct in content if ct != "application/json"]
        for ct in non_json_cts:
            ct_schema = content[ct]["schema"]
            assert not _has_result_wrapper(ct_schema), (
                f"Non-JSON content type '{ct}' has result wrapper. "
                f"Produces: {produces}"
            )

        # If there is an application/json entry too, it SHOULD be wrapped
        if "application/json" in content:
            json_schema = content["application/json"]["schema"]
            assert _has_result_wrapper(json_schema), (
                f"JSON variant in mixed-content response missing result wrapper. "
                f"Produces: {produces}"
            )

    def test_no_produces_defaults_to_json_and_is_wrapped(self) -> None:
        """Endpoints without ``produces`` default to JSON and are wrapped."""
        spec = _make_spec(paths={
            "/item": {
                "get": {
                    "operationId": "getItem",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "schema": {"type": "object", "properties": {"id": {"type": "integer"}}},
                        },
                    },
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)

        resp = result["paths"]["/item"]["get"]["responses"]["200"]
        content = resp.get("content", {})
        assert "application/json" in content, (
            f"Endpoint without produces should default to application/json. "
            f"Got content types: {list(content.keys())}"
        )
        json_schema = content["application/json"]["schema"]
        assert _has_result_wrapper(json_schema), (
            "Default JSON response missing result wrapper."
        )


# ===========================================================================
# Invariant 5: Round-trip completeness — every input path survives
# ===========================================================================


class TestRoundTripCompleteness:
    """Every path from the input must survive conversion with at least one operation."""

    @given(
        path_count=st.integers(min_value=0, max_value=5),
        methods_per_path=st.lists(
            st.sampled_from(["get", "post", "put", "patch", "delete"]),
            min_size=0, max_size=4, unique=True,
        ),
    )
    def test_all_input_paths_preserved(
        self, path_count: int, methods_per_path: list[str],
    ) -> None:
        """All input paths must exist in the output."""
        paths: dict[str, Any] = {}
        for i in range(path_count):
            path = f"/resource_{i}"
            ops: dict[str, Any] = {}
            for method in methods_per_path:
                ops[method] = {
                    "operationId": f"{method}Resource{i}",
                    "responses": {"200": {"description": "OK", "schema": {"type": "object"}}},
                }
            if ops:
                paths[path] = ops

        spec = _make_spec(paths=paths)
        result = convert_swagger_to_openapi_v3(spec)
        result_paths = result.get("paths", {})

        for input_path, input_ops_dict in paths.items():
            assert input_path in result_paths, (
                f"Input path '{input_path}' missing from output. "
                f"Output paths: {list(result_paths.keys())}"
            )
            # At least one operation from this path must survive
            input_ops = [m for m in input_ops_dict if m in ("get", "post", "put", "patch", "delete")]
            output_ops = [m for m in result_paths[input_path] if m in ("get", "post", "put", "patch", "delete")]
            assert len(output_ops) == len(input_ops), (
                f"Path '{input_path}': expected {len(input_ops)} operation(s), "
                f"got {len(output_ops)}. Missing: {set(input_ops) - set(output_ops)}"
            )

    def test_empty_paths_survives(self) -> None:
        """An empty paths dict must not cause a crash and must survive."""
        spec = _make_spec(paths={})
        result = convert_swagger_to_openapi_v3(spec)
        assert "paths" in result
        assert result["paths"] == {}


# ===========================================================================
# Invariant 6: Parameter conversion preserves in, name, schema
# ===========================================================================


class TestParameterConversionPreserved:
    """Path, query, and header parameters must survive with correct fields."""

    @st.composite
    def _param(draw: st.DrawFn) -> dict[str, Any]:
        """Generate a single Swagger parameter dict."""
        param_in = draw(st.sampled_from(["path", "query", "header"]))
        param_name = draw(st.text(min_size=1, max_size=8, alphabet=st.characters(
            whitelist_categories=["Ll", "Lu", "Nd"],
        )))
        param_type = draw(st.sampled_from(["string", "integer", "boolean"]))
        param = {
            "in": param_in,
            "name": param_name,
            "type": param_type,
            "required": draw(st.booleans()),
        }
        if param_in == "path":
            param["required"] = True
        # Add optional description
        if draw(st.booleans()):
            param["description"] = draw(st.text(max_size=20))
        return param

    @given(params=st.lists(_param(), min_size=1, max_size=5, unique_by=lambda p: (p.get("in"), p.get("name"))))
    def test_parameters_preserve_in_and_name(self, params: list[dict[str, Any]]) -> None:
        """Each parameter must survive with the correct ``in`` and ``name`` fields."""
        spec = _make_spec(paths={
            "/resource/{param}": {
                "get": {
                    "operationId": "getResource",
                    "parameters": params,
                    "responses": {"200": {"description": "OK", "schema": {"type": "object"}}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)
        output_params = result["paths"]["/resource/{param}"]["get"].get("parameters", [])

        # Build lookup by (in, name) for both input and output
        input_index = {(p["in"], p["name"]): p for p in params}
        output_index: dict[tuple[str, str], dict[str, Any]] = {}
        for p in output_params:
            pin = p.get("in")
            pname = p.get("name")
            if pin and pname:
                output_index[(pin, pname)] = p

        for key in input_index:
            assert key in output_index, (
                f"Parameter ({key[0]}, {key[1]}) missing from output. "
                f"Input params: {params}, output params: {output_params}"
            )

    def test_parameter_schema_has_type(self) -> None:
        """Every output parameter must have a ``schema`` with a ``type``."""
        params = [
            {"in": "path", "name": "owner", "type": "string", "required": True},
            {"in": "query", "name": "limit", "type": "integer"},
            {"in": "header", "name": "X-Custom", "type": "string"},
        ]
        spec = _make_spec(paths={
            "/repos/{owner}": {
                "get": {
                    "operationId": "getRepo",
                    "parameters": params,
                    "responses": {"200": {"description": "OK", "schema": {"type": "object"}}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)
        output_params = result["paths"]["/repos/{owner}"]["get"]["parameters"]

        for p in output_params:
            assert "schema" in p, (
                f"Parameter '{p.get('name')}' missing 'schema' field. "
                f"Output: {p}"
            )
            assert "type" in p["schema"], (
                f"Parameter '{p.get('name')}' schema missing 'type'. "
                f"Output schema: {p['schema']}"
            )

    def test_path_required_flag_preserved(self) -> None:
        """Path parameters must have ``required: true`` after conversion."""
        params = [{"in": "path", "name": "id", "type": "integer", "required": True}]
        spec = _make_spec(paths={
            "/items/{id}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": params,
                    "responses": {"200": {"description": "OK", "schema": {"type": "object"}}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)
        output_params = result["paths"]["/items/{id}"]["get"]["parameters"]
        assert len(output_params) == 1
        assert output_params[0]["required"] is True, (
            f"Path parameter 'id' should be required: {output_params[0]}"
        )


# ===========================================================================
# Invariant 7: No crash on edge cases
# ===========================================================================


class TestEdgeCases:
    """Converter must handle corner-case inputs without crashing."""

    def test_minimal_spec_no_paths(self) -> None:
        """A spec with only swagger/info/basePath must not crash."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "basePath": "/api",
        }
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        assert "paths" not in result or result["paths"] == {}

    def test_no_info_field(self) -> None:
        """A spec without ``info`` must not crash."""
        spec = {"swagger": "2.0", "basePath": "/api"}
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"

    def test_info_not_a_dict(self) -> None:
        """``info`` as a non-dict must not crash."""
        spec = {"swagger": "2.0", "info": "just a string", "basePath": "/api"}
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"

    def test_no_base_path(self) -> None:
        """A spec without ``basePath`` must not crash."""
        spec = {"swagger": "2.0", "info": {"title": "T", "version": "1"}}
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        assert "servers" not in result

    def test_duplicate_operation_ids(self) -> None:
        """Duplicate operationId values must not cause a crash."""
        spec = _make_spec(paths={
            "/a": {"get": {"operationId": "getThing", "responses": {"200": {"description": "OK"}}}},
            "/b": {"get": {"operationId": "getThing", "responses": {"200": {"description": "OK"}}}},
        })
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"
        assert "/a" in result["paths"]
        assert "/b" in result["paths"]

    def test_null_values_in_spec(self) -> None:
        """A spec with None values for optional fields must not crash."""
        spec = {
            "swagger": "2.0",
            "info": {"title": "T", "version": "1"},
            "basePath": None,
            "paths": None,
            "definitions": None,
        }
        result = convert_swagger_to_openapi_v3(spec)
        assert result["openapi"] == "3.1.1"

    def test_path_with_no_operations(self) -> None:
        """A path item that is not a dict must not crash."""
        spec = _make_spec(paths={
            "/empty": "not a dict",
            "/real": {
                "get": {
                    "operationId": "getReal",
                    "responses": {"200": {"description": "OK", "schema": {"type": "object"}}},
                },
            },
        })
        result = convert_swagger_to_openapi_v3(spec)
        assert "/real" in result["paths"]
        # /empty is preserved as-is (non-dict items pass through) — at minimum no crash
