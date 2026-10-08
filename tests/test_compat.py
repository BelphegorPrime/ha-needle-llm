"""Compatibility smoke tests for supported Home Assistant releases."""

from custom_components.needle_llm.compat import (
    route_parameters_schema,
    schema_to_json_schema,
)


def test_route_schema_serializes() -> None:
    """The public router schema serializes on this HA version."""
    schema = route_parameters_schema()
    converted = schema_to_json_schema(schema)

    assert converted["type"] == "object"
    assert converted["properties"]["query"]["type"] == "string"
    assert "query" in converted.get("required", [])
