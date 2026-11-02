#!/usr/bin/env python3
"""Controlled comparison of the visual audit on an unchanged and an altered LabelMe sample.

Runs every visual rule of a rulebook against the SAME image in its baseline and altered archive, once
per prompt version (v3 = single verdict, v4 = all-object observations, v5 = scoped assessments), and writes a report. No
database is used; samples are built from the archives in a temporary folder.

    python -B evaluation/annotation_error_pack/tools/compare_visual_audit.py \
        --baseline-archive evaluation/annotation_error_pack/archives/labelme_baseline_3.zip \
        --altered-archive  evaluation/annotation_error_pack/archives/labelme_errors_3.zip \
        --sample-stem 258421 --rulebook rulebook.json \
        --polygon-fit-rule RULE-7 --shifted-key /shapes/0 --out comparison_report.json

Real mode needs a running local Ollama with the pinned model (FEEDBACK_MODEL_DIGEST set, as for the
application). Each model call can take minutes on CPU; use --rules to limit, and --versions v5 to skip v3.

Expected outcomes are NEVER sent to the model. They are applied only after all results exist, from the
command-line arguments above, in the "evaluation" section of the report. One altered image cannot show
general accuracy; the report counts false alarms and unsupported allegations separately.

--mock replaces the model with a canned local stand-in. It exists only to test this script's mechanics.
Its numbers are not model evidence and the report says so. It cannot tell the baseline from the altered
sample, so it fails the shifted annotation in both; only the script's mechanics are being tested.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from hashlib import sha256
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from backend.app.core.config import AssetStorageSettings                      # noqa: E402
from backend.app.core.dataset_import import ImportSettings                     # noqa: E402
from backend.app.datasets.json_zip import prepare_sample, unpack_archive       # noqa: E402
from backend.app.inference.annotation_guideline import GuidelineClient         # noqa: E402
from backend.app.models.dataset_import import ImportPolicy, ImportRequest      # noqa: E402
from backend.app.models.feedback import ExecutionPin, RulePin                  # noqa: E402
from backend.app.models.rule import ConfirmationState, Rulebook               # noqa: E402

VERSIONS = {"v3": "annotation-guideline-v3", "v4": "annotation-guideline-v4", "v5": "annotation-guideline-v5"}


def build_sample(archive: Path, stem: str, work: Path):
    """Import the archive exactly like the application does and return (manifest, asset settings)."""
    # macOS tempfile paths can use /var while the importer resolves /private/var.
    # Keep source paths and the importer's containment root in the same namespace.
    work = work.resolve(strict=True)
    content = archive.read_bytes()
    root, assets, source = work / "imports", work / "assets", work / "extracted"
    root.mkdir(), assets.mkdir()
    (root / "input.zip").write_bytes(content)
    request = ImportRequest(job_id="job_compare", dataset_id="dataset_compare", archive_key="input.zip",
                            archive_sha256=sha256(content).hexdigest(), archive_size_bytes=len(content),
                            policy=ImportPolicy()).model_copy(update={"format": "labelme"})
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
    return Rulebook.model_validate(document)


def pin_for(version: str, mock: bool) -> ExecutionPin:
    if mock:
        base = ExecutionPin(model="mock-vision", model_digest="sha256:" + "a" * 64, num_predict=2048,
                            prompt_version="annotation-guideline-v3", preprocessing="original-and-annotation-overlay-v1")
    else:
        from backend.app.core.feedback import load_execution_pin
        base = load_execution_pin()
    values = base.model_dump(mode="python")
    values["prompt_version"] = VERSIONS[version]
    values["preprocessing"] = "original-and-annotation-overlay-v1"
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
        if execution.prompt_version == 'annotation-guideline-v4':
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
                                         "prompt_eval_count": 1000,
                                         "message": {"role": "assistant", "content": json.dumps(body)}})
    return httpx.MockTransport(handler)


def run_one(version, rule, manifest, assets, args):
    execution = pin_for(version, args.mock)
    client = GuidelineClient(execution, transport=mock_transport(execution, args.shifted_key) if args.mock else None)
    try:
        recorded = client.predict(manifest, RulePin(rulebook_id=args.rulebook_id, rulebook_version=args.rulebook_version,
                                                    rule=rule), [], assets)
    finally:
        client.close()
    scope = recorded.scope.model_dump(mode="json") if recorded.scope is not None else None
    return {"rule_id": rule.rule_id, "description": rule.description, "outcome": recorded.prediction.outcome,
            "reason": recorded.prediction.reason, "model_verified": recorded.model_verified,
            "duration_ms": round(recorded.duration_ms, 1), "scope": scope,
            "visual_assessment": None if recorded.visual_assessment is None else recorded.visual_assessment.model_dump(mode='json')}


def evaluate(report, args):
    """Apply the expected outcomes AFTER the fact. Returns counts, not a verdict on model accuracy."""
    result = {}
    def skipped_for_eligibility(row):
        scope = row.get('scope')
        return bool(scope and scope.get('outcome_source') == 'no_model_call'
                    and scope.get('unavailable') is not None and not row.get('model_verified'))

    for version, samples in report["results"].items():
        base, alt = samples["baseline"], samples["altered"]
        fit_alt = next((r for r in alt if r["rule_id"] == args.polygon_fit_rule), None)
        rows = None
        if fit_alt and fit_alt.get('model_verified'):
            if fit_alt.get('scope'):
                rows = fit_alt['scope']['results'] or None
            elif fit_alt.get('visual_assessment'):
                rows = fit_alt['visual_assessment']['observations'] or None
        flagged = None if rows is None else sorted({x['object_key'] for x in rows if x['outcome'] in ('fail', 'unsure')})
        result[version] = {
            "baseline_rules_failed_false_alarms": [r["rule_id"] for r in base if r["outcome"] == "fail"],
            "altered_polygon_fit_outcome": None if fit_alt is None else fit_alt["outcome"],
            "altered_flagged_annotations_polygon_fit": flagged,
            "altered_rules_failed_other_than_polygon_fit": [r["rule_id"] for r in alt
                                                            if r["outcome"] == "fail" and r["rule_id"] != args.polygon_fit_rule],
            "altered_unchanged_annotation_flagged": None if flagged is None else [k for k in flagged if k != args.shifted_key],
            "model_calls_skipped_as_unavailable_without_inference": sum(
                1 for batch in (base, alt) for r in batch if skipped_for_eligibility(r)),
            "unverified_execution_count": sum(
                1 for batch in (base, alt) for r in batch
                if not r['model_verified'] and not skipped_for_eligibility(r)),
            "unavailable_rules": {name: [r['rule_id'] for r in batch if r['outcome'] == 'unavailable']
                                  for name, batch in (('baseline', base), ('altered', alt))},
            "total_duration_seconds": round(sum(r["duration_ms"] for rows in (base, alt) for r in rows) / 1000, 1),
        }
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-archive", required=True, type=Path)
    parser.add_argument("--altered-archive", required=True, type=Path)
    parser.add_argument("--sample-stem", required=True)
    parser.add_argument("--rulebook", required=True, type=Path, help="Rulebook JSON, e.g. GET /api/v1/rulebooks/<id>")
    parser.add_argument("--polygon-fit-rule", required=True, help="rule_id of the polygon-fit rule (expected outcome only)")
    parser.add_argument("--shifted-key", default="/shapes/0", help="source key of the deliberately shifted annotation")
    parser.add_argument("--versions", default="v4,v5")
    parser.add_argument("--rules", help="comma-separated rule_ids to run (default: all supported visual rules)")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"Refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    versions = [v.strip() for v in args.versions.split(",")]
    if any(v not in VERSIONS for v in versions):
        print("--versions must be a comma-separated selection of v3, v4 and v5", file=sys.stderr)
        return 2
    book = load_rulebook(args.rulebook)
    args.rulebook_id, args.rulebook_version = book.rulebook_id, book.version
    wanted = set(args.rules.split(",")) if args.rules else None
    visual = [r for r in book.rules if r.check_name == "annotation_guideline"
              and r.confirmation_state is ConfirmationState.CONFIRMED and (wanted is None or r.rule_id in wanted)]
    skipped = [{"rule_id": r.rule_id, "reason": "not a confirmed annotation_guideline rule"}
               for r in book.rules if r not in visual]
    report = {"mock_only_not_model_evidence": args.mock, "rulebook": f"{book.rulebook_id}@{book.version}",
              "sample_stem": args.sample_stem, "versions": versions, "rules_run": [r.rule_id for r in visual],
              "rules_not_run": skipped, "results": {}}
    with tempfile.TemporaryDirectory() as tmp:
        samples = {}
        for name, archive in (("baseline", args.baseline_archive), ("altered", args.altered_archive)):
            work = Path(tmp) / name
            work.mkdir()
            samples[name] = build_sample(archive, args.sample_stem, work)
        for version in versions:
            report["results"][version] = {"baseline": [], "altered": []}
            for name, (manifest, assets) in samples.items():
                for rule in visual:
                    row = run_one(version, rule, manifest, assets, args)
                    report["results"][version][name].append(row)
                    print(f"[{version}] {name:8} {rule.rule_id:8} {row['outcome']:11} {row['duration_ms'] / 1000:7.1f}s  "
                          f"{row['reason'][:90]}", flush=True)
    report["evaluation"] = evaluate(report, args)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")
    if args.mock:
        print("MOCK RUN: these results test the script only and are not model evidence.")
    print(json.dumps(report["evaluation"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
