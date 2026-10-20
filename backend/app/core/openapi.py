"""Preserve exact Pydantic schema values in FastAPI's public OpenAPI document.

FastAPI 0.143.5 passes JSON Schema through its OpenAPI models, which coerce
integer bounds to floats and remove explicit null defaults. Reuse its own raw
route/schema generation to retain PostgreSQL BIGINT limits and nullable defaults.
This adapter follows the pinned FastAPI API and does no storage or request work.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import (
    get_definitions,
    get_fields_from_routes,
    get_flat_models_from_fields,
    get_model_name_map,
    get_openapi_path,
)
from fastapi.routing import APIRoute, iter_route_contexts


def preserve_schema_values(app: FastAPI) -> None:
    """Install the normal lazy cache with lossless generated paths/components."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        document = generate()
        try:
            fields = get_fields_from_routes(app.routes)
            models = get_flat_models_from_fields(fields, known_models=set())
            names = get_model_name_map(models)
            mapping, definitions = get_definitions(
                fields=fields,
                model_name_map=names,
                separate_input_output_schemas=app.separate_input_output_schemas,
            )
            paths = {}
            operation_ids = set()
            for context in iter_route_contexts(app.routes):
                if not isinstance(context.original_route, APIRoute):
                    continue
                generated = get_openapi_path(
                    route=context,
                    operation_ids=operation_ids,
                    model_name_map=names,
                    field_mapping=mapping,
                    separate_input_output_schemas=app.separate_input_output_schemas,
                )
                if generated is None:
                    continue
                path, _, extra_definitions = generated
                if path:
                    paths.setdefault(context.path_format, {}).update(path)
                definitions.update(extra_definitions)
            document["paths"] = paths
            document.setdefault("components", {}).setdefault("schemas", {}).update(definitions)
        except Exception:
            app.openapi_schema = None
            raise
        app.openapi_schema = document
        return document

    app.openapi = openapi
