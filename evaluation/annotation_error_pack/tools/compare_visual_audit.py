#!/usr/bin/env python3
"""Controlled comparison of the visual audit on an unchanged and an altered sample.

Runs every selected visual rule of a rulebook against the SAME image in its baseline and altered archive, once
per prompt version (v3 = single verdict, v4 = all-object observations, v5 = scoped assessments, v6 = one
focused request per target), and writes a report. No database is used; samples are built from the archives in
a temporary folder.

    python -B evaluation/annotation_error_pack/tools/compare_visual_audit.py \
        --baseline-archive evaluation/annotation_error_pack/archives/labelme_baseline_3.zip \
        --altered-archive  evaluation/annotation_error_pack/archives/labelme_errors_3.zip \
        --sample-stem 258421 --rulebook rulebook.json --versions v5,v6 --rules RULE-7 \
        --polygon-fit-rule RULE-7 --shifted-key /shapes/0 --out comparison_report_v6_try1.json

Real mode needs a running local Ollama with the pinned model (FEEDBACK_MODEL_DIGEST set, as for the
application). Each model call can take minutes on CPU, and v6 makes one call per eligible target, so use
--rules to limit the first run. The script refuses to overwrite a report: every run needs a new filename.

Expected outcomes are NEVER sent to the model. They are applied only after all results exist, from
--polygon-fit-rule/--shifted-key or an --expectations file ({"baseline": {"RULE-7": {"/shapes/0": "pass"}},
"altered": {...}}), in the report's "expectations" section. One altered image cannot show general accuracy.

The report (format 2) separates, for every eligible annotation: explicit failures, explicit passes, uncertain
judgments, missing assessments (a coverage gap, not an allegation), unavailable results, execution failures and
excluded-by-condition; and, per rule, expected no-model eligibility skips. It records the model and digest,
prompt/preprocessing version and inference settings, the rulebook and selected rules, source hashes, and per-call
timing and coverage. --diagnostics adds bounded excerpts of replies that failed validation (never prompts or images).

--mock replaces the model with a canned local stand-in. It exists only to test this script's mechanics.
Its numbers are not model evidence and the report says so. It cannot tell the baseline from the altered
sample, so it fails the shifted annotation in both; only the script's mechanics are being tested.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app.core.config import AssetStorageSettings                      # noqa: E402
from backend.app.core.dataset_import import ImportSettings                     # noqa: E402
from backend.app.datasets.json_zip import prepare_sample, unpack_archive       # noqa: E402
from backend.app.inference.annotation_guideline import GuidelineClient         # noqa: E402
from backend.app.models.dataset_import import ImportPolicy, ImportRequest      # noqa: E402
from backend.app.models.feedback import ExecutionPin, RulePin                  # noqa: E402
from backend.app.models.rule import ConfirmationState, Rulebook, RulebookRecord  # noqa: E402
from comparison_report import (                                                # noqa: E402
    DEPRECATED_FIELDS, REPORT_FORMAT_VERSION, add_categories, evaluate_expectations, legacy_evaluation,
    legacy_expectations, summarize)

VERSIONS = {"v3": "annotation-guideline-v3", "v4": "annotation-guideline-v4", "v5": "annotation-guideline-v5",
            "v6": "annotation-guideline-v6", "v7": "annotation-guideline-v7"}
PREPROCESSING = {"v6": "target-focus-overlay-v1", "v7": "target-focus-overlay-v1"}          # every other version uses the shared overlay
DEFAULT_PREPROCESSING = "original-and-annotation-overlay-v1"


def build_sample(archive: Path, stem: str, work: Path, annotation_format: str = "labelme"):
    """Import the archive exactly like the application does; return (manifest, asset settings)."""
    # macOS tempfile paths can use /var while the importer resolves /private/var.
    # Keep source paths and the importer's containment root in the same namespace.
    work = work.resolve(strict=True)
    content = archive.read_bytes()
    root, assets, source = work / "imports", work / "assets", work / "extracted"
    root.mkdir(), assets.mkdir()
    (root / "input.zip").write_bytes(content)
    request = ImportRequest(job_id="job_compare", dataset_id="dataset_compare", archive_key="input.zip",
                            archive_sha256=sha256(content).hexdigest(), archive_size_bytes=len(content),
                            policy=ImportPolicy()).model_copy(update={"format": annotation_format})
    settings = ImportSettings(root=root, asset_root=assets, policy=request.policy)
    samples, _ = unpack_archive(root / "input.zip", source, request)
    matches = [(i, item) for i, item in enumerate(samples) if item.sample_id.startswith(stem)]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one sample starting with {stem!r} in {archive.name}; "
                         f"found {[s.sample_id for s in samples]}")
    index, item = matches[0]
    publication = prepare_sample(request.dataset_id, item, index, source_root=source,
                                 asset_root=settings.asset_root, policy=request.policy)
    missing = [name for name in ('original_image', 'annotation_text')
               if getattr(publication.manifest, name).availability.value != 'available']
    if missing:
        raise SystemExit(f"Cannot compare {stem!r} from {archive.name}: unavailable source assets: {', '.join(missing)}")
    return publication.manifest, AssetStorageSettings(root=settings.asset_root)


def load_rulebook(path: Path) -> Rulebook:
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, dict) and "items" in document:      # a rulebook list/version page
        document = document["items"][0]
    # API records now include display-name metadata. Validate that shape, then
    # retain only the immutable rulebook content used by inference.
    record = RulebookRecord.model_validate(document)
    return Rulebook.model_validate(record.model_dump(exclude={'name'}))


def pin_for(version: str, mock: bool) -> ExecutionPin:
    if mock:
        base = ExecutionPin(model="mock-vision", model_digest="sha256:" + "a" * 64, num_predict=2048,
                            prompt_version="annotation-guideline-v3", preprocessing=DEFAULT_PREPROCESSING)
    else:
        from backend.app.core.feedback import load_execution_pin
        base = load_execution_pin()
    values = base.model_dump(mode="python")
    values["prompt_version"] = VERSIONS[version]
    values["preprocessing"] = PREPROCESSING.get(version, DEFAULT_PREPROCESSING)
    return ExecutionPin.model_validate(values)


def mock_transport(execution: ExecutionPin, shifted_key: str):
    """Canned stand-in for the local server (script self-test only)."""
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.33.3"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": execution.model, "digest": "a" * 64}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["vision"]})
        document = json.loads(json.loads(request.content)["messages"][-1]["content"])
        body = {"case_id": document["case_id"], "rule_id": document["rule_id"], "evidence_references": ["image"]}
        scope = document.get("requirement_scope")
        if execution.prompt_version in {'annotation-guideline-v6', 'annotation-guideline-v7'}:
            key = document["focus"]["object_key"]
            supplied = document["requirement_scope"]["eligible_targets"][0]["supplied_geometry"]
            body.update(object_key=key, condition="applies", compliance="no" if key == shifted_key else "yes",
                        geometry=supplied[0] if supplied else "polygon", reason="Mock focused assessment.")
        elif execution.prompt_version == 'annotation-guideline-v4':
            body.pop('evidence_references')
            body['observations'] = [
                {'object_key': item['object_key'],
                 'outcome': 'fail' if item['object_key'] == shifted_key else 'pass',
                 'geometry': ['polygon'], 'reason': 'Mock observation.',
                 'region': [0, 0, 1, 1] if item['object_key'] == shifted_key else None}
                for item in document['target']['inputs']['object_inventory']]
        elif scope is None:
            body.update(outcome="fail", reason="Mock v3 reply (no scoping).")
        else:
            body.update(outcome="fail", reason="Mock v5 reply.", assessments=[
                {"object_key": t["object_key"], "outcome": "fail" if t["object_key"] == shifted_key else "pass",
                 "geometry": t["supplied_geometry"][0] if t["supplied_geometry"] else "polygon",
                 "condition": "applies", "reason": "Mock assessment."} for t in scope["eligible_targets"]])
        return httpx.Response(200, json={"model": execution.model, "done": True, "done_reason": "stop",
                                         "prompt_eval_count": 1000, "eval_count": 60,
                                         "message": {"role": "assistant", "content": json.dumps(body)}})
    return httpx.MockTransport(handler)


def run_one(version, rule, manifest, assets, args):
    execution = pin_for(version, args.mock)
    client = GuidelineClient(execution, transport=mock_transport(execution, args.shifted_key) if args.mock else None,
                             capture_diagnostics=args.diagnostics)
    try:
        recorded = client.predict(manifest, RulePin(rulebook_id=args.rulebook_id, rulebook_version=args.rulebook_version,
                                                    rule=rule), [], assets)
        calls, diagnostics = list(client.call_log), list(client.diagnostics)
    finally:
        client.close()
    scope = recorded.scope.model_dump(mode="json") if recorded.scope is not None else None
    row = {"rule_id": rule.rule_id, "description": rule.description, "outcome": recorded.prediction.outcome,
           "reason": recorded.prediction.reason, "model_verified": recorded.model_verified,
           "duration_ms": round(recorded.duration_ms, 1), "scope": scope, "calls": calls,
           "visual_assessment": None if recorded.visual_assessment is None else recorded.visual_assessment.model_dump(mode='json')}
    if args.diagnostics:
        row["diagnostics"] = diagnostics[:16]
    return row


def execution_record(version, args):
    """Everything needed to reproduce a run: pin (model, digest, versions, settings), host and rules."""
    pin = pin_for(version, args.mock).model_dump(mode="json")
    if not args.mock:
        from backend.app.core.config import load_ollama_settings
        pin["ollama_host"] = str(load_ollama_settings().host)
    return pin


def evaluate(report, args):
    """Report-format-1 entry point, kept for existing consumers; see comparison_report.legacy_evaluation."""
    return legacy_evaluation(report["results"], args.polygon_fit_rule, args.shifted_key)


def describe_row(version, name, row):
    skipped = (row["categories"]["status"] == "eligibility_skip")
    note = f"[{row['categories']['status']}]" if row["categories"]["status"] != "assessed" else ""
    print(f"[{version}] {name:8} {row['rule_id']:8} {row['outcome']:11} {row['duration_ms'] / 1000:7.1f}s  "
          f"{'skip ' if skipped else ''}{note} {row['reason'][:80]}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-archive", required=True, type=Path)
    parser.add_argument("--altered-archive", required=True, type=Path)
    parser.add_argument("--sample-stem", required=True)
    parser.add_argument("--format", default="labelme", choices=["labelme", "coco"], dest="annotation_format")
    parser.add_argument("--rulebook", required=True, type=Path, help="Rulebook JSON, e.g. GET /api/v1/rulebooks/<id>")
    parser.add_argument("--polygon-fit-rule", help="rule_id of the polygon-fit rule (expected outcome only)")
    parser.add_argument("--shifted-key", default="/shapes/0", help="source key of the deliberately shifted annotation")
    parser.add_argument("--expectations", type=Path, help="JSON of expected pass/fail per sample, rule and annotation key")
    parser.add_argument("--versions", default="v4,v5")
    parser.add_argument("--rules", help="comma-separated rule_ids to run (default: all supported visual rules)")
    parser.add_argument("--diagnostics", action="store_true", help="include bounded excerpts of invalid model replies")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"Refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    versions = [v.strip() for v in args.versions.split(",")]
    if any(v not in VERSIONS for v in versions):
        print("--versions must be a comma-separated selection of v3, v4, v5, v6 and v7", file=sys.stderr)
        return 2
    book = load_rulebook(args.rulebook)
    args.rulebook_id, args.rulebook_version = book.rulebook_id, book.version
    wanted = set(args.rules.split(",")) if args.rules else None
    visual = [r for r in book.rules if r.check_name == "annotation_guideline"
              and r.confirmation_state is ConfirmationState.CONFIRMED and (wanted is None or r.rule_id in wanted)]
    skipped = [{"rule_id": r.rule_id, "reason": "not a confirmed annotation_guideline rule"}
               for r in book.rules if r not in visual]
    given = json.loads(args.expectations.read_text(encoding="utf-8")) if args.expectations else None
    report = {"report_format_version": REPORT_FORMAT_VERSION, "mock_only_not_model_evidence": args.mock,
              "rulebook": f"{book.rulebook_id}@{book.version}", "sample_stem": args.sample_stem,
              "annotation_format": args.annotation_format, "versions": versions,
              "rules_run": [r.rule_id for r in visual], "rules_not_run": skipped,
              "rules": [{"rule_id": r.rule_id, "description_sha256": sha256(r.description.encode()).hexdigest(),
                         "stored_applicability": None if r.applicability is None else r.applicability.model_dump(mode="json")}
                        for r in visual],
              "execution": {version: execution_record(version, args) for version in versions},
              "sources": {}, "results": {}, "deprecated_fields": DEPRECATED_FIELDS}
    with tempfile.TemporaryDirectory() as tmp:
        samples = {}
        for name, archive in (("baseline", args.baseline_archive), ("altered", args.altered_archive)):
            work = Path(tmp) / name
            work.mkdir()
            samples[name] = build_sample(archive, args.sample_stem, work, args.annotation_format)
            manifest = samples[name][0]
            report["sources"][name] = {
                "archive": archive.name, "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
                "sample_id": manifest.sample_id, "sample_revision": manifest.sample_revision,
                "image_sha256": manifest.original_image.sha256, "annotation_sha256": manifest.annotation_text.sha256}
        for version in versions:
            report["results"][version] = {"baseline": [], "altered": []}
            for name, (manifest, assets) in samples.items():
                for rule in visual:
                    row = run_one(version, rule, manifest, assets, args)
                    add_categories([row])
                    report["results"][version][name].append(row)
                    describe_row(version, name, row)
    report["summary"] = {version: summarize(samples) for version, samples in report["results"].items()}
    report["expectations"] = {}
    for version, samples in report["results"].items():
        expected = given or (legacy_expectations(samples, args.polygon_fit_rule, args.shifted_key)
                             if args.polygon_fit_rule else None)
        if expected:
            report["expectations"][version] = evaluate_expectations(samples, expected)
    if args.polygon_fit_rule:
        report["evaluation"] = legacy_evaluation(report["results"], args.polygon_fit_rule, args.shifted_key)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")
    if args.mock:
        print("MOCK RUN: these results test the script only and are not model evidence.")
    for version in versions:
        counts = {name: report["summary"][version][name]["counts"] for name in ("baseline", "altered")}
        print(f"[{version}] counts: {json.dumps(counts)}")
        if version in report["expectations"]:
            print(f"[{version}] expectations: {json.dumps(report['expectations'][version]['counts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
