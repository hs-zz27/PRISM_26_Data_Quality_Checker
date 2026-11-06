"""Shared OpenAPI vocabulary and small schema constructors."""

from typing import Any


BASE_PATH = "/api/v1"
JsonObject = dict[str, Any]


def ref(name: str) -> JsonObject:
    return {"$ref": f"#/components/schemas/{name}"}


def nullable(schema: JsonObject) -> JsonObject:
    return {"anyOf": [schema, {"type": "null"}]}


def array(schema: JsonObject) -> JsonObject:
    return {"type": "array", "items": schema}


def obj(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def choice(*values: str) -> JsonObject:
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string", "minLength": 1}
INTEGER = {"type": "integer"}
COUNT = {"type": "integer", "minimum": 0}
VERSION = {"type": "integer", "minimum": 1}
BOOLEAN = {"type": "boolean"}
TIME = {"type": "string", "format": "date-time"}
FRACTION = {"type": "number", "minimum": 0, "maximum": 1}
JOB_STATUS = choice("queued", "running", "cancelling", "completed", "failed", "cancelled")
SAMPLE_REVIEW_STATUS = choice("pending_review", "needs_correction", "accepted", "rejected")
TOOL_STATUS = choice("pending", "flagged", "unsure", "no_issue", "unavailable")


def replace_model_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: replace_model_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_model_refs(item) for item in value]
    if isinstance(value, str) and value.startswith("#/$defs/"):
        return value.replace("#/$defs/", "#/components/schemas/", 1)
    return value
