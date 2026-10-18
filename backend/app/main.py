"""Local audit review API. Schema migrations remain an explicit setup step."""

from fastapi import FastAPI

from .routers.audits import router as audits_router
from .routers.errors import register_error_handlers
from .routers.finding_reviews import router as finding_reviews_router
from .routers.sample_reviews import router as sample_reviews_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="PRISM audit review API", version="0.1.0",
        description=(
            "Audit sample reads, finding and sample review decisions, and review history are implemented. "
            "Other operations in backend/api remain planned."
        ),
    )
    register_error_handlers(app)
    app.include_router(audits_router, prefix="/api/v1")
    app.include_router(finding_reviews_router, prefix="/api/v1")
    app.include_router(sample_reviews_router, prefix="/api/v1")
    return app


app = create_app()
