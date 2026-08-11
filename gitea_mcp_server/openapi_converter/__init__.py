"""Convert Swagger 2.0 spec to OpenAPI 3.1.

Public API
----------
The only public function is ``convert_swagger_to_openapi_v3``.
All other names are implementation details re-exported here for
backward compatibility. They may change without notice.
"""

from gitea_mcp_server.openapi_converter.core import (
    SCHEMA_FIELDS,
    BasePathToServerConverter,
    OperationIdFormatter,
    OperationTransformer,
    PathsConverter,
    ReferenceFixer,
    RequestBodyBuilder,
    SecuritySchemeConverter,
    SpecVersionUpdater,
    _add_nullable_for_optional_refs,
    _convert_components,
    _determine_content_type,
    _resolve_spec_ref,
    _response_is_contents_base64,
    _update_info_version,
    _validate_spec,
    _wrap_response_schema,
    _wrap_success_response_schemas,
    camel_to_snake,
    convert_definitions,
    convert_parameters,
    convert_paths,
    convert_responses,
    convert_schema,
    convert_swagger_to_openapi_v3,
    fix_references,
    remove_swagger_fields,
)
from gitea_mcp_server.openapi_converter.param_collision import resolve_param_collisions
from gitea_mcp_server.openapi_converter.schema import (
    OptionalPropertyTransformer,
    PropertyRequiredCollector,
    SchemaCallback,
    SchemaNormalizer,
    SchemaWalker,
)

__all__ = [
    "SCHEMA_FIELDS",
    "BasePathToServerConverter",
    "OperationIdFormatter",
    "OperationTransformer",
    "OptionalPropertyTransformer",
    "PathsConverter",
    "PropertyRequiredCollector",
    "ReferenceFixer",
    "RequestBodyBuilder",
    "SchemaCallback",
    "SchemaNormalizer",
    "SchemaWalker",
    "SecuritySchemeConverter",
    "SpecVersionUpdater",
    "_convert_components",
    "_resolve_spec_ref",
    "_response_is_contents_base64",
    "_wrap_response_schema",
    "_wrap_success_response_schemas",
    "camel_to_snake",
    "convert_definitions",
    "convert_parameters",
    "convert_paths",
    "convert_responses",
    "convert_schema",
    "convert_swagger_to_openapi_v3",
    "fix_references",
    "remove_swagger_fields",
    "resolve_param_collisions",
]
