"""Convert Swagger 2.0 spec to OpenAPI 3.1 format."""

import logging
import re
from typing import Any, cast

from gitea_mcp_server.exceptions import SpecError

logger = logging.getLogger(__name__)


def camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case.

    Handles consecutive uppercase letters properly:
    - "GetURL" -> "get_url"
    - "repoGet" -> "repo_get"
    - "issueCreateIssue" -> "issue_create_issue"
    """
    # Insert underscore before uppercase letters followed by lowercase
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    # Insert underscore before uppercase letters that follow lowercase or digits
    s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower()


def remove_swagger_fields(obj: dict[str, Any], fields: list[str]) -> None:
    """Remove Swagger-specific fields from object in-place."""
    for field in fields:
        obj.pop(field, None)


def normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize Swagger 2.0 schema types to OpenAPI 3.1 compatible types."""
    schema = dict(schema)
    # Convert file type to binary string (for file uploads/downloads)
    if schema.get("type") == "file":
        schema["type"] = "string"
        schema["format"] = "binary"
    return schema


def fix_references(spec: dict[str, Any]) -> dict[str, Any]:
    """Fix $ref references from Swagger 2.0 to OpenAPI 3.x format.

    Handles:
    - #/definitions/ -> #/components/schemas/
    - #/responses/ -> #/components/responses/
    - #/parameters/ -> #/components/parameters/
    - #/securityDefinitions/ -> #/components/securitySchemes/
    """

    def fix_value(obj: Any) -> Any:
        if isinstance(obj, dict):
            # Check if this is a $ref that needs fixing
            if "$ref" in obj and isinstance(obj["$ref"], str):
                ref = obj["$ref"]
                # Handle all standard Swagger 2.0 reference patterns
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
            # Recursively fix all dict values
            return {k: fix_value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [fix_value(item) for item in obj]
        return obj

    return cast("dict[str, Any]", fix_value(spec))


def convert_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a Swagger 2.0 schema to OpenAPI 3.1 format."""
    schema = dict(schema)
    schema = normalize_schema(schema)

    # Remove Swagger-specific fields
    schema.pop("readOnly", None)
    schema.pop("xml", None)

    # Handle nested properties and collect required fields from property-level
    if "properties" in schema:
        new_properties = {}
        required_fields = []

        for prop_name, prop_schema in schema["properties"].items():
            if isinstance(prop_schema, dict):
                # Check for property-level required: true (Swagger 2.0 style)
                if prop_schema.get("required") is True:
                    required_fields.append(prop_name)
                    # Create copy and remove required from property
                    new_prop_schema = dict(prop_schema)
                    new_prop_schema.pop("required", None)
                    new_properties[prop_name] = convert_schema(new_prop_schema)
                else:
                    new_properties[prop_name] = convert_schema(prop_schema)
            else:
                new_properties[prop_name] = prop_schema

        schema["properties"] = new_properties

        # Only add required if there are fields (avoid empty or null)
        if required_fields:
            schema["required"] = required_fields

    # Handle array items
    if "type" in schema and schema["type"] == "array" and "items" in schema:
        schema["items"] = convert_schema(schema["items"])

    # Handle allOf, anyOf, oneOf
    for combo in ["allOf", "anyOf", "oneOf"]:
        if combo in schema:
            schema[combo] = [convert_schema(s) for s in schema[combo]]

    return schema


def convert_parameters(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Swagger 2.0 parameters to OpenAPI 3.1 format.

    Excludes body and formData parameters (handled separately as requestBody).
    Ensures schema information is properly nested under 'schema' key.
    """
    new_params = []

    # Fields that belong inside schema in OAS 3.1 (type, format, etc.)
    # Note: 'required' stays at parameter level, NOT inside schema
    schema_fields = {
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

    for param in parameters:
        param_in = param.get("in")
        # Skip body and formData - they become requestBody
        if param_in in ("body", "formData"):
            continue

        param_copy = dict(param)

        # If schema already exists, normalize it
        if "schema" in param_copy:
            param_copy["schema"] = normalize_schema(param_copy["schema"])
        else:
            # Move type-related fields from top-level into schema
            schema_dict = {}
            for field in list(schema_fields):
                if field in param_copy:
                    schema_dict[field] = param_copy.pop(field)
            if schema_dict:
                param_copy["schema"] = normalize_schema(schema_dict)

        # Remove Swagger-specific fields not allowed in OAS 3.1 parameters
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

        # Convert headers: type fields must be under schema
        if "headers" in response_copy and isinstance(response_copy["headers"], dict):
            headers = response_copy["headers"]
            converted_headers = {}
            for hdr_name, hdr_def in headers.items():
                if isinstance(hdr_def, dict):
                    hdr = dict(hdr_def)
                    if "schema" not in hdr:
                        schema_dict = {}
                        for field in [
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
                        ]:
                            if field in hdr:
                                schema_dict[field] = hdr.pop(field)
                        if schema_dict:
                            hdr["schema"] = normalize_schema(schema_dict)
                    converted_headers[hdr_name] = hdr
                else:
                    converted_headers[hdr_name] = hdr_def
            response_copy["headers"] = converted_headers

        new_responses[status] = response_copy

    return new_responses


def convert_definitions(definitions: dict[str, Any]) -> dict[str, Any]:
    """Convert Swagger 2.0 definitions to OpenAPI 3.1 schemas."""
    converted = {}
    for name, schema in definitions.items():
        converted[name] = convert_schema(schema)

    # Fix internal $ref references
    result = {"definitions": converted}
    fixed = fix_references(result)
    return cast("dict[str, Any]", fixed["definitions"])


def convert_paths(paths: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
    """Convert Swagger 2.0 paths to OpenAPI 3.1 format."""
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

            op_copy = dict(operation)

            # Keep original parameters list for later processing
            raw_params = op_copy.get("parameters", [])
            # Convert parameters (filters out body/formData)
            op_copy["parameters"] = convert_parameters(raw_params)

            # Only add requestBody for methods that typically have bodies
            if method in ("post", "put", "patch"):
                # Handle formData parameters -> requestBody (multipart/form-data + x-www-form-urlencoded)
                form_params = [
                    p for p in raw_params if isinstance(p, dict) and p.get("in") == "formData"
                ]
                if form_params:
                    schema_props = {}
                    required_fields = []

                    for param in form_params:
                        name = param.get("name", "unnamed")
                        if "schema" in param:
                            prop_schema = normalize_schema(param["schema"])
                        else:
                            # Build simple schema from type fields
                            prop_schema = {
                                "type": param.get("type", "string"),
                            }
                            if "description" in param:
                                prop_schema["description"] = param["description"]
                            prop_schema = normalize_schema(prop_schema)

                        schema_props[name] = prop_schema
                        if param.get("required", False):
                            required_fields.append(name)

                    if schema_props:
                        form_schema = {"type": "object", "properties": schema_props}
                        if required_fields:
                            form_schema["required"] = required_fields

                        op_copy["requestBody"] = {
                            "content": {
                                "multipart/form-data": {"schema": form_schema},
                                "application/x-www-form-urlencoded": {"schema": form_schema},
                            },
                            "required": True,
                        }

                # Handle body parameters -> requestBody (application/json)
                body_params = [
                    p for p in raw_params if isinstance(p, dict) and p.get("in") == "body"
                ]
                if body_params:
                    body_param = body_params[0]
                    if "schema" in body_param:
                        body_schema = convert_schema(body_param["schema"])
                        op_copy["requestBody"] = {
                            "content": {"application/json": {"schema": body_schema}},
                            "required": body_param.get("required", True),
                        }

            # Convert responses
            if "responses" in op_copy:
                op_copy["responses"] = convert_responses(op_copy["responses"])

            # Ensure operationId exists
            if "operationId" not in op_copy:
                op_copy["operationId"] = f"{method}{path.replace('/', '_')}"

            # Normalize operationId to snake_case for MCP compliance
            op_copy["operationId"] = camel_to_snake(op_copy["operationId"])

            # Remove Swagger-specific fields from operation
            remove_swagger_fields(op_copy, ["produces", "consumes"])

            path_item_copy[method] = op_copy

        # Remove Swagger-specific fields from path item
        remove_swagger_fields(path_item_copy, ["produces", "consumes"])

        new_paths[path] = path_item_copy

    return new_paths


def convert_swagger_to_openapi_v3(spec: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0912, PLR0915
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

    # Upgrade to OpenAPI 3.1.1
    spec["openapi"] = "3.1.1"
    spec.pop("swagger", None)

    # Update info: keep version but optionally append to description
    if "info" in spec and isinstance(spec["info"], dict):
        info = dict(spec["info"])
        if "version" in info:
            version = info["version"]  # Don't remove it!
            desc = info.get("description", "")
            # Optionally prepend/append version info to description (kept from original logic)
            if desc:
                info["description"] = f"{desc}\n\nAPI Version: {version}"
        spec["info"] = info

    # Convert basePath to servers
    if "basePath" in spec:
        base_path = spec.pop("basePath")
        host = spec.pop("host", None)
        schemes = spec.pop("schemes", ["http"])

        # Build server URL: schemes://host + basePath
        server_url = ""
        if schemes and host:
            server_url = f"{schemes[0]}://{host}{base_path}"
        elif base_path:
            server_url = base_path

        if server_url:
            spec["servers"] = [{"url": server_url}]

    # Initialize components dict
    components: dict[str, Any] = {}

    # Convert definitions -> components/schemas
    if "definitions" in spec:
        definitions = spec.pop("definitions")
        if isinstance(definitions, dict):
            components["schemas"] = convert_definitions(definitions)

    # Convert responses -> components/responses
    if "responses" in spec:
        responses = spec.pop("responses")
        if isinstance(responses, dict):
            components["responses"] = convert_responses(responses)

    # Convert parameters -> components/parameters
    if "parameters" in spec:
        params = spec.pop("parameters")
        param_list: list[dict[str, Any]]
        if isinstance(params, dict):
            param_list = list(params.values())
        elif isinstance(params, list):
            param_list = params
        else:
            param_list = []
        if param_list:
            converted_params = convert_parameters(param_list)
            # Build a dictionary keyed by parameter name for OpenAPI components
            param_dict = {p["name"]: p for p in converted_params if "name" in p}
            components["parameters"] = param_dict

    # Convert securityDefinitions -> components/securitySchemes
    if "securityDefinitions" in spec:
        sec_defs = spec.pop("securityDefinitions")
        if isinstance(sec_defs, dict):
            security_schemes: dict[str, dict[str, Any]] = {}

            for name, details in sec_defs.items():
                if not isinstance(details, dict):
                    continue

                sec_type = details.get("type", "apiKey").lower()

                if sec_type == "basic":
                    # Basic auth -> type: http, scheme: basic
                    scheme = cast("dict[str, Any]", {"type": "http", "scheme": "basic"})
                elif sec_type == "oauth2":
                    # OAuth2 requires flows object
                    scheme = cast("dict[str, Any]", {"type": "oauth2"})
                    if "flow" in details:
                        flow = details["flow"]
                        # Initialize flows dict explicitly
                        flows: dict[str, Any] = {}
                        scheme["flows"] = flows
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
                else:
                    # apiKey, application, etc.
                    scheme = cast("dict[str, Any]", {"type": details.get("type", "apiKey")})
                    if "name" in details:
                        scheme["name"] = details["name"]
                    if "in" in details:
                        scheme["in"] = details["in"]

                security_schemes[name] = scheme

            if security_schemes:
                components["securitySchemes"] = security_schemes

    if components:
        spec["components"] = components

    # Convert paths (most complex)
    if "paths" in spec:
        spec["paths"] = convert_paths(spec["paths"])

    # Remove deprecated/swagger-specific fields at root level
    remove_swagger_fields(spec, ["consumes", "produces", "schemes"])

    # Fix remaining $ref references throughout the entire spec
    spec = fix_references(spec)

    # Add nullable for optional reference fields to match actual API behavior
    _add_nullable_for_optional_refs(spec)

    logger.info("OpenAPI conversion completed successfully")
    return spec


def _add_nullable_for_optional_refs(spec: dict[str, Any]) -> None:
    """Mutate spec to allow null for optional reference and simple type properties.

    Many Gitea API models include fields that are pointers or can be null, but the
    Swagger spec does not mark these as nullable, causing strict validators to reject
    responses where such fields are present with null value. This function walks through
    all schemas and for any property that is optional (not in required), it makes the
    property schema nullable:
      - If it's a $ref, wrap with anyOf: [{$ref}, {type: 'null'}]
      - If it's a simple type (string, number, integer, boolean, array, object),
        add 'null' to the type array.
    """

    def _process_schema(schema: dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            return

        # Remove email format to allow empty strings (Gitea returns empty string for hidden emails)
        if schema.get("type") == "string" and schema.get("format") == "email":
            schema.pop("format", None)

        # Process properties if present
        props = schema.get("properties")
        if isinstance(props, dict):
            required = set(schema.get("required", []))
            for prop_name, prop_schema in props.items():
                if not isinstance(prop_schema, dict):
                    continue
                # Remove email format to allow empty strings (before any type mutation)
                if prop_schema.get("type") == "string" and prop_schema.get("format") == "email":
                    prop_schema.pop("format", None)
                # Only modify if property is optional
                if prop_name not in required:
                    # Case 1: $ref -> wrap with anyOf
                    if (
                        "$ref" in prop_schema
                        and "anyOf" not in prop_schema
                        and "oneOf" not in prop_schema
                    ):
                        ref = prop_schema["$ref"]
                        props[prop_name] = {"anyOf": [{"$ref": ref}, {"type": "null"}]}
                        # No need to recurse into the new anyOf; the referenced schema will be processed separately
                        continue  # skip further recursion on this prop
                    # Case 2: simple type -> add null
                    elif (
                        "type" in prop_schema
                        and "anyOf" not in prop_schema
                        and "oneOf" not in prop_schema
                    ):
                        t = prop_schema["type"]
                        if isinstance(t, str):
                            if t != "null":
                                prop_schema["type"] = [t, "null"]
                        elif isinstance(t, list) and "null" not in t:
                            t.append("null")
                        # Continue to recurse into nested structures (e.g., if it's an object with properties)
                # Recurse into the property schema to handle nested objects/arrays
                _process_schema(prop_schema)

        # Process nested schemas inside allOf/anyOf/oneOf
        for combo_key in ("allOf", "anyOf", "oneOf"):
            items = schema.get(combo_key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        _process_schema(item)

        # Process items if this is an array schema
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            _process_schema(items_schema)

        # Process additionalProperties if it is a schema dict
        add_props = schema.get("additionalProperties")
        if isinstance(add_props, dict):
            _process_schema(add_props)

        # Process patternProperties
        for key, value in schema.items():
            if key == "patternProperties" and isinstance(value, dict):
                for pat_schema in value.values():
                    if isinstance(pat_schema, dict):
                        _process_schema(pat_schema)

    # Apply to all component schemas
    components = spec.get("components", {})
    schemas = components.get("schemas", {})
    for schema in schemas.values():
        if isinstance(schema, dict):
            _process_schema(schema)

    # Also process top-level schema if any? Usually not needed.
