"""Align only the five dataset operations with live OpenAPI, failing on drift.

No requests, repository calls or file reads. Descriptions/examples are the only
annotations ignored for schema comparison; constraints/defaults remain exact.
"""
from copy import deepcopy

from .common import BASE_PATH

DATASET_OPERATIONS = {
    "importDataset": ("/datasets", "post"),
    "listDatasets": ("/datasets", "get"),
    "getDataset": ("/datasets/{dataset_id}", "get"),
    "listDatasetSamples": ("/datasets/{dataset_id}/samples", "get"),
    "getDatasetSample": ("/datasets/{dataset_id}/samples/{sample_id}", "get"),
}
_ANNOTATIONS = {"title", "description", "example", "examples"}
_PREFIX = "#/components/schemas/"


def structural(value, *, field_map=False):
    """Keep field names and literal defaults, even when called title/description."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not field_map and key in _ANNOTATIONS:
                continue
            if not field_map and key in {"default", "const", "enum"}:
                result[key] = deepcopy(item)
            else:
                result[key] = structural(item, field_map=(not field_map and key in {
                    "properties", "patternProperties", "$defs", "definitions", "dependentSchemas",
                }))
        return result
    if isinstance(value, list):
        return [structural(item) for item in value]
    return value


def referenced_components(value, *, field_map=False):
    found = set()
    if isinstance(value, dict):
        target = None if field_map else value.get("$ref")
        if target is not None:
            if not isinstance(target, str) or not target.startswith(_PREFIX):
                raise ValueError("Dataset transport contains an unsupported schema reference.")
            found.add(target[len(_PREFIX):])
        for key, item in value.items():
            if not field_map and key in {"example", "examples", "default", "const", "enum"}:
                continue
            found.update(referenced_components(item, field_map=(not field_map and key in {
                "properties", "patternProperties", "$defs", "definitions", "dependentSchemas",
            })))
    elif isinstance(value, list):
        for item in value:
            found.update(referenced_components(item))
    return found


def schema_closure(operation, schemas):
    pending = referenced_components(operation)
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in schemas:
            raise ValueError("Dataset transport has an unresolved schema reference.")
        selected[name] = schemas[name]
        pending.update(referenced_components(schemas[name]) - selected.keys())
    return selected


def align_dataset_contract(reference, live):
    """Promote existing declarations, never append/replace unrelated operations.

    Validate every collision before changing the reference. Live parameters,
    request bodies, responses and reachable components supply actual constraints.
    Hand-authored descriptions and safe examples remain attached to those shapes.
    """
    replacements = {}
    components = {}
    documented_schemas = reference["components"]["schemas"]
    live_schemas = live.get("components", {}).get("schemas", {})
    for identifier, (path, method) in DATASET_OPERATIONS.items():
        documented = reference["paths"][path][method]
        actual = live["paths"][BASE_PATH + path][method]
        if (documented["operationId"] != identifier or actual["operationId"] != identifier
                or documented["x-implementation-status"] != "implemented"):
            raise ValueError("Dataset operation identity or promotion disagrees with live routes.")
        if set(documented["responses"]) != set(actual["responses"]):
            raise ValueError("Dataset response status inventory disagrees with live routes.")
        operation = deepcopy(actual)
        for key in ("summary", "description", "x-implementation-status", "x-backing-service-status"):
            operation[key] = deepcopy(documented[key])
        docs = {(item["in"], item["name"]): item for item in documented.get("parameters", [])}
        for item in operation.get("parameters", []):
            matching = docs.get((item["in"], item["name"]), {})
            for key in ("example", "description"):
                if key in matching:
                    item[key] = deepcopy(matching[key])
        for status, response in operation["responses"].items():
            source = documented["responses"][status].get("content", {})
            if set(response.get("content", {})) != set(source):
                raise ValueError("Dataset response media inventory disagrees with live routes.")
            for media_type, media in response.get("content", {}).items():
                for key in ("example", "examples"):
                    media.pop(key, None)
                    if key in source[media_type]:
                        media[key] = deepcopy(source[media_type][key])
        actual_body = operation.get("requestBody", {}).get("content", {})
        expected_body = documented.get("requestBody", {}).get("content", {})
        if set(actual_body) != set(expected_body):
            raise ValueError("Dataset request media inventory disagrees with live routes.")
        for media_type, media in actual_body.items():
            if "example" in expected_body[media_type]:
                media["example"] = deepcopy(expected_body[media_type]["example"])
        # Traverse the actual shape, not embedded example documents (which could
        # legitimately contain a source annotation string resembling a reference).
        shapes = {"parameters": actual.get("parameters", []),
                  "requestBody": actual.get("requestBody", {}), "responses": actual["responses"]}
        for name, schema in schema_closure(shapes, live_schemas).items():
            if name in documented_schemas and structural(documented_schemas[name]) != structural(schema):
                raise ValueError("Dataset component collision changes transport constraints: " + name)
            if name in components and structural(components[name]) != structural(schema):
                raise ValueError("Dataset live component definitions disagree: " + name)
            components[name] = deepcopy(schema)
        replacements[path, method] = operation
    documented_schemas.update(components)
    for (path, method), operation in replacements.items():
        reference["paths"][path][method] = operation
