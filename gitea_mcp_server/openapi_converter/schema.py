"""Schema walker and transformation utilities for OpenAPI spec processing."""

from typing import Any, ClassVar, Protocol


class SchemaCallback(Protocol):
    """Protocol for schema walker callbacks."""

    def __call__(
        self, schema: dict[str, Any], parent: dict[str, Any] | None, key: str | None
    ) -> None: ...


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


class SchemaWalker:
    """Iterative schema walker for applying transformations."""

    # Schemas whose value is a dict - iterate over entries, push each sub-schema
    _DICT_ITER_KEYS = ("properties", "patternProperties")
    # Schemas whose value is a single dict - push directly
    _SINGLE_DICT_KEYS = ("items", "additionalProperties")
    # Schemas whose value is a list of schemas - iterate, push each
    _LIST_ITER_KEYS = ("allOf", "anyOf", "oneOf")

    def __init__(self, callback: SchemaCallback):
        self.callback = callback

    def _push_child_schemas(self, stack: list, current_schema: dict[str, Any]) -> None:
        """Push all child schemas onto the stack for further processing."""
        for key in self._DICT_ITER_KEYS:
            children = current_schema.get(key)
            if isinstance(children, dict):
                for name, child in children.items():
                    if isinstance(child, dict):
                        stack.append((child, current_schema, name))

        for key in self._SINGLE_DICT_KEYS:
            child = current_schema.get(key)
            if isinstance(child, dict):
                stack.append((child, current_schema, key))

        for key in self._LIST_ITER_KEYS:
            items = current_schema.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        stack.append((item, current_schema, key))

    def walk(self, schema: dict[str, Any]) -> None:
        """Walk the schema tree iteratively and apply callback to each schema node."""
        stack: list[tuple[dict[str, Any], dict[str, Any] | None, str | None]] = [
            (schema, None, None)
        ]

        while stack:
            current_schema, parent, key = stack.pop()

            self.callback(current_schema, parent, key)

            self._push_child_schemas(stack, current_schema)


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


class OptionalPropertyTransformer:
    """Add null to optional properties and handle special format cases (e.g., email)."""

    FORMATS_NEEDING_EMPTY: ClassVar[frozenset[str]] = frozenset({"email"})

    def _is_property_schema(self, parent: dict[str, Any], key: str) -> bool:
        """Check if this schema represents a property-like schema."""
        return (
            ("properties" in parent and key in parent["properties"])
            or ("patternProperties" in parent and key in parent["patternProperties"])
            or (key == "additionalProperties" and "additionalProperties" in parent)
        )

    def _is_optional_property(self, parent: dict[str, Any], key: str) -> bool:
        """Check if this property is optional (not in required list)."""
        if "properties" not in parent or key not in parent["properties"]:
            return True
        required = parent.get("required", [])
        return key not in required

    def _transform_special_format(self, schema: dict[str, Any], optional: bool) -> None:
        """Handle special formats (e.g., email) - transform to anyOf."""
        fmt = schema.get("format", "email")
        format_branch = {"type": "string", "format": fmt}
        for k in ("pattern", "minLength", "maxLength", "enum", "default"):
            if k in schema:
                format_branch[k] = schema[k]

        any_of = [format_branch]
        if optional:
            any_of.append({"type": "string", "maxLength": 0})
            any_of.append({"type": "null"})

        new_schema = {"anyOf": any_of}
        for k in ("description", "title", "example", "readOnly", "writeOnly", "deprecated"):
            if k in schema:
                new_schema[k] = schema[k]

        schema.clear()
        schema.update(new_schema)

    def _add_nullable(self, schema: dict[str, Any]) -> None:
        """Add nullable type to schema for optional properties."""
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

    def __call__(
        self, schema: dict[str, Any], parent: dict[str, Any] | None, key: str | None
    ) -> None:
        if parent is None or key is None:
            return

        if not self._is_property_schema(parent, key):
            return

        optional = self._is_optional_property(parent, key)

        if schema.get("type") == "string" and schema.get("format") in self.FORMATS_NEEDING_EMPTY:
            self._transform_special_format(schema, optional)
            return

        if optional:
            self._add_nullable(schema)
