"""Core conversion pipeline - Swagger 2.0 to OpenAPI 3.1.

Schema walker and transformation utilities live in ``.schema``.
This module contains the converter classes and the public
``convert_swagger_to_openapi_v3`` entry point.
"""

import logging
import re
from copy import deepcopy
from typing import Any, cast

from gitea_mcp_server.exceptions import SpecError
from gitea_mcp_server.openapi_types import OpenAPISpec, SwaggerV2Spec

from .schema import (
    OptionalPropertyTransformer,
    PropertyRequiredCollector,
    SchemaNormalizer,
    SchemaWalker,
)

logger = logging.getLogger(__name__)

SCHEMA_FIELDS = frozenset(
    {
        "type",
        "format",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "maxItems",
        "minItems",
        "uniqueItems",
        "enum",
        "multipleOf",
        "maxProperties",
        "minProperties",
        "items",
        "$ref",
        "default",
    }
)


# ============================================================================
# Utilities
# ============================================================================


def camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def remove_swagger_fields(obj: dict[str, Any], fields: list[str]) -> None:
    """Remove Swagger-specific fields from object in-place."""
    for field in fields:
        obj.pop(field, None)


# ============================================================================
# Component Transformers
# ============================================================================


class ReferenceFixer:
    """Fix $ref references from Swagger 2.0 to OpenAPI 3.x format."""

    def fix(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Fix all $ref references in the spec."""

        def fix_value(obj: Any) -> Any:
            if isinstance(obj, dict):
                if "$ref" in obj and isinstance(obj["$ref"], str):
                    ref = obj["$ref"]
                    replacements = [
                        ("#/definitions/", "#/components/schemas/"),
                        ("#/responses/", "#/components/responses/"),
                        ("#/parameters/", "#/components/parameters/"),
                        ("#/securityDefinitions/", "#/components/securitySchemes/"),
                        ("#/definitions", "#/components/schemas"),
                        ("#/responses", "#/components/responses"),
                        ("#/parameters", "#/components/parameters"),
                        ("#/securityDefinitions", "#/components/securitySchemes"),
                    ]
                    for old, new in replacements:
                        if ref.startswith(old):
                            obj["$ref"] = ref.replace(old, new, 1)
                            break
                return {k: fix_value(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [fix_value(item) for item in obj]
            return obj

        return cast("dict[str, Any]", fix_value(spec))


# Backward compatibility
def fix_references(spec: dict[str, Any]) -> dict[str, Any]:
    """Legacy wrapper for ReferenceFixer.fix()."""
    return ReferenceFixer().fix(spec)


class SpecVersionUpdater:
    """Update spec version from Swagger 2.0 to OpenAPI 3.1."""

    def update(self, spec: dict[str, Any]) -> None:
        """Upgrade to OpenAPI 3.1.1 and remove swagger field."""
        spec["openapi"] = "3.1.1"
        spec.pop("swagger", None)


class BasePathToServerConverter:
    """Convert basePath, host, schemes to servers array."""

    def convert(self, spec: dict[str, Any]) -> None:
        """Convert basePath to servers entry."""
        if "basePath" not in spec:
            return

        base_path = spec.pop("basePath")
        host = spec.pop("host", None)
        schemes = spec.pop("schemes", ["http"])

        server_url = ""
        if schemes and host:
            server_url = f"{schemes[0]}://{host}{base_path}"
        elif base_path:
            server_url = base_path

        if server_url:
            spec["servers"] = [{"url": server_url}]


class OperationIdFormatter:
    """Format and generate operation IDs."""

    def generate(self, method: str, path: str) -> str:
        """Generate an operationId from method and path."""
        op_id = f"{method}{path.replace('/', '_')}"
        return self.normalize(op_id)

    def normalize(self, op_id: str) -> str:
        """Normalize operationId to snake_case."""
        return camel_to_snake(op_id)


class RequestBodyBuilder:
    """Build requestBody objects from formData or body parameters."""

    def build_from_form_data(self, form_params: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Build multipart/form-data and x-www-form-urlencoded requestBody."""
        if not form_params:
            return None

        schema_props = {}
        required_fields = []

        for param in form_params:
            name = param.get("name", "unnamed")
            if "schema" in param:
                prop_schema = SchemaNormalizer().normalize(param["schema"])
            else:
                prop_schema = {"type": param.get("type", "string")}
                if "description" in param:
                    prop_schema["description"] = param["description"]
                prop_schema = SchemaNormalizer().normalize(prop_schema)

            schema_props[name] = prop_schema
            if param.get("required", False):
                required_fields.append(name)

        if not schema_props:
            return None

        form_schema = {"type": "object", "properties": schema_props}
        if required_fields:
            form_schema["required"] = required_fields

        return {
            "content": {
                "multipart/form-data": {"schema": form_schema},
                "application/x-www-form-urlencoded": {"schema": form_schema},
            },
            "required": True,
        }

    def build_from_body_params(self, body_params: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Build application/json requestBody."""
        if not body_params:
            return None

        body_param = body_params[0]
        if "schema" in body_param:
            body_schema = convert_schema(body_param["schema"])
            return {
                "content": {"application/json": {"schema": body_schema}},
                "required": body_param.get("required", True),
            }
        return None


class SecuritySchemeConverter:
    """Convert Swagger 2.0 securityDefinitions to OpenAPI 3.x securitySchemes."""

    def convert(self, sec_defs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Convert security definitions dictionary."""
        security_schemes: dict[str, dict[str, Any]] = {}

        for name, details in sec_defs.items():
            if not isinstance(details, dict):
                continue

            sec_type = details.get("type", "apiKey").lower()

            scheme: dict[str, Any] = {}
            if "description" in details:
                scheme["description"] = details["description"]

            if sec_type == "basic":
                scheme["type"] = "http"
                scheme["scheme"] = "basic"
            elif sec_type == "oauth2":
                scheme["type"] = "oauth2"
                if "flow" in details:
                    scheme["flows"] = self._convert_flow(details)
            else:
                scheme["type"] = details.get("type", "apiKey")
                if "name" in details:
                    scheme["name"] = details["name"]
                if "in" in details:
                    scheme["in"] = details["in"]

            security_schemes[name] = scheme

        return security_schemes

    def _convert_flow(self, details: dict[str, Any]) -> dict[str, Any]:
        """Convert OAuth2 flow configuration."""
        flow = details["flow"]
        flows: dict[str, Any] = {}

        if flow == "implicit":
            flows["implicit"] = {
                "authorizationUrl": details.get("authorizationUrl"),
                "scopes": details.get("scopes", {}),
            }
        elif flow == "password":
            flows["password"] = {
                "tokenUrl": details.get("tokenUrl"),
                "scopes": details.get("scopes", {}),
            }
        elif flow == "clientCredentials":
            flows["clientCredentials"] = {
                "tokenUrl": details.get("tokenUrl"),
                "scopes": details.get("scopes", {}),
            }
        elif flow == "authorizationCode":
            flows["authorizationCode"] = {
                "authorizationUrl": details.get("authorizationUrl"),
                "tokenUrl": details.get("tokenUrl"),
                "scopes": details.get("scopes", {}),
            }

        return flows


class OperationTransformer:
    """Transform an operation object to OpenAPI 3.x format."""

    def __init__(
        self, request_body_builder: RequestBodyBuilder, op_id_formatter: OperationIdFormatter
    ):
        self.request_body_builder = request_body_builder
        self.op_id_formatter = op_id_formatter

    def transform(
        self, operation: dict[str, Any], path: str, method: str, raw_params: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Transform a single operation."""
        op_copy = dict(operation)

        # Convert parameters (filters out body/formData)
        op_copy["parameters"] = convert_parameters(raw_params)

        # Only add requestBody for methods that typically have bodies
        if method in ("post", "put", "patch"):
            # Handle formData parameters -> requestBody
            form_params = [
                p for p in raw_params if isinstance(p, dict) and p.get("in") == "formData"
            ]
            form_body = self.request_body_builder.build_from_form_data(form_params)
            if form_body:
                op_copy["requestBody"] = form_body

            # Handle body parameters -> requestBody
            body_params = [p for p in raw_params if isinstance(p, dict) and p.get("in") == "body"]
            body_req = self.request_body_builder.build_from_body_params(body_params)
            if body_req:
                op_copy["requestBody"] = body_req

        # Preserve non-JSON content types before conversion (produces is
        # stripped by remove_swagger_fields below). Used downstream to
        # distinguish text/plain from application/json responses so we can
        # skip output_schema wrapping for non-JSON endpoints.
        produces = op_copy.get("produces", [])
        non_json = [ct for ct in produces if ct.lower().strip() != "application/json"]
        if non_json:
            op_copy["x-original-content-types"] = non_json

        # Convert responses
        if "responses" in op_copy:
            op_copy["responses"] = convert_responses(op_copy["responses"], produces)

        # Ensure operationId exists and is normalized
        if "operationId" not in op_copy:
            op_copy["operationId"] = self.op_id_formatter.generate(method, path)
        else:
            op_copy["operationId"] = self.op_id_formatter.normalize(op_copy["operationId"])

        # Remove Swagger-specific fields from operation
        remove_swagger_fields(op_copy, ["produces", "consumes"])

        return op_copy


class PathsConverter:
    """Convert paths object to OpenAPI 3.x format."""

    def __init__(self, operation_transformer: OperationTransformer):
        self.operation_transformer = operation_transformer

    def convert(self, paths: dict[str, Any]) -> dict[str, Any]:
        """Convert all paths and their operations."""
        new_paths = {}
        allowed_methods = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                new_paths[path] = path_item
                continue

            path_item_copy = dict(path_item)

            # Convert path-level parameters
            if "parameters" in path_item_copy:
                path_item_copy["parameters"] = convert_parameters(path_item_copy["parameters"])

            # Process each HTTP method
            for method in list(path_item_copy.keys()):
                if method not in allowed_methods:
                    continue

                operation = path_item_copy[method]
                if not isinstance(operation, dict):
                    continue

                raw_params = operation.get("parameters", [])
                op_copy = self.operation_transformer.transform(operation, path, method, raw_params)
                path_item_copy[method] = op_copy

            # Remove Swagger-specific fields from path item
            remove_swagger_fields(path_item_copy, ["produces", "consumes"])

            new_paths[path] = path_item_copy

        return new_paths


def _add_nullable_for_optional_refs(spec: OpenAPISpec) -> None:
    """Apply nullable transformations to all component schemas.

    Args:
        spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``).
              Only ``components/schemas`` is accessed.
    """
    components = spec.get("components", {})
    schemas = components.get("schemas", {})
    walker = SchemaWalker(OptionalPropertyTransformer())
    for schema in schemas.values():
        if isinstance(schema, dict):
            walker.walk(schema)


# ============================================================================
# Core Conversion Functions
# ============================================================================


def convert_parameters(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Swagger 2.0 parameters to OpenAPI 3.1 format."""
    new_params = []

    for param in parameters:
        param_in = param.get("in")
        if param_in in ("body", "formData"):
            continue

        param_copy = dict(param)

        if "schema" in param_copy:
            param_copy["schema"] = convert_schema(param_copy["schema"])
        else:
            schema_dict = {}
            for field in SCHEMA_FIELDS:
                if field in param_copy:
                    schema_dict[field] = param_copy.pop(field)
            if schema_dict:
                param_copy["schema"] = convert_schema(schema_dict)

        param_copy.pop("collectionFormat", None)

        # Strip vendor extensions from the parameter-level (defense-in-depth).
        # Schema-level x-* are handled by convert_schema() above.
        for key in list(param_copy.keys()):
            if key.startswith("x-"):
                del param_copy[key]

        new_params.append(param_copy)

    return new_params


def _determine_content_type(produces: list[str] | None) -> str:
    """Determine the correct response content-type from the Swagger ``produces`` list.

    Args:
        produces: The ``produces`` list from a Swagger operation, or ``None``.

    Returns:
        The MIME type to use in the response content, defaulting to
        ``application/json``.
    """
    if produces:
        for ct in produces:
            ct_lower = ct.lower().strip()
            # Non-JSON types like text/plain should be preserved.
            if ct_lower != "application/json":
                return ct_lower
    return "application/json"


def convert_responses(
    responses: dict[str, Any],
    produces: list[str] | None = None,
) -> dict[str, Any]:
    """Convert Swagger 2.0 responses to OpenAPI 3.1 format.

    Args:
        responses: Swagger 2.0 responses dictionary
        produces: Original ``produces`` content types from the operation.
                  Used to preserve non-JSON content types (e.g. ``text/plain``)
                  instead of always assigning ``application/json``.

    """
    new_responses = {}

    for status, response in responses.items():
        if not isinstance(response, dict):
            new_responses[status] = response
            continue

        response_copy = dict(response)

        if "schema" in response_copy:
            schema = response_copy.pop("schema")
            # Use the correct content type from produces if available.
            # text/plain endpoints (diff, patch) should not be marked as
            # application/json -- FastMCP's OpenAPITool will try response.json()
            # and fail, then fall back to ToolResult(content=text) which causes
            # "outputSchema defined but no structured output returned".
            content_type = _determine_content_type(produces)
            response_copy["content"] = {content_type: {"schema": convert_schema(schema)}}

        if "headers" in response_copy and isinstance(response_copy["headers"], dict):
            headers = response_copy["headers"]
            converted_headers = {}
            for hdr_name, hdr_def in headers.items():
                if isinstance(hdr_def, dict):
                    hdr = dict(hdr_def)
                    if "schema" not in hdr:
                        schema_dict = {}
                        for field in SCHEMA_FIELDS:
                            if field in hdr:
                                schema_dict[field] = hdr.pop(field)
                        if schema_dict:
                            hdr["schema"] = SchemaNormalizer().normalize(schema_dict)
                    converted_headers[hdr_name] = hdr
                else:
                    converted_headers[hdr_name] = hdr_def
            response_copy["headers"] = converted_headers

        new_responses[status] = response_copy

    return new_responses


def convert_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a Swagger 2.0 schema to OpenAPI 3.1 format."""
    schema = dict(schema)
    schema = SchemaNormalizer().normalize(schema)

    schema.pop("readOnly", None)
    schema.pop("xml", None)

    # Strip vendor extensions (x-*) - they leak Go struct internals
    # (x-go-name, x-go-package) into agent-facing parameter schemas.
    # Operation-level x-* fields like x-original-content-types live on
    # operation objects, not schema objects, so they are unaffected.
    for key in list(schema.keys()):
        if key.startswith("x-"):
            del schema[key]

    if "properties" in schema:
        props = schema.get("properties", {})
        if isinstance(props, dict):
            new_properties, required_fields = PropertyRequiredCollector().collect_required(props)
            # Strip vendor extensions from each property schema.
            # Top-level x-* already stripped above; property schemas
            # are dict copies from collect_required() and need cleanup.
            for prop_schema in new_properties.values():
                if isinstance(prop_schema, dict):
                    for key in list(prop_schema.keys()):
                        if key.startswith("x-"):
                            del prop_schema[key]
            schema["properties"] = new_properties
            if required_fields:
                schema["required"] = required_fields

    if "type" in schema and schema["type"] == "array" and "items" in schema:
        schema["items"] = convert_schema(schema["items"])

    for combo in ["allOf", "anyOf", "oneOf"]:
        if combo in schema:
            schema[combo] = [convert_schema(s) for s in schema[combo]]

    return schema


def convert_definitions(definitions: dict[str, Any]) -> dict[str, Any]:
    """Convert Swagger 2.0 definitions to OpenAPI 3.1 schemas."""
    converted = {}
    for name, schema in definitions.items():
        converted[name] = convert_schema(schema)

    result = {"definitions": converted}
    fixed = ReferenceFixer().fix(result)
    return cast("dict[str, Any]", fixed["definitions"])


def convert_paths(paths: dict[str, Any]) -> dict[str, Any]:
    """Convert Swagger 2.0 paths to OpenAPI 3.1 format."""
    request_body_builder = RequestBodyBuilder()
    op_id_formatter = OperationIdFormatter()
    operation_transformer = OperationTransformer(request_body_builder, op_id_formatter)
    paths_converter = PathsConverter(operation_transformer)
    return paths_converter.convert(paths)


# ============================================================================
# Main Entry Point - Conversion Steps
# ============================================================================


def _validate_spec(spec: Any) -> None:
    """Validate the input spec is a valid Swagger 2.0 dictionary."""
    if not isinstance(spec, dict):
        msg = "Invalid spec: must be a dictionary"
        raise SpecError(msg)
    swagger_version = spec.get("swagger")
    if swagger_version != "2.0":
        msg = f"Expected Swagger 2.0, got {swagger_version}"
        raise SpecError(msg)


def _update_info_version(spec: dict[str, Any]) -> None:
    """Update info - preserve version in description."""
    if "info" not in spec or not isinstance(spec["info"], dict):
        return
    info = dict(spec["info"])
    if "version" in info:
        version = info["version"]
        desc = info.get("description", "")
        if desc:
            info["description"] = f"{desc}\n\nAPI Version: {version}"
    spec["info"] = info


def _convert_components(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert definition/response/parameter/securityDefinition to components."""
    components: dict[str, Any] = {}

    if "definitions" in spec:
        definitions = spec.pop("definitions")
        if isinstance(definitions, dict):
            components["schemas"] = convert_definitions(definitions)

    if "responses" in spec:
        responses = spec.pop("responses")
        if isinstance(responses, dict):
            components["responses"] = convert_responses(responses)

    if "parameters" in spec:
        params = spec.pop("parameters")
        if isinstance(params, dict):
            param_list = list(params.values())
        elif isinstance(params, list):
            param_list = params
        else:
            param_list = []
        if param_list:
            converted_params = convert_parameters(param_list)
            param_dict = {p["name"]: p for p in converted_params if "name" in p}
            components["parameters"] = param_dict

    if "securityDefinitions" in spec:
        sec_defs = spec.pop("securityDefinitions")
        if isinstance(sec_defs, dict):
            security_schemes = SecuritySchemeConverter().convert(sec_defs)
            if security_schemes:
                components["securitySchemes"] = security_schemes

    return components


def _resolve_spec_ref(spec: OpenAPISpec, ref: str) -> dict[str, Any] | None:
    """Resolve a ``$ref`` pointer (e.g. ``#/components/schemas/Foo``) in a spec.

    Walks the spec tree using string path segments.  Returns ``None`` if
    any segment is missing (handles malformed refs gracefully).

    Args:
        spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``).
        ref: The ``$ref`` string to resolve.

    Returns:
        The resolved schema dict, or ``None`` if resolution fails.
    """
    parts = ref.lstrip("#/").split("/")
    current: Any = spec
    try:
        for part in parts:
            current = current[part]
    except (KeyError, TypeError):
        return None
    return current if isinstance(current, dict) else None


def _wrap_response_schema(response: dict[str, Any], spec: OpenAPISpec) -> None:
    """Wrap a response schema in ``result`` so output_schema matches runtime shape.

    FastMCP 3.x requires ``output_schema`` to be ``type: object`` at runtime.
    The wrapping pipeline always wraps results in
    ``{"result": result}``. This function ensures the schema in the OpenAPI
    spec reflects that same wrapping.

    ``$ref`` schemas (media-type level) are resolved so the wrapped schema
    is self-contained at each response site.

    Note: Response-level ``$ref`` responses (e.g.
    ``{"$ref": "#/components/responses/empty"}``) are skipped here because
    they have no ``content`` key — the function returns early.  The ``$ref``
    survives and is resolved at tool-registration time by downstream code in
    ``tools/schemas.py``.

    Args:
        response: A single response object from the spec (``dict[str, Any]``
                  because status-code keys are dynamic).
        spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``)
              used for ``$ref`` resolution.

    Remove this when FastMCP adds native non-object ``output_schema`` support.
    """
    content = response.get("content", {})
    if not isinstance(content, dict):
        return
    json_content = content.get("application/json", {})
    if not isinstance(json_content, dict):
        return
    schema = json_content.get("schema")
    if not isinstance(schema, dict):
        return

    # Resolve $ref schemas to get the actual schema before wrapping.
    if "$ref" in schema:
        resolved = _resolve_spec_ref(spec, schema["$ref"])
        if not isinstance(resolved, dict):
            return
        schema = deepcopy(resolved)

    json_content["schema"] = {
        "type": "object",
        "properties": {
            "result": schema,
        },
    }


def _wrap_success_response_schemas(spec: OpenAPISpec) -> None:
    """Wrap all success response schemas in a ``result`` object container.

    FastMCP 3.x requires ``output_schema`` to be ``type: object`` at runtime.
    The wrapping pipeline wraps all tool results
    in ``{"result": result}``. This function transforms the spec so every
    200/201 response schema reflects that same wrapping -- regardless of
    the original response type.

    Shared response components in ``components/responses`` are also wrapped
    for consistency.

    ``paths`` and ``components`` sub-objects are cast to ``dict[str, Any]``
    for dynamic-key access (URL paths, status codes).

    Remove this when FastMCP adds native non-object ``output_schema`` support.

    Args:
         spec: Post-conversion OpenAPI 3.1 spec (typed as ``OpenAPISpec``,
               mutated in place).
    """
    paths: dict[str, Any] = cast("dict[str, Any]", spec.get("paths", {}))
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            for code in ("200", "201"):
                response = responses.get(code)
                if not isinstance(response, dict):
                    continue
                _wrap_response_schema(response, spec)

    components = spec.get("components", {})
    for response in components.get("responses", {}).values():
        if isinstance(response, dict):
            _wrap_response_schema(response, spec)


def convert_swagger_to_openapi_v3(spec: SwaggerV2Spec) -> dict[str, Any]:
    """Convert Swagger 2.0 spec to OpenAPI 3.1.

    Accepts a ``SwaggerV2Spec`` typed input, immediately creates a mutable
    ``dict[str, Any]`` copy for the conversion pipeline, then casts to
    ``OpenAPISpec`` for the final read-only wrapping steps.

    Args:
        spec: Swagger 2.0 specification (typed as ``SwaggerV2Spec``)

    Returns:
        OpenAPI 3.1 specification as a dictionary

    Raises:
        SpecError: If conversion fails due to invalid input
    """
    _validate_spec(spec)
    result: dict[str, Any] = dict(spec)

    swagger_version = result.get("swagger")
    logger.info("Starting OpenAPI conversion", extra={"swagger_version": swagger_version})

    SpecVersionUpdater().update(result)
    _update_info_version(result)
    BasePathToServerConverter().convert(result)

    components = _convert_components(result)
    if components:
        result["components"] = components

    if "paths" in result:
        result["paths"] = convert_paths(result["paths"])

    remove_swagger_fields(result, ["consumes", "produces", "schemes"])
    result = ReferenceFixer().fix(result)
    _add_nullable_for_optional_refs(cast("OpenAPISpec", result))
    _wrap_success_response_schemas(cast("OpenAPISpec", result))

    logger.info("OpenAPI conversion completed successfully")
    return result


__all__ = [
    "convert_swagger_to_openapi_v3",
]
