"""Independent synthetic catalogue snapshots, validated against public models.

These are not importer executions, parser results or stored resources. No files,
archive decoding, database, model inference or environment settings are accessed.
"""
from copy import deepcopy
import json
from urllib.parse import quote

from backend.app.core.dataset_import import ERROR_MESSAGES
from backend.app.models.dataset import Dataset, DatasetImportStarted, Sample, source_issue
from backend.app.models.job import Job, JobStatus

TIMESTAMP = "2026-10-20T10:00:00Z"
DATASET_ID = "dataset_import_demo"
JOB_ID = "job_dataset_import_demo"


def validated(model, document):
    """JSON-mode validation preserves strict integer/tuple/datetime semantics."""
    return model.model_validate_json(json.dumps(document, allow_nan=False)).model_dump(mode="json")


def _assets(sample_id, *, image=True, mask=True):
    return {
        kind: {"url": (f"/api/v1/datasets/{quote(DATASET_ID, safe='')}/samples/"
                       f"{quote(sample_id, safe='')}/assets/{kind}?revision=1"),
               "content_type": "image/png", "revision": 1} if available else None
        for kind, available in (("image", image), ("thumbnail", image),
                                ("mask", mask), ("mask_preview", mask))
    }


def _sample(name, *, objects=(), issues=(), image=True, mask=True, image_size=(4, 3)):
    return validated(Sample, {
        "sample_id": name, "dataset_id": DATASET_ID, "revision": 1,
        "image_size": image_size, "integrity_status": "invalid" if issues else "valid",
        "objects": list(objects), "assets": _assets(name, image=image, mask=mask),
        "issues": [source_issue(code, field=field).model_dump(mode="json") for code, field in issues],
    })


def _pair(state, *, code=None, invalid=False, before_discovery=False):
    unknown = state == "queued" or before_discovery
    processed = 0 if unknown else 6 if state == "completed" else 1
    status = {"completed": "ready", "failed": "invalid" if invalid else "failed",
              "cancelled": "cancelled"}.get(state, "importing")
    issues = []
    if code:
        issue_code, field = {
            "INVALID_ARCHIVE": ("invalid_archive", "archive"),
            "INVALID_LAYOUT": ("invalid_layout", "layout"),
            "IMPORT_RESOURCE_LIMIT": ("resource_limit", None),
        }.get(code, ("import_failed", None))
        issues = [source_issue(issue_code, field=field).model_dump(mode="json")]
    dataset = validated(Dataset, {
        "dataset_id": DATASET_ID, "name": "Synthetic dirty-source dataset", "format": "penn_fudan",
        "status": status, "sample_count": None if unknown else 6, "object_count": None,
        "import_job_id": JOB_ID, "created_at": TIMESTAMP, "issues": issues,
    })
    job = validated(Job, {
        "job_id": JOB_ID, "kind": "dataset_import", "status": state,
        "created_at": TIMESTAMP, "updated_at": TIMESTAMP,
        "progress": {"phase": "importing" if state in {"running", "cancelling"} and not unknown else state,
                     "processed": processed, "total": None if unknown else 6,
                     "fraction": None if unknown else processed / 6},
        "result": {"kind": "dataset", "id": DATASET_ID, "version": None,
                   "href": f"/api/v1/datasets/{DATASET_ID}"} if state == "completed" else None,
        "error": {"code": code, "message": ERROR_MESSAGES[code], "details": [], "request_id": JOB_ID}
                 if code else None,
    })
    return {"dataset": dataset, "job": job}


def build_dataset_import_scenarios():
    states = {state.value: _pair(state.value, code="IMPORT_FAILED" if state is JobStatus.FAILED else None)
              for state in JobStatus}
    # The acceptance wrapper deliberately represents only queued/unknown work.
    started = validated(DatasetImportStarted, states["queued"])
    object_one = {"object_id": 1, "label": "person", "bbox": [1, 1, 2, 2]}
    samples = {
        "zero_object": _sample("zero_object"),
        "annotation_only": _sample("annotation_only", objects=[object_one], image=False, mask=False,
                                   image_size=None, issues=[("missing_image", "image"), ("missing_mask", "mask")]),
        "missing_mask": _sample("missing_mask", objects=[object_one], mask=False,
                                issues=[("missing_mask", "mask")]),
        "corrupt_image": _sample("corrupt_image", objects=[object_one], image_size=None,
                                 issues=[("invalid_image", "image")]),
        "malformed_annotation": _sample("malformed_annotation", issues=[("invalid_annotation", "annotation")]),
        "invalid_objects": _sample("invalid_objects", objects=[
            {"object_id": 0, "label": "person", "bbox": [-3, 4, 1, -2]},
            {"object_id": 0, "label": "person", "bbox": [4, 3, 1, 1]},
        ], issues=[("invalid_object", "annotation.objects")]),
    }
    return {
        "notice": ("Independent synthetic snapshots, not importer outputs. Ready does not mean clean data. "
                   "Failed/cancelled imports retain only revisions already published; progress is not readiness."),
        "started": started, "states": states, "samples": samples,
        "invalid": {code: _pair("failed", code=code, invalid=True, before_discovery=True)
                    for code in ("INVALID_ARCHIVE", "INVALID_LAYOUT")},
        "failures": {code: _pair("failed", code=code) for code in ERROR_MESSAGES
                     if code not in {"INVALID_ARCHIVE", "INVALID_LAYOUT"}},
        "queued_cancelled": _pair("cancelled", before_discovery=True),
        "annotation_evidence": {
            "notice": "Illustrative source text only, not part of the Sample transport or an executed parser check.",
            "zero_object": {"text": 'Image size (X x Y x C) : 4 x 3 x 3\nObjects with ground truth : 0 {}\n# Top left pixel co-ordinates : (1, 1)\n',
                            "annotation_object_count": 0},
            "malformed_annotation": {"text": "not a recognized annotation\n", "annotation_object_count": None},
        },
        "partial_sample": deepcopy(samples["annotation_only"]),
    }
