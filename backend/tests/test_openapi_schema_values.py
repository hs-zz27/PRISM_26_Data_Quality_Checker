"""Regressions for the pinned FastAPI OpenAPI serialization boundary."""

from typing import Annotated

from fastapi import APIRouter, FastAPI, Query
from pydantic import BaseModel, Field

from backend.app.core.openapi import preserve_schema_values


def test_openapi_retains_exact_integer_bounds_and_null_defaults():
    class Document(BaseModel):
        count: int = Field(ge=0, le=9223372036854775807)
        optional: int | None = None
        metadata: dict = {"description": None}

    app = FastAPI()

    @app.get("/document", response_model=Document)
    def read_document(offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0):
        raise AssertionError("Schema generation must not execute the endpoint.")

    preserve_schema_values(app)
    schema = app.openapi()
    model = schema["components"]["schemas"]["Document"]
    bound = model["properties"]["count"]["maximum"]
    assert type(bound) is int and bound == 9223372036854775807
    assert "optional" not in model["required"]
    assert model["properties"]["optional"]["default"] is None
    assert model["properties"]["metadata"]["default"] == {"description": None}
    offset = schema["paths"]["/document"]["get"]["parameters"][0]["schema"]
    assert type(offset["maximum"]) is int and offset["maximum"] == bound
    assert app.openapi() is schema


def test_openapi_preserves_route_metadata_and_excludes_hidden_routes():
    app = FastAPI(title="Precision example")

    @app.get("/visible", operation_id="visible", tags=["Example"])
    def visible():
        raise AssertionError("Schema generation must not execute the endpoint.")

    @app.get("/hidden", include_in_schema=False)
    def hidden():
        raise AssertionError("Schema generation must not execute the endpoint.")

    preserve_schema_values(app)
    schema = app.openapi()
    assert schema["info"]["title"] == "Precision example"
    assert set(schema["paths"]) == {"/visible"}
    assert schema["paths"]["/visible"]["get"]["operationId"] == "visible"
    assert schema["paths"]["/visible"]["get"]["tags"] == ["Example"]


def test_openapi_edits_do_not_mutate_shared_router_metadata():
    router = APIRouter()
    body = {
        "required": True,
        "content": {"multipart/form-data": {"schema": {
            "type": "object", "properties": {"archive": {"type": "string", "format": "binary"}},
        }}},
    }

    @router.post("/upload", openapi_extra={"requestBody": body})
    def upload():
        raise AssertionError("Schema generation must not execute the endpoint.")

    def application():
        app = FastAPI()
        app.include_router(router)
        preserve_schema_values(app)
        return app

    first = application().openapi()
    first["paths"]["/upload"]["post"]["requestBody"]["content"] = {"application/json": {}}
    second = application().openapi()
    assert second["paths"]["/upload"]["post"]["requestBody"] == body
    assert set(body["content"]) == {"multipart/form-data"}
