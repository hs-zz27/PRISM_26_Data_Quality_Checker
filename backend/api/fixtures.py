"""Deterministic synthetic frontend fixtures, including actual sample measurements."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from PIL import Image

from backend.app.models.rule import ConfirmationState, Rule, Rulebook, RuleOrigin
from backend.app.models.rulebook_creation import RulebookCreate, RulebookCreateRule
from backend.app.models.audit import AuditRun
from backend.app.models.job import ApiError, Job, JobKind, JobStatus, Progress, ResultReference
from backend.app.services.audit import audit_sample
from backend.app.models.dataset import Dataset, Sample

from .dataset_examples import build_dataset_import_scenarios, validated

from .common import BASE_PATH, JsonObject

TIMESTAMP = "2026-10-14T10:00:00Z"
SOURCE = "Allowed labels are person."
EXAMPLE_MODEL = "example-local-model:latest"

def page(items: list[Any]) -> JsonObject:
    return {"items": items, "limit": 20, "offset": 0, "total": len(items), "next_offset": None}

def job(kind: str, resource_kind: str, resource_id: str, *, completed: bool = False) -> JsonObject:
    collection = {
        "rulebook": "rulebooks", "dataset": "datasets", "audit": "audits",
        "export": "exports", "feedback_evaluation": "feedback/evaluations",
    }[resource_kind]
    return {
        "job_id": f"job_{kind}_demo", "kind": kind,
        "status": "completed" if completed else "queued",
        "progress": {"phase": "completed" if completed else "queued", "processed": 1 if completed else 0, "total": 1, "fraction": 1.0 if completed else 0.0},
        "result": {"kind": resource_kind, "id": resource_id, "version": 1 if resource_kind == "rulebook" else None,
                   "href": f"{BASE_PATH}/{collection}/{resource_id}" + ("?version=1" if resource_kind == "rulebook" else "")} if completed else None,
        "error": None, "created_at": TIMESTAMP, "updated_at": TIMESTAMP,
    }

def build_future_job_scenarios() -> JsonObject:
    """Model-validated lifecycle mocks for the still-planned job producers."""
    targets = {
        "export": ("export", "export_demo", "EXPORT_FAILED"),
        "feedback_evaluation": ("feedback_evaluation", "evaluation_demo", "FEEDBACK_EVALUATION_FAILED"),
    }
    scenarios = {}
    for kind, (resource_kind, resource_id, failure_code) in targets.items():
        states = {}
        for status in JobStatus:
            value = job(kind, resource_kind, resource_id, completed=status is JobStatus.COMPLETED)
            value["status"] = status.value
            value["progress"]["phase"] = status.value
            if status is JobStatus.FAILED:
                value["error"] = {
                    "code": failure_code, "message": "The requested work could not be completed.",
                    "details": [], "request_id": "request_demo",
                }
            states[status.value] = Job.model_validate_json(json.dumps(value)).model_dump(mode="json")
        scenarios[kind] = states
    return scenarios

def build_extraction_job_scenarios() -> JsonObject:
    """Delivered extraction lifecycle plus every supported safe execution failure."""
    from backend.app.storage.extraction_repository import ERROR_MESSAGES
    states = {}
    for status in JobStatus:
        value = job("rule_extraction", "rulebook", "rulebook_demo", completed=status is JobStatus.COMPLETED)
        value["status"] = status.value
        value["progress"]["phase"] = status.value
        if status is JobStatus.FAILED:
            value["error"] = {"code": "MODEL_UNAVAILABLE", "message": ERROR_MESSAGES["MODEL_UNAVAILABLE"],
                              "details": [], "request_id": "request_demo"}
        states[status.value] = Job.model_validate_json(json.dumps(value)).model_dump(mode="json")
    failures = {}
    for code, message in ERROR_MESSAGES.items():
        value = deepcopy(states["failed"])
        value["error"] = {"code": code, "message": message, "details": [], "request_id": "request_demo"}
        failures[code] = Job.model_validate_json(json.dumps(value)).model_dump(mode="json")
    return {"states": states, "failures": failures}

def build_lifecycle_scenarios(rulebook_version: int) -> tuple[JsonObject, JsonObject]:
    """Create independent, model-validated job/audit snapshots for frontend states."""
    timestamp = datetime.fromisoformat(TIMESTAMP)
    jobs, audits = {}, {}
    for status in JobStatus:
        processed = 0 if status is JobStatus.QUEUED else 2 if status is JobStatus.COMPLETED else 1
        result = ResultReference(kind="audit", id="audit_demo", href=f"{BASE_PATH}/audits/audit_demo")
        error = ApiError(
            code="AUDIT_FAILED", message="The audit worker failed before the batch completed.",
            request_id="request_demo",
        ) if status is JobStatus.FAILED else None
        jobs[status.value] = Job(
            job_id="job_audit_demo", kind=JobKind.AUDIT, status=status,
            progress=Progress(phase=status.value, processed=processed, total=2, fraction=processed / 2),
            result=result if status in (JobStatus.COMPLETED, JobStatus.CANCELLED) else None,
            error=error, created_at=timestamp, updated_at=timestamp,
        ).model_dump(mode="json")
        audits[status.value] = AuditRun(
            audit_id="audit_demo", job_id="job_audit_demo", dataset_id="dataset_demo",
            rulebook_id="rulebook_demo", rulebook_version=rulebook_version,
            source_mode="snapshot", status=status, sample_count=2, processed_count=processed,
            failed_count=1 if status is JobStatus.FAILED else 0, incomplete_count=0,
            created_at=timestamp, updated_at=timestamp,
        ).model_dump(mode="json")
    return jobs, audits

def build_examples() -> JsonObject:
    """Produce synthetic examples, including measurements from the actual service."""
    draft_rule = Rule(rule_id="LBL-1", description="Use an allowed label.", check_name="allowed_labels",
                      parameters={"allowed_labels": ["person"]}, origin=RuleOrigin.EXTRACTED, source_sentence=SOURCE)
    draft = Rulebook(rulebook_id="rulebook_demo", guideline_text=SOURCE, rules=[draft_rule]).model_dump(mode="json")
    manual_rule = Rule(**{
        **draft_rule.model_dump(), "origin": RuleOrigin.REVIEWER, "source_sentence": None,
    })
    manual_draft = Rulebook(
        rulebook_id="rulebook_manual_demo", guideline_text=SOURCE, rules=[manual_rule],
    ).model_dump(mode="json")
    manual_create = RulebookCreate(
        guideline_text=SOURCE,
        rules=[RulebookCreateRule(
            rule_id=manual_rule.rule_id, description=manual_rule.description,
            check_name=manual_rule.check_name, parameters=manual_rule.parameters,
            source_sentence=manual_rule.source_sentence,
        )],
    ).model_dump(mode="json")
    confirmed_rule = Rule(**{**draft_rule.model_dump(), "confirmation_state": ConfirmationState.CONFIRMED})
    reviewed = Rulebook(
        rulebook_id="rulebook_demo", version=2, guideline_text=SOURCE, rules=[confirmed_rule],
    ).model_dump(mode="json")
    confirmed = Rulebook(
        **{**reviewed, "version": 3, "confirmation_state": ConfirmationState.CONFIRMED},
    ).model_dump(mode="json")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for folder in ("PNGImages", "Annotation", "PedMasks"):
            (root / folder).mkdir()
        Image.new("RGB", (4, 3)).save(root / "PNGImages/sample_001.png")
        mask = np.array([[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 0, 2]], dtype=np.uint8)
        Image.fromarray(mask).save(root / "PedMasks/sample_001_mask.png")
        (root / "Annotation/sample_001.txt").write_text(
            'Image size (X x Y x C) : 4 x 3 x 3\n'
            'Objects with ground truth : 2 { "person" "vehicle" }\n'
            '# Top left pixel co-ordinates : (1, 1)\n'
            'Bounding box for object 1 "person" (Xmin, Ymin) - (Xmax, Ymax) : (1, 1) - (2, 2)\n'
            'Bounding box for object 2 "vehicle" (Xmin, Ymin) - (Xmax, Ymax) : (4, 3) - (4, 3)\n',
            encoding="utf-8",
        )
        measurements = json.loads(json.dumps(audit_sample(root, "sample_001", ["person"])))
        path = root / "Annotation/sample_001.txt"
        path.write_text(path.read_text(encoding="utf-8").replace('"vehicle"', '"person"'), encoding="utf-8")
        correct_measurements = json.loads(json.dumps(audit_sample(root, "sample_001", ["person"])))
    annotations = [row["annotation"] for row in measurements["objects"]]
    assets = {kind: {"url": f"{BASE_PATH}/datasets/dataset_demo/samples/sample_001/assets/{kind}?revision=1",
                      "content_type": "image/png", "revision": 1}
              for kind in ("image", "mask", "mask_preview", "thumbnail")}
    sample = {"sample_id": "sample_001", "dataset_id": "dataset_demo", "revision": 1,
              "image_size": [4, 3], "integrity_status": "valid", "objects": annotations, "assets": assets, "issues": []}
    dataset = {"dataset_id": "dataset_demo", "name": "Demo dataset", "format": "penn_fudan", "status": "ready",
               "sample_count": 1, "object_count": 2, "import_job_id": "job_dataset_import_demo", "created_at": TIMESTAMP, "issues": []}
    sample = validated(Sample, sample)
    dataset = validated(Dataset, dataset)
    audit_timestamp = datetime.fromisoformat(TIMESTAMP)
    audit_run = AuditRun(
        audit_id="audit_demo", job_id="job_audit_demo", dataset_id="dataset_demo",
        rulebook_id="rulebook_demo", rulebook_version=confirmed["version"],
        source_mode="snapshot", status=JobStatus.COMPLETED, sample_count=1, processed_count=1,
        failed_count=0, incomplete_count=0,
        created_at=audit_timestamp, updated_at=audit_timestamp,
    ).model_dump(mode="json")
    audit = deepcopy(audit_run)
    finding = {
        "finding_id": "finding_demo", "audit_id": "audit_demo", "sample_id": "sample_001", "sample_revision": 1,
        "object_id": 2, "rulebook_id": "rulebook_demo", "rulebook_version": confirmed["version"], "rule_id": "LBL-1",
        "description": "Use an allowed label.", "check_name": "allowed_labels", "outcome": "fail",
        "reason": "label_not_allowed", "parameters": {"allowed_labels": ["person"]}, "origin": "extracted", "source_sentence": SOURCE,
        "evidence": {"image_url": None, "mask_preview_url": None, "crop_url": None,
                     "measurements": {"actual_label": "vehicle", "allowed_labels": ["person"]}},
        "review": {"revision": 0, "resolution": "pending", "reviewer_id": None, "comment": None, "updated_at": None},
    }
    summary = {"sample_id": "sample_001", "sample_revision": 1, "tool_status": "flagged", "review_status": "pending_review",
               "processing_status": "completed", "incomplete": False,
               "finding_counts": {"pass": 1, "fail": 1, "unsure": 0, "unavailable": 0},
               "thumbnail_url": assets["thumbnail"]["url"]}
    passing = deepcopy(finding)
    passing.update(finding_id="finding_pass_demo", object_id=1, outcome="pass", reason=None)
    passing["evidence"]["measurements"]["actual_label"] = "person"
    sample_review = {"audit_id": "audit_demo", "sample_id": "sample_001", "sample_revision": 1,
                     "revision": 0, "decision": "pending_review", "reviewer_id": None, "comment": None, "updated_at": None}
    detail = {"audit_id": "audit_demo", "dataset_id": "dataset_demo", "summary": summary,
              "assets": assets, "failures": [],
              "measurements": measurements, "findings": [passing, finding], "review": sample_review}
    legacy = deepcopy(detail)
    legacy["assets"] = dict.fromkeys(assets)
    legacy["summary"]["thumbnail_url"] = None
    no_issue = deepcopy(detail)
    no_issue["summary"].update(tool_status="no_issue", finding_counts={"pass": 2, "fail": 0, "unsure": 0, "unavailable": 0})
    no_issue["measurements"] = correct_measurements
    no_issue["findings"] = [deepcopy(passing), {**deepcopy(passing), "finding_id": "finding_pass_second_demo", "object_id": 2}]
    uncertain = deepcopy(no_issue)
    uncertain_finding = deepcopy(passing)
    uncertain_finding.update(finding_id="finding_uncertain_demo", rule_id="POSTURE-1", object_id=1,
                             description="The activity label should match visible posture.", check_name="person_posture",
                             outcome="unsure", reason="ambiguous_posture", origin="reviewer", source_sentence=None, parameters={})
    uncertain_finding["evidence"]["measurements"] = {"model_result": "uncertain"}
    uncertain["findings"].append(uncertain_finding)
    uncertain["summary"].update(tool_status="unsure", finding_counts={"pass": 2, "fail": 0, "unsure": 1, "unavailable": 0})
    unavailable = deepcopy(no_issue)
    blocked_finding = deepcopy(uncertain_finding)
    blocked_finding.update(finding_id="finding_unavailable_demo", rule_id="CUSTOM-1", object_id=None,
                           description="Custom reviewer requirement without a supported check.", check_name=None,
                           outcome="unavailable", reason="unsupported_check")
    blocked_finding["evidence"]["measurements"] = {}
    unavailable["findings"].append(blocked_finding)
    unavailable["summary"].update(tool_status="unavailable", finding_counts={"pass": 2, "fail": 0, "unsure": 0, "unavailable": 1})
    scenario_rulebooks = {"flagged": confirmed, "no_issue": confirmed, "legacy": confirmed}
    for name, scenario, rule_id in (("unsure", uncertain, "POSTURE-1"), ("unavailable", unavailable, "CUSTOM-1")):
        extra_finding = scenario["findings"][-1]
        extra_rule = Rule(
            rule_id=rule_id, description=extra_finding["description"], check_name=extra_finding["check_name"],
            parameters={}, origin=RuleOrigin.REVIEWER, confirmation_state=ConfirmationState.CONFIRMED,
        )
        scenario_book = Rulebook(
            rulebook_id=f"rulebook_{name}_demo", version=confirmed["version"], guideline_text=SOURCE,
            rules=[confirmed_rule, extra_rule], confirmation_state=ConfirmationState.CONFIRMED,
        ).model_dump(mode="json")
        for item in scenario["findings"]:
            item["rulebook_id"] = scenario_book["rulebook_id"]
            item["rulebook_version"] = scenario_book["version"]
        scenario_rulebooks[name] = scenario_book
    corrected = deepcopy(annotations)
    corrected[1]["label"] = "person"
    revision = {"dataset_id": "dataset_demo", "sample_id": "sample_001", "revision": 2, "parent_revision": 1,
                "objects": corrected, "mask_changed": False, "reviewer_id": "reviewer_demo", "reason": "Corrected the second object's label.",
                "source_audit_id": "audit_demo", "created_at": TIMESTAMP}
    export = {"export_id": "export_demo", "audit_id": "audit_recheck_demo", "job_id": "job_export_demo", "status": "completed",
              "format": "penn_fudan", "selection": "accepted_only", "include_images": False, "included_count": 1, "excluded_count": 0,
              "download_url": f"{BASE_PATH}/exports/export_demo/download", "manifest_url": f"{BASE_PATH}/exports/export_demo/manifest", "created_at": TIMESTAMP}
    from .feedback_examples import feedback_examples
    feedback_values=feedback_examples()['responses']
    feedback=feedback_values['listFeedbackExamples']['items'][0]
    policy=feedback_values['getFeedbackPolicy']
    evaluation=feedback_values['getFeedbackEvaluation']
    job_scenarios, audit_scenarios = build_lifecycle_scenarios(confirmed["version"])
    return {
        "draft": draft, "manual_create": manual_create, "manual_draft": manual_draft, "reviewed": reviewed, "confirmed": confirmed,
        "sample": sample, "dataset": dataset,
        "audit": audit, "audit_run": audit_run, "finding": finding, "passing": passing, "summary": summary,
        "detail": detail, "sample_review": sample_review, "corrected": corrected,
        "revision": revision, "export": export, "feedback": feedback,
        "policy": policy, "evaluation": evaluation,
        "review_scenarios": {
            "flagged": detail, "no_issue": no_issue, "unsure": uncertain,
            "unavailable": unavailable, "legacy": legacy,
        },
        "review_scenario_rulebooks": scenario_rulebooks,
        "job_scenarios": job_scenarios,
        "audit_scenarios": audit_scenarios,
        "dataset_import_scenarios": build_dataset_import_scenarios(),
        "future_job_scenarios": build_future_job_scenarios(),
        "extraction_job_scenarios": build_extraction_job_scenarios(),
    }
