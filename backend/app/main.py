"""Local audit review API. Schema migrations remain an explicit setup step."""

from fastapi import FastAPI

from .core.openapi import preserve_schema_values
from .routers.feedback import router as feedback_router,register_feedback_errors
from .routers.corrections import router as corrections_router, register_correction_errors
from .routers.correction_ingress import CorrectionIngress
from .routers.rechecks import router as rechecks_router
from .routers.audits import router as audits_router
from .routers.assets import router as assets_router
from .routers.datasets import router as datasets_router
from .routers.dataset_ingress import DatasetUploadIngress
from .routers.errors import register_error_handlers
from .routers.exports import router as exports_router, register_export_error_handlers
from .routers.finding_reviews import router as finding_reviews_router
from .routers.jobs import router as jobs_router
from .routers.sample_reviews import router as sample_reviews_router
from .routers.rulebooks import router as rulebooks_router
from .routers.system import router as system_router

def create_app(*, dataset_upload_settings_factory=None, correction_settings_factory=None) -> FastAPI:
    app = FastAPI(
        title="PRISM audit review API", version="0.1.0",
        description=(
            "Audit creation, listing and sample reads, revision-pinned asset delivery, job status and audit-aware cancellation, finding and sample review decisions, "
            "review history, durable plain-text extraction submission and manual rulebook creation/reads/reviewer actions are implemented. "
            "Dataset upload acceptance and catalogue reads are implemented; the import worker is explicit. "
            "Reviewed dataset export acceptance, listing, metadata, manifest and ZIP download are implemented; export processing uses an explicit worker. "
            "Annotation/mask correction, revision history and linked snapshot rechecks are implemented; rechecks use the explicit audit worker. "
            "Reviewed-example policies, adjudicated truth, held-out comparison jobs and explicit activation are implemented in source. "
            "Liveness, capabilities and local readiness reads are implemented. "
            "Other operations in backend/api remain planned."
        ),
    )
    register_error_handlers(app)
    register_feedback_errors(app)
    register_export_error_handlers(app)
    register_correction_errors(app)
    app.add_middleware(CorrectionIngress, settings_factory=correction_settings_factory)
    app.add_middleware(DatasetUploadIngress, settings_factory=dataset_upload_settings_factory)
    app.include_router(audits_router, prefix="/api/v1")
    app.include_router(feedback_router,prefix="/api/v1")
    app.include_router(assets_router, prefix="/api/v1")
    app.include_router(datasets_router, prefix="/api/v1")
    app.include_router(exports_router, prefix="/api/v1")
    app.include_router(corrections_router, prefix="/api/v1")
    app.include_router(rechecks_router, prefix="/api/v1")
    app.include_router(finding_reviews_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(sample_reviews_router, prefix="/api/v1")
    app.include_router(rulebooks_router, prefix="/api/v1")
    app.include_router(system_router, prefix="/api/v1")
    preserve_schema_values(app)
    return app

app = create_app()
