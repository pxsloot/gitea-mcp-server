"""Convert Swagger 2.0 spec to OpenAPI 3.1.

Public API
----------
The only public function is ``convert_swagger_to_openapi_v3``.
All other names are implementation details of the converter pipeline.
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
    camel_to_snake,
    convert_definitions,
    convert_parameters,
    convert_paths,
    convert_responses,
    convert_schema,
    convert_swagger_to_openapi_v3,
    fix_references,
    remove_swagger_fields,
    resolve_spec_ref,
)
from gitea_mcp_server.openapi_converter.normalize import normalize_spec
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
    "camel_to_snake",
    "convert_definitions",
    "convert_parameters",
    "convert_paths",
    "convert_responses",
    "convert_schema",
    "convert_swagger_to_openapi_v3",
    "fix_references",
    "normalize_spec",
    "remove_swagger_fields",
    "resolve_param_collisions",
    "resolve_spec_ref",
]
