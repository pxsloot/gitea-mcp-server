"""Convert Swagger 2.0 spec to OpenAPI 3.1 format."""

# ruff: noqa: PLR0912,PLR0915

import logging
import re
from typing import Any, Protocol, cast

from gitea_mcp_server.exceptions import SpecError

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
# Protocols
# ============================================================================


class SchemaCallback(Protocol):
    """Protocol for schema walker callbacks."""

    def __call__(
        self, schema: dict[str, Any], parent: dict[str, Any] | None, key: str | None
    ) -> None: ...


# ============================================================================
# Component Transformers
# ============================================================================


class SchemaNormalizer:
    """Normalize Swagger 2.0 schema types to OpenAPI 3.1 compatible types."""

    def normalize(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single schema."""
        schema = dict(schema)
        if schema.get("type") == "file":
            schema["type"] = "string"
            schema["format"] = "binary"
        if schema.get("format") == "uint64":
            schema["format"] = "int64"
        return schema


class PropertyRequiredCollector:
    """Collect required fields from property-level and move to parent."""

    def collect_required(self, properties: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Process properties, collecting property-level required flags.

        Returns:
            (new_properties, required_fields)
        """
        new_properties = {}
        required_fields = []

        for prop_name, prop_schema in properties.items():
            if isinstance(prop_schema, dict):
                if prop_schema.get("required") is True:
                    required_fields.append(prop_name)
                    new_prop_schema = dict(prop_schema)
                    new_prop_schema.pop("required", None)
                    new_properties[prop_name] = new_prop_schema
                else:
                    new_properties[prop_name] = dict(prop_schema)
            else:
                new_properties[prop_name] = prop_schema

        return new_properties, required_fields


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

            if sec_type == "basic":
                scheme: dict[str, Any] = {"type": "http", "scheme": "basic"}
            elif sec_type == "oauth2":
                scheme = {"type": "oauth2"}
                if "flow" in details:
                    scheme["flows"] = self._convert_flow(details)
            else:
                scheme = {"type": details.get("type", "apiKey")}
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

        # Convert responses
        if "responses" in op_copy:
            op_copy["responses"] = convert_responses(op_copy["responses"])

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


# ============================================================================
# Schema Walker and Callbacks
# ============================================================================


class SchemaWalker:
    """Iterative schema walker for applying transformations."""

    def __init__(self, callback: SchemaCallback):
        self.callback = callback

    def walk(self, schema: dict[str, Any]) -> None:
        """Walk the schema tree iteratively and apply callback to each schema node."""
        stack: list[tuple[dict[str, Any], dict[str, Any] | None, str | None]] = [
            (schema, None, None)
        ]

        while stack:
            current_schema, parent, key = stack.pop()
            if not isinstance(current_schema, dict):
                continue  # type: ignore[unreachable]

            # Apply callback to current schema
            self.callback(current_schema, parent, key)

            # Process properties - push each property with parent=current_schema
            props = current_schema.get("properties")
            if isinstance(props, dict):
                for prop_name, prop_schema in props.items():
                    if isinstance(prop_schema, dict):
                        stack.append((prop_schema, current_schema, prop_name))

            # Process combinator schemas (allOf, anyOf, oneOf) - push with parent=current_schema
            for combo_key in ("allOf", "anyOf", "oneOf"):
                items = current_schema.get(combo_key)
                if isinstance(items, list):
                    for idx, item in enumerate(items):
                        if isinstance(item, dict):
                            stack.append((item, current_schema, combo_key))

            # Process items (array)
            items_schema = current_schema.get("items")
            if isinstance(items_schema, dict):
                stack.append((items_schema, current_schema, "items"))

            # Process additionalProperties
            add_props = current_schema.get("additionalProperties")
            if isinstance(add_props, dict):
                stack.append((add_props, current_schema, "additionalProperties"))

            # Process patternProperties
            pattern_props = current_schema.get("patternProperties")
            if isinstance(pattern_props, dict):
                for pat_key, pat_schema in pattern_props.items():
                    if isinstance(pat_schema, dict):
                        stack.append((pat_schema, current_schema, pat_key))


class OptionalPropertyTransformer:
    """Add null to optional properties and handle special format cases (e.g., email)."""

    FORMATS_NEEDING_EMPTY = {"email"}

    def __call__(
        self, schema: dict[str, Any], parent: dict[str, Any] | None, key: str | None
    ) -> None:
        if parent is None or key is None:
            return
        if not isinstance(parent, dict):
            return  # type: ignore[unreachable]

        # Determine if this schema is a property-like schema
        is_property = False
        if (
            ("properties" in parent and key in parent["properties"])
            or ("patternProperties" in parent and key in parent["patternProperties"])
            or (key == "additionalProperties" and "additionalProperties" in parent)
        ):
            is_property = True

        if not is_property:
            return

        # Determine if this property is optional
        optional = True
        if "properties" in parent and key in parent["properties"]:
            required = parent.get("required", [])
            if key in required:
                optional = False

        # Handle special formats (e.g., email) - always transform to anyOf
        if schema.get("type") == "string" and schema.get("format") in self.FORMATS_NEEDING_EMPTY:
            fmt = schema.get("format", "email")
            format_branch = {"type": "string", "format": fmt}
            for k in ["pattern", "minLength", "maxLength", "enum", "default"]:
                if k in schema:
                    format_branch[k] = schema[k]

            any_of = [format_branch]
            if optional:
                any_of.append({"type": "string", "maxLength": 0})
                any_of.append({"type": "null"})

            new_schema = {"anyOf": any_of}
            for k in ["description", "title", "example", "readOnly", "writeOnly", "deprecated"]:
                if k in schema:
                    new_schema[k] = schema[k]

            schema.clear()
            schema.update(new_schema)
            return

        # For other optional properties, add nullable
        if optional:
            if "$ref" in schema and "anyOf" not in schema and "oneOf" not in schema:
                ref = schema["$ref"]
                schema.clear()
                schema["anyOf"] = [{"$ref": ref}, {"type": "null"}]
                return

            if "type" in schema and "anyOf" not in schema and "oneOf" not in schema:
                t = schema["type"]
                if isinstance(t, str):
                    if t != "null":
                        schema["type"] = [t, "null"]
                elif isinstance(t, list) and "null" not in t:
                    t.append("null")


# The following transformers are no-ops because recursion is handled by walker
class CombinatorSchemaTransformer: ...


class ArrayItemsTransformer: ...


class AdditionalPropertiesTransformer: ...


class PatternPropertiesTransformer: ...


def _add_nullable_for_optional_refs_impl(spec: dict[str, Any]) -> None:
    """Apply nullable transformations to all component schemas."""
    components = spec.get("components", {})
    schemas = components.get("schemas", {})
    walker = SchemaWalker(OptionalPropertyTransformer())
    for schema in schemas.values():
        if isinstance(schema, dict):
            walker.walk(schema)


# Backward compatibility
def _add_nullable_for_optional_refs(spec: dict[str, Any]) -> None:
    """Legacy wrapper."""
    _add_nullable_for_optional_refs_impl(spec)


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
            param_copy["schema"] = SchemaNormalizer().normalize(param_copy["schema"])
        else:
            schema_dict = {}
            for field in SCHEMA_FIELDS:
                if field in param_copy:
                    schema_dict[field] = param_copy.pop(field)
            if schema_dict:
                param_copy["schema"] = SchemaNormalizer().normalize(schema_dict)

        param_copy.pop("collectionFormat", None)
        new_params.append(param_copy)

    return new_params


def convert_responses(responses: dict[str, Any]) -> dict[str, Any]:
    """Convert Swagger 2.0 responses to OpenAPI 3.1 format."""
    new_responses = {}

    for status, response in responses.items():
        if not isinstance(response, dict):
            new_responses[status] = response
            continue

        response_copy = dict(response)

        if "schema" in response_copy:
            schema = response_copy.pop("schema")
            response_copy["content"] = {"application/json": {"schema": convert_schema(schema)}}

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

    if "properties" in schema:
        props = schema.get("properties", {})
        if isinstance(props, dict):
            new_properties, required_fields = PropertyRequiredCollector().collect_required(props)
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
# Main Entry Point
# ============================================================================


def convert_swagger_to_openapi_v3(spec: dict[str, Any]) -> dict[str, Any]:
    """Convert Swagger 2.0 spec to OpenAPI 3.1.

    Args:
        spec: Swagger 2.0 specification as a dictionary

    Returns:
        OpenAPI 3.1 specification as a dictionary

    Raises:
        SpecError: If conversion fails due to invalid input
    """
    if not isinstance(spec, dict):
        msg = "Invalid spec: must be a dictionary"  # type: ignore[unreachable]
        raise SpecError(msg)

    spec = dict(spec)

    # Validate Swagger version
    swagger_version = spec.get("swagger")
    if swagger_version != "2.0":
        msg = f"Expected Swagger 2.0, got {swagger_version}"
        raise SpecError(msg)

    logger.info("Starting OpenAPI conversion", extra={"swagger_version": swagger_version})

    # Step 1: Update spec version
    SpecVersionUpdater().update(spec)

    # Step 2: Update info (preserve version in description)
    if "info" in spec and isinstance(spec["info"], dict):
        info = dict(spec["info"])
        if "version" in info:
            version = info["version"]
            desc = info.get("description", "")
            if desc:
                info["description"] = f"{desc}\n\nAPI Version: {version}"
        spec["info"] = info

    # Step 3: Convert basePath to servers
    BasePathToServerConverter().convert(spec)

    # Step 4: Initialize components
    components: dict[str, Any] = {}

    # Step 5: Convert definitions -> components/schemas
    if "definitions" in spec:
        definitions = spec.pop("definitions")
        if isinstance(definitions, dict):
            components["schemas"] = convert_definitions(definitions)

    # Step 6: Convert responses -> components/responses
    if "responses" in spec:
        responses = spec.pop("responses")
        if isinstance(responses, dict):
            components["responses"] = convert_responses(responses)

    # Step 7: Convert parameters -> components/parameters
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

    # Step 8: Convert securityDefinitions -> components/securitySchemes
    if "securityDefinitions" in spec:
        sec_defs = spec.pop("securityDefinitions")
        if isinstance(sec_defs, dict):
            security_schemes = SecuritySchemeConverter().convert(sec_defs)
            if security_schemes:
                components["securitySchemes"] = security_schemes

    # Step 9: Attach components if non-empty
    if components:
        spec["components"] = components

    # Step 10: Convert paths
    if "paths" in spec:
        spec["paths"] = convert_paths(spec["paths"])

    # Step 11: Remove Swagger-specific root fields
    remove_swagger_fields(spec, ["consumes", "produces", "schemes"])

    # Step 12: Fix $ref references
    spec = ReferenceFixer().fix(spec)

    # Step 13: Add nullable for optional reference fields
    _add_nullable_for_optional_refs_impl(spec)

    logger.info("OpenAPI conversion completed successfully")
    return spec
