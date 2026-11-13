#!/usr/bin/env python3
"""Run the whole LabelMe flow against a running application and score it against the answer key.

Steps: import the three archives, extract and confirm the guideline rules, audit each dataset, compare the
findings with the injected errors, then on the error dataset: ask for a suggested correction, apply it, recheck
the saved revision, record reviewer decisions, export and re-import the export.

Needs the API, its workers, PostgreSQL, the local chat model (for rule extraction only) and the person model
file. Standard library only. Writes results.json next to this script and prints the results table.

    python evaluation/labelme_end_to_end/run_flow.py
    python evaluation/labelme_end_to_end/run_flow.py --rulebook-id <id> --rulebook-version <n>   # skip extraction
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
REVIEWER = "labelme_flow_check"
MEASURED_CHECKS = ("allowed_labels", "person_coverage", "unannotated_people")
ERROR_CHECK = {"head_cut": "person_coverage", "feet_cut": "person_coverage", "extra_background": "person_coverage",
               "shifted": "person_coverage", "wrong_label": "allowed_labels",
               "annotation_removed": "unannotated_people"}
DATASETS = ("labelme_people_clean", "labelme_people_errors", "labelme_native_examples")


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None, *, raw=None, content_type="application/json", allow=()):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request) as response:
                payload = response.read()
                is_json = response.headers.get_content_type() == "application/json"
                return json.loads(payload) if is_json else payload
        except urllib.error.HTTPError as error:
            detail = error.read()[:600]
            if error.code in allow:
                return {"http_error": error.code, "detail": detail.decode("utf-8", "replace")}
            raise SystemExit(f"{method} {path} -> {error.code} {detail!r}")

    def pages(self, path):
        items, offset = [], 0
        joiner = "&" if "?" in path else "?"
        while offset is not None:
            page = self.call("GET", f"{path}{joiner}limit=100&offset={offset}")
            items += page["items"]
            offset = page.get("next_offset")
        return items

    def wait(self, job_id, what):
        begin = time.monotonic()
        while True:
            job = self.call("GET", f"/jobs/{job_id}")
            if job["status"] in ("completed", "failed", "cancelled"):
                seconds = time.monotonic() - begin
                print(f"   {what}: {job['status']} in {seconds:.0f}s", flush=True)
                if job["status"] != "completed":
                    raise SystemExit(f"{what} did not complete: {job.get('error')}")
                return job, seconds
            time.sleep(2)

    def import_zip(self, name, archive: bytes, file_name):
        boundary = uuid.uuid4().hex
        def field(key, value):
            return f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        body = b"".join([
            field("name", name), field("format", "labelme"),
            f'--{boundary}\r\nContent-Disposition: form-data; name="archive"; filename="{file_name}"\r\n'
            f"Content-Type: application/zip\r\n\r\n".encode() + archive + b"\r\n",
            f"--{boundary}--\r\n".encode()])
        started = self.call("POST", "/datasets", raw=body, content_type=f"multipart/form-data; boundary={boundary}")
        self.wait(started["job"]["job_id"], f"import {name}")
        return started["dataset"]["dataset_id"]


def read_archive(path: Path) -> dict[str, dict]:
    """{file stem: LabelMe document} from a dataset ZIP."""
    with zipfile.ZipFile(path) as archive:
        return {Path(name).stem: json.loads(archive.read(name)) for name in archive.namelist()
                if name.endswith(".json") and Path(name).name == name}


def as_points(polygons):
    return [[list(point) for point in part] for part in polygons]


def exact_vertices(samples: list[dict], documents: dict[str, dict]) -> list[str]:
    """Differences between what the application shows and the uploaded files; empty means exact."""
    problems = []
    by_stem = {sample["sample_id"].rsplit("_", 1)[0]: sample for sample in samples}
    for stem, document in documents.items():
        sample = by_stem.get(stem)
        if sample is None:
            problems.append(f"{stem}: not imported")
            continue
        shapes = document["shapes"]
        if len(sample["objects"]) != len(shapes):
            problems.append(f"{stem}: {len(sample['objects'])} objects shown for {len(shapes)} shapes")
            continue
        for index, (shown, shape) in enumerate(zip(sample["objects"], shapes)):
            where = f"{stem} /shapes/{index}"
            if shown.get("source_key") != f"/shapes/{index}":
                problems.append(f"{where}: key shown as {shown.get('source_key')}")
            if shown["label"] != shape["label"]:
                problems.append(f"{where}: label {shown['label']!r} for {shape['label']!r}")
            if shape["shape_type"] == "polygon":
                if as_points(shown.get("polygons") or []) != [shape["points"]]:
                    problems.append(f"{where}: polygon points differ from the file")
                if shown.get("bbox_origin") == "supplied":
                    problems.append(f"{where}: a polygon is shown with a supplied box")
    return problems


def box_iou(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def score(findings: list[dict], samples: list[dict], cases: list[dict]) -> dict:
    """Compare tool findings with the injected errors.

    Found: the expected check fails on the altered annotation, or the image is reported with an unannotated
    person where the removed or shifted annotation belongs. False alarm: any other `fail` from the three measured
    checks, each unannotated person reported in the wrong place counting once. One exception: a label the
    guideline does not allow, already present in the source file (furniture in the upstream examples), is a
    correct report and is counted on its own.
    """
    stem_of = {sample["sample_id"]: sample["sample_id"].rsplit("_", 1)[0] for sample in samples}
    key_of = {(sample["sample_id"], obj["object_id"]): obj["source_key"]
              for sample in samples for obj in sample["objects"]}
    label_of = {(sample["sample_id"], obj["object_id"]): obj["label"]
                for sample in samples for obj in sample["objects"]}
    source_labels_reported = 0
    found_by: dict[str, set] = {}
    injected = {Path(case["file"]).stem: case for case in cases if case["error"]}
    found, outcomes_on_injected, false_alarms = set(), {}, []
    for finding in findings:
        if finding["check_name"] not in MEASURED_CHECKS:
            continue
        stem = stem_of[finding["sample_id"]]
        case = injected.get(stem)
        if finding["check_name"] == "unannotated_people":
            if finding["outcome"] != "fail":
                if case and case["error"] == "annotation_removed":
                    outcomes_on_injected[stem] = finding["outcome"]
                continue
            result = ((finding.get("evidence") or {}).get("measurements") or {}).get("result") or {}
            for person in result.get("unannotated") or [{}]:
                box = person.get("box")
                # A removed annotation, or an outline moved so far off its person that the person is left
                # without one, is reported here: at the place where the annotation belongs.
                if (case and case["error"] in ("annotation_removed", "shifted") and box
                        and box_iou(box, case["source_box"]) >= 0.3):
                    found.add(stem)
                    found_by.setdefault(stem, set()).add("unannotated_people")
                else:
                    false_alarms.append({"file": f"{stem}.json", "check": "unannotated_people", "box": box})
            continue
        shape_key = key_of.get((finding["sample_id"], finding["object_id"]))
        on_injected = bool(case and case["shape_key"] == shape_key
                           and ERROR_CHECK[case["error"]] == finding["check_name"])
        if on_injected:
            outcomes_on_injected[stem] = finding["outcome"]
            if finding["outcome"] == "fail":
                found.add(stem)
                found_by.setdefault(stem, set()).add(finding["check_name"])
        elif (finding["outcome"] == "fail" and finding["check_name"] == "allowed_labels"
              and label_of.get((finding["sample_id"], finding["object_id"])) != "person"):
            source_labels_reported += 1
        elif finding["outcome"] == "fail":
            false_alarms.append({"file": f"{stem}.json", "check": finding["check_name"], "shape_key": shape_key,
                                 "reason": (finding.get("reason") or "")[:200]})
    by_type = {}
    for stem, case in injected.items():
        entry = by_type.setdefault(case["error"], {"injected": 0, "found": 0, "found_by": {}, "missed": []})
        entry["injected"] += 1
        if stem in found:
            entry["found"] += 1
            for check in sorted(found_by[stem]):
                entry["found_by"][check] = entry["found_by"].get(check, 0) + 1
        else:
            entry["missed"].append({"file": case["file"], "tool_outcome": outcomes_on_injected.get(stem, "no finding")})
    outcomes = {}
    for finding in findings:
        name = f"{finding['check_name']}:{finding['outcome']}"
        outcomes[name] = outcomes.get(name, 0) + 1
    return {"errors_injected": len(injected), "errors_found": len(found), "false_alarms": len(false_alarms),
            "source_labels_outside_the_guideline_reported": source_labels_reported,
            "found_by_file": {f"{stem}.json": sorted(checks) for stem, checks in sorted(found_by.items())},
            "by_error_type": by_type, "false_alarm_list": false_alarms, "finding_outcomes": dict(sorted(outcomes.items()))}


def rulebook(api: Api, arguments) -> dict:
    """Extract the guideline, review the draft as a reviewer would, and confirm it."""
    if arguments.rulebook_id:
        book = api.call("GET", f"/rulebooks/{arguments.rulebook_id}?version={arguments.rulebook_version}")
        return {"rulebook_id": book["rulebook_id"], "version": book["version"], "reused": True,
                "audited_checks": [rule["check_name"] for rule in book["rules"]]}
    guideline = (HERE / "guidelines.txt").read_text(encoding="utf-8").strip()
    if arguments.draft_rulebook_id:
        book = api.call("GET", f"/rulebooks/{arguments.draft_rulebook_id}?version=1")
    else:
        job = api.call("POST", "/rulebooks/extractions", {"guideline_text": guideline})
        done, _ = api.wait(job["job_id"], "rule extraction")
        book = api.call("GET", done["result"]["href"].removeprefix("/api/v1"))
    extracted = [rule["check_name"] for rule in book["rules"]]
    print("   extracted checks:", extracted, flush=True)
    # Review step. A sentence the model left without a check stays in the draft, and the reviewer chooses the
    # check on the rule screen. This run does that for the label sentence only, and reports that it did.
    assigned = []
    for rule in book["rules"]:
        sentence = (rule.get("source_sentence") or "").lower()
        if rule["check_name"] is None and "allowed label" in sentence and "allowed_labels" not in extracted:
            rule["check_name"], rule["parameters"] = "allowed_labels", {"allowed_labels": ["person"]}
            assigned.append({"rule_id": rule["rule_id"], "check": "allowed_labels", "sentence": rule["source_sentence"]})
    reviewed = [rule["check_name"] for rule in book["rules"]]
    missing = [check for check in MEASURED_CHECKS if check not in reviewed]
    if missing:
        raise SystemExit(f"The guideline was not turned into these checks: {missing}. Nothing was audited.")
    # Keep the three measured rules. Anything else the model proposed is left out of this run, so the timing
    # and the scores are those of the measured checks only.
    rules = [{**rule, "confirmation_state": "confirmed"} for rule in book["rules"]
             if rule["check_name"] in MEASURED_CHECKS]
    saved = api.call("PUT", f"/rulebooks/{book['rulebook_id']}", {
        "expected_version": book["version"], "reviewer_id": REVIEWER, "guideline_text": guideline, "rules": rules})
    final = api.call("POST", f"/rulebooks/{book['rulebook_id']}/confirm",
                     {"expected_version": saved["version"], "reviewer_id": REVIEWER})
    api.call("PUT", f"/rulebooks/{book['rulebook_id']}/title", {
        "title": "LabelMe end-to-end check", "expected_title_revision": 0, "reviewer_id": REVIEWER})
    return {"rulebook_id": final["rulebook_id"], "version": final["version"], "extracted_checks": extracted,
            "checks_assigned_by_reviewer": assigned, "audited_checks": list(MEASURED_CHECKS)}


def audit(api: Api, dataset_id, book_id, book_version, title):
    started = api.call("POST", "/audits", {"dataset_id": dataset_id, "rulebook_id": book_id,
                                           "rulebook_version": book_version, "sample_ids": None,
                                           "feedback_policy_id": None})
    _, seconds = api.wait(started["job"]["job_id"], f"audit {title}")
    audit_id = started["audit"]["audit_id"]
    api.call("PUT", f"/audits/{audit_id}/title", {"title": title, "expected_title_revision": 0,
                                                 "reviewer_id": REVIEWER})
    return audit_id, seconds


def correct_and_recheck(api: Api, audit_id, dataset_id, sample, shape_key, clean_points) -> dict:
    """Suggested correction -> explicit apply -> recheck of the saved revision, for one failing outline."""
    sample_id, revision = sample["sample_id"], sample["revision"]
    record = {"sample_id": sample_id, "shape_key": shape_key}
    started = api.call("POST", f"/audits/{audit_id}/rechecks", {
        "sample_ids": [sample_id], "sample_revisions": {sample_id: revision}, "reviewer_id": REVIEWER,
        "feedback": "The outline does not cover the whole person.", "propose_correction": True,
        "object_keys": [shape_key]})
    api.wait(started["job"]["job_id"], "recheck with feedback and a suggested correction")
    attempt = started["audit"]["audit_id"]
    detail = api.call("GET", f"/audits/{attempt}/samples/{sample_id}")
    proposal = (detail.get("measurements") or {}).get("correction_proposal") or {}
    record["proposal_status"] = proposal.get("status")
    record["proposal_reason"] = (proposal.get("reason") or "")[:300]
    unchanged = api.call("GET", f"/datasets/{dataset_id}/samples/{sample_id}")
    record["proposal_changed_nothing"] = unchanged["revision"] == revision
    if proposal.get("status") == "proposed" and proposal.get("objects"):
        objects, record["applied"] = proposal["objects"], "suggested correction"
    else:
        # No usable suggestion: the reviewer corrects the outline by hand (here, back to the annotator's points).
        objects = json.loads(json.dumps(unchanged["objects"]))
        for obj in objects:
            if obj["source_key"] == shape_key:
                obj["polygons"] = [clean_points]
                xs, ys = [p[0] for p in clean_points], [p[1] for p in clean_points]
                obj["bbox"] = [min(xs), min(ys), max(xs), max(ys)]  # derived bounds must agree with the points
        record["applied"] = "manual correction"
    saved = api.call("POST", f"/datasets/{dataset_id}/samples/{sample_id}/revisions", {
        "expected_revision": revision, "reviewer_id": REVIEWER, "reason": "Outline corrected to cover the person.",
        "objects": objects, "mask_upload_id": None, "source_audit_id": attempt}, allow=(409, 422))
    if "http_error" in saved:
        record["save_error"] = saved
        return record
    record["saved_revision"] = saved["revision"]
    rechecked = api.call("POST", f"/audits/{audit_id}/rechecks", {
        "sample_ids": [sample_id], "sample_revisions": {sample_id: saved["revision"]}, "reviewer_id": REVIEWER,
        "feedback": None, "propose_correction": False})
    api.wait(rechecked["job"]["job_id"], "recheck of the saved revision")
    record["recheck_audit_id"] = rechecked["audit"]["audit_id"]
    after = api.pages(f"/audits/{record['recheck_audit_id']}/findings?sample_id={sample_id}")
    current = api.call("GET", f"/datasets/{dataset_id}/samples/{sample_id}")
    number = next(obj["object_id"] for obj in current["objects"] if obj["source_key"] == shape_key)
    record["outcome_after"] = next((finding["outcome"] for finding in after
                                    if finding["check_name"] == "person_coverage"
                                    and finding["object_id"] == number), None)
    # Every annotation the correction did not name keeps its exact points.
    before_objects = {obj["source_key"]: obj for obj in unchanged["objects"]}
    record["other_annotations_unchanged"] = all(
        obj["polygons"] == before_objects[obj["source_key"]]["polygons"] and obj["label"] == before_objects[obj["source_key"]]["label"]
        for obj in current["objects"] if obj["source_key"] != shape_key)
    return record


def decide(api: Api, audit_id, item) -> str:
    """One reviewer decision: reject an image the tool reported a problem on, accept the others."""
    failed = item["finding_counts"].get("fail", 0) > 0
    decision = "rejected" if failed else "accepted"
    api.call("PUT", f"/audits/{audit_id}/samples/{item['sample_id']}/review", {
        "expected_sample_revision": item["sample_revision"], "expected_review_version": 0,
        "decision": decision, "reviewer_id": REVIEWER,
        "comment": "The tool reported a problem and I agree." if failed else "Checked; looks correct."},
        allow=(409,))  # 409: this image already has its decision from an earlier, interrupted run
    return decision


def review_and_export(api: Api, audit_id, dataset_id, documents, corrections) -> dict:
    """Reviewer decisions for every image, a native export of the workspace, and a re-import of that export."""
    decisions = {item["sample_id"]: decide(api, audit_id, item) for item in api.pages(f"/audits/{audit_id}/samples")}
    for correction in corrections:  # a corrected image is decided on its latest attempt, the recheck
        if correction.get("recheck_audit_id"):
            for item in api.pages(f"/audits/{correction['recheck_audit_id']}/samples"):
                decisions[item["sample_id"]] = decide(api, correction["recheck_audit_id"], item)
    exported = api.call("POST", "/exports", {"audit_id": audit_id, "format": "labelme", "selection": "all_reviewed",
                                            "include_images": True, "include_rechecks": True})
    api.wait(exported["job"]["job_id"], "export")
    export_id = exported["export"]["export_id"]
    data = api.call("GET", f"/exports/{export_id}/download")
    manifest = api.call("GET", f"/exports/{export_id}/manifest")
    revisions = {entry["sample_id"]: entry["sample_revision"] for entry in manifest["entries"]}
    record = {"export_id": export_id, "decisions": {
        "accepted": sum(value == "accepted" for value in decisions.values()),
        "rejected": sum(value == "rejected" for value in decisions.values())},
        "corrected_revisions_exported": sorted(s for s, revision in revisions.items() if revision > 1)}
    expected, wrong_folder, changed, not_source = {}, [], [], []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        record["folders"] = {folder: sum(name.startswith(f"{folder}/") and name.endswith(".json") for name in names)
                             for folder in ("accepted", "rejected")}
        record["has_review_report"] = "review_report.csv" in names
        for sample_id, decision in decisions.items():
            name = f"{decision}/data/{sample_id}/annotations.json"
            if name not in names:
                wrong_folder.append(sample_id)
                continue
            shapes = [(shape["label"], shape["points"]) for shape in json.loads(archive.read(name))["shapes"]]
            pinned = api.call("GET", f"/datasets/{dataset_id}/samples/{sample_id}?revision={revisions[sample_id]}")
            if shapes != [(obj["label"], as_points(obj["polygons"])[0]) for obj in pinned["objects"]]:
                changed.append(sample_id)
            source = documents[sample_id.rsplit("_", 1)[0]]["shapes"]
            if revisions[sample_id] == 1 and shapes != [(shape["label"], shape["points"]) for shape in source]:
                not_source.append(sample_id)
            # The importer names a sample after its image file; an exported image is always image.jpg.
            again = "image_" + hashlib.sha256(f"{name}#".encode()).hexdigest()[:16]
            expected[again] = shapes
    record["images_in_wrong_folder"] = wrong_folder
    record["exported_files_differing_from_their_pinned_revision"] = changed
    record["uncorrected_files_differing_from_the_upload"] = not_source
    reimported = api.import_zip("LabelMe flow: re-import of the export", data, "export.zip")
    samples = [api.call("GET", f"/datasets/{reimported}/samples/{item['sample_id']}")
               for item in api.pages(f"/datasets/{reimported}/samples")]
    got = {sample["sample_id"]: [(obj["label"], as_points(obj["polygons"])[0]) for obj in sample["objects"]]
           for sample in samples}
    record["reimported_images"] = len(samples)
    record["reimport_differences"] = sorted(key for key in expected.keys() | got.keys() if expected.get(key) != got.get(key))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000/api/v1")
    parser.add_argument("--rulebook-id")
    parser.add_argument("--rulebook-version", type=int)
    parser.add_argument("--resume", action="store_true", help="continue from run_state.json after an interruption")
    parser.add_argument("--draft-rulebook-id", help="an extracted, unreviewed draft to review instead of extracting again")
    arguments = parser.parse_args()
    api = Api(arguments.api)
    key = json.loads((HERE / "answer_key.json").read_text(encoding="utf-8"))
    status = api.call("GET", "/system/status")
    if status["person_model"]["checkpoint_file"] != "present":
        raise SystemExit("The person model file is not installed; the person checks would be unavailable.")

    print("1. Guideline -> rules", flush=True)
    book = rulebook(api, arguments)
    book_id, book_version = book["rulebook_id"], book["version"]
    results = {"seed": key["seed"], "rulebook": book,
               "execution_device": status["execution_device"], "datasets": {}}

    print("2. Import, check exact vertices, audit and score", flush=True)
    state_path = HERE / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if arguments.resume and state_path.is_file() else {}
    state.setdefault("datasets", {})
    state.setdefault("corrections", [])
    loaded = {}
    for name in DATASETS:
        path = HERE / key["datasets"][name]["archive"]
        documents = read_archive(path)
        if name not in state["datasets"]:
            dataset_id = api.import_zip(f"LabelMe flow: {name}", path.read_bytes(), path.name)
            audit_id, seconds = audit(api, dataset_id, book_id, book_version, f"LabelMe flow: {name}")
            state["datasets"][name] = {"dataset_id": dataset_id, "audit_id": audit_id, "audit_seconds": round(seconds, 1)}
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        dataset_id, audit_id = state["datasets"][name]["dataset_id"], state["datasets"][name]["audit_id"]
        # Revision 1 is what was uploaded and audited; a later correction never changes it.
        samples = [api.call("GET", f"/datasets/{dataset_id}/samples/{item['sample_id']}?revision=1")
                   for item in api.pages(f"/datasets/{dataset_id}/samples")]
        findings = api.pages(f"/audits/{audit_id}/findings")
        cases = key["cases"] if name == "labelme_people_errors" else []
        facts = key["datasets"][name]
        results["datasets"][name] = {
            "format": "labelme", "images": facts["images"], "annotations": facts["annotations"],
            "dataset_id": dataset_id, "audit_id": audit_id,
            "vertex_differences": exact_vertices(samples, documents),
            "integrity": {status: sum(sample["integrity_status"] == status for sample in samples)
                          for status in sorted({sample["integrity_status"] for sample in samples})},
            "seconds_per_image": round(state["datasets"][name]["audit_seconds"] / facts["images"], 1),
            **score(findings, samples, cases)}
        loaded[name] = (dataset_id, audit_id, samples, documents)

    # An injected error counts as newly reported only when a check reports it that did not already fail on the
    # same annotation in the unchanged file. The source outlines are human-drawn and the tool faults some as is.
    before = {(alarm["file"], alarm.get("shape_key"), alarm["check"])
              for alarm in results["datasets"]["labelme_people_clean"]["false_alarm_list"]}
    errors_row = results["datasets"]["labelme_people_errors"]
    already = sorted(
        case["file"] for case in key["cases"] if case["error"] and case["file"] in errors_row["found_by_file"]
        and all((case["file"], case["source_shape_key"], check) in before
                for check in errors_row["found_by_file"][case["file"]]))
    errors_row["errors_found_already_reported_on_the_unchanged_file"] = already
    errors_row["errors_newly_reported"] = errors_row["errors_found"] - len(already)

    print("3. Correct failing outlines and recheck them", flush=True)
    dataset_id, audit_id, samples, documents = loaded["labelme_people_errors"]
    clean = loaded["labelme_people_clean"][3]
    done = {record["file"] for record in state["corrections"]}
    for target in [case for case in key["cases"] if case["error"] == "head_cut" and case["file"] not in done]:
        stem = Path(target["file"]).stem
        sample = next(item for item in samples if item["sample_id"].rsplit("_", 1)[0] == stem)
        index = int(target["source_shape_key"].rsplit("/", 1)[1])
        state["corrections"].append({"file": target["file"], "error": target["error"], **correct_and_recheck(
            api, audit_id, dataset_id, sample, target["shape_key"], clean[stem]["shapes"][index]["points"])})
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    results["corrections"] = state["corrections"]

    print("4. Reviewer decisions, export and re-import", flush=True)
    if "export" not in state:
        state["export"] = review_and_export(api, audit_id, dataset_id, documents, state["corrections"])
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    results["export"] = state["export"]

    (HERE / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("newly reported:", errors_row["errors_newly_reported"], "already reported before injection:", already)
    print("\n| Dataset | Format | Images | Annotations | Errors injected | Errors found | False alarms | Seconds per image |")
    print("|---|---|---|---|---|---|---|---|")
    for name, row in results["datasets"].items():
        print(f"| {name} | {row['format']} | {row['images']} | {row['annotations']} | {row['errors_injected']} | "
              f"{row['errors_found']} | {row['false_alarms']} | {row['seconds_per_image']} |")
    print("\ncorrections:", json.dumps(results["corrections"], indent=2))
    print("export:", json.dumps(results["export"], indent=2))
    problems = [f"{name}: {row['vertex_differences'][:3]}" for name, row in results["datasets"].items()
                if row["vertex_differences"]]
    if problems:
        raise SystemExit(f"Vertices differ from the uploaded files: {problems}")


if __name__ == "__main__":
    main()
