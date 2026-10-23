"""Status and scoring for saved runs. Status never contacts a model; only scoring reads labels."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .prompts import CROP_VARIANT, CROP_VARIANTS, PACKET, RAW_VARIANT
from .run_files import PROGRESS_FILE, RESULTS_FILE, RUN_FILE, SCORE_FILE, SUMMARY_FILE, RunFiles, read_json, read_lines
from .scope import DEVELOPMENT
from .scoring import ScoredRow, assess, score_arm


class ReportError(RuntimeError):
    """A saved run or label file cannot be reported honestly."""


# --- status -------------------------------------------------------------------------------------

def seconds_between(first: str, last: str) -> float:
    return (datetime.fromisoformat(last) - datetime.fromisoformat(first)).total_seconds()


def open_unit(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The unit that started but has not finished, with the boxes it has completed so far."""
    started: dict[str, Any] | None = None
    boxes = 0
    for event in events:
        if event["event"] == "unit_started":
            started, boxes = event, 0
        elif event["event"] == "box_finished" and started is not None:
            boxes += 1
        elif event["event"] == "unit_finished":
            started = None
    return None if started is None else {**started, "boxes_finished": boxes}


def build_status(run_dir: Path) -> str:
    """Summarize saved progress without running inference or contacting a model."""
    if not (run_dir / RUN_FILE).exists():
        raise ReportError(f"No {RUN_FILE} in {run_dir}: the run never started or this is not a run directory.")
    run = read_json(run_dir / RUN_FILE)
    events = read_lines(run_dir / PROGRESS_FILE)
    results = read_lines(run_dir / RESULTS_FILE)
    summary = read_json(run_dir / SUMMARY_FILE) if (run_dir / SUMMARY_FILE).exists() else None
    stage = summary["stage"] if summary else "unfinished (running now, or stopped without a summary)"
    lines = [f"Run: {run['run_name']}  ({run['experiment_version']})",
             f"Stage: {stage}"]
    if summary and summary.get("reason"):
        lines.append(f"Reason: {summary['reason']}")
    lines.append(f"Planned units: {run['planned_units']}   Completed: {len(results)}   Arms: {', '.join(run['arms'])}")
    for arm in run["arms"]:
        rows = [r for r in results if r["arm"] == arm]
        outcomes: dict[str, int] = {}
        for row in rows:
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
        shown = ", ".join(f"{name} {count}" for name, count in sorted(outcomes.items())) or "none yet"
        lines.append(f"  {arm}: {len(rows)} done ({shown}); {sum(r['model_calls'] for r in rows)} model calls; "
                     f"{sum(r['duration_ms'] for r in rows) / 1000:.1f} s model-call time")
    pending = open_unit(events) if summary is None else None
    if pending:
        lines.append(f"In progress: {pending['case_name']} {pending['arm']} (unit {pending['unit']}/{pending['units_total']}; "
                     f"{pending['boxes_finished']} box results saved)")
    if events:
        lines.append(f"Started: {events[0]['time']}   Last event: {events[-1]['event']} at {events[-1]['time']}")
        lines.append(f"Time covered by saved events: {seconds_between(events[0]['time'], events[-1]['time']):.0f} s")
    if summary:
        if summary["not_run"]:
            lines.append("Not run: " + ", ".join(f"{u['case_name']}/{u['arm']}" for u in summary["not_run"]))
        if summary["source_files_changed"]:
            lines.append(f"WARNING: source files changed during the run: {summary['source_files_changed']}")
    lines.append("This command reads saved files only; it ran no inference.")
    return "\n".join(lines)


# --- scoring ------------------------------------------------------------------------------------

def load_labels(path: Path) -> dict[str, dict[str, Any]]:
    labels = read_json(path)
    cases = {entry["case_id"]: entry for entry in labels["cases"]}
    if len(cases) != len(labels["cases"]):
        raise ReportError("Duplicate case identifiers in the label file.")
    for name, entry in cases.items():
        if entry["decision"] not in ("pass", "fail"):
            raise ReportError(f"Unsupported label decision for {name}.")
    return cases


def reference_metrics(packet: Path) -> dict[str, Any]:
    """Historical Mac measurements, shown for context only. They are not the paired control."""
    reference = packet / "reference_results"
    out: dict[str, Any] = {}
    for key, name, path in (("historical_raw_baseline", "baseline", "reviewed_comparison.json"),
                            ("historical_one_example_candidate", "candidate", "reviewed_comparison.json"),
                            ("historical_numbered_overview", "candidate", "numbered_overview.json")):
        if (reference / path).exists():
            out[key] = read_json(reference / path)["metrics"][name]
    return out


def box_summary(box: dict[str, Any]) -> dict[str, Any]:
    return {key: box[key] for key in ("box_number", "object_id", "outcome", "reason", "duration_ms")}


def case_row(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    reason = record["reason"] if record["boxes"] or not record["prediction"] else record["prediction"]["reason"]
    return {"outcome": record["outcome"], "reason": reason, "duration_ms": record["duration_ms"],
            "model_calls": record["model_calls"], "boxes": [box_summary(b) for b in record["boxes"]]}


def build_score(run_dir: Path, labels_path: Path, packet: Path = PACKET) -> dict[str, Any]:
    run = read_json(run_dir / RUN_FILE)
    results = read_lines(run_dir / RESULTS_FILE)
    labels = load_labels(labels_path)
    intended = sorted(name for name, entry in labels.items() if entry["partition"] == DEVELOPMENT)
    by_arm: dict[str, dict[str, dict[str, Any]]] = {arm: {} for arm in run["arms"]}
    for record in results:
        arm, name = record["arm"], record["case_name"]
        if arm not in by_arm or name not in run["cases"]:
            raise ReportError(f"Result is outside the saved run plan: {arm}/{name}.")
        if name in by_arm[arm]:
            raise ReportError(f"Duplicate result for {arm}/{name}.")
        by_arm[arm][name] = record
    for arm, records in by_arm.items():
        unlabeled = [name for name in records if name not in labels or labels[name]["partition"] != DEVELOPMENT]
        if unlabeled:
            raise ReportError(f"{arm} has results without a development label: {unlabeled}")
    if not intended:
        raise ReportError("The label file contains no intended development cases.")
    # A stopped or subset run must not shrink its denominator to completed work.
    # Keep missing cases visible as unavailable, while retaining None in the
    # case table so an unexecuted case is distinguishable from a failed request.
    metrics = {arm: score_arm([
        ScoredRow(name, labels[name]["decision"],
                  records[name]["outcome"] if name in records else "unavailable",
                  records[name]["duration_ms"] if name in records else 0.0)
        for name in intended
    ]) for arm, records in by_arm.items()}
    summary = read_json(run_dir / SUMMARY_FILE) if (run_dir / SUMMARY_FILE).exists() else None
    complete = (all(set(records) == set(intended) for records in by_arm.values())
                and summary is not None and summary["stage"] == "completed"
                and not summary.get("source_files_changed"))
    cases = sorted({name for records in by_arm.values() for name in records})
    table = [{"case_name": name, "expected": labels[name]["decision"],
              "arms": {arm: case_row(records.get(name)) for arm, records in by_arm.items()}} for name in intended]
    errors = {arm: {"false_negatives": [r["case_name"] for r in table if r["expected"] == "fail"
                                        and (r["arms"][arm] is None or r["arms"][arm]["outcome"] != "fail")],
                    "false_positives": [r["case_name"] for r in table if r["expected"] == "pass"
                                        and r["arms"][arm] and r["arms"][arm]["outcome"] == "fail"]}
              for arm in by_arm}
    comparison = None
    candidates = [arm for arm in metrics if arm in CROP_VARIANTS]
    candidate = candidates[0] if len(candidates) == 1 else None
    if RAW_VARIANT in metrics and candidate is not None:
        comparison = assess(metrics[RAW_VARIANT], metrics[candidate]) if complete else "inconclusive"
    return {
        "schema": "box-grounding-score-v1", "run_name": run["run_name"],
        "complete_paired_comparison": complete and len(by_arm) == 2 and bool(comparison),
        "intended_development_cases": intended, "cases_scored": cases,
        "missing_cases_by_arm": {arm: sorted(set(intended) - set(records)) for arm, records in by_arm.items()},
        "arms": {arm: m.model_dump(mode="json") for arm, m in metrics.items()},
        "assessment_crops_vs_raw_control": comparison, "candidate_arm": candidate,
        "identity_schema": run["execution"].get("identity_schema", "echo-v1"),
        "errors": errors, "cases": table,
        "model": {"digest": run["digest"], "execution": {k: run["execution"][k] for k in (
            "model", "num_ctx", "num_predict", "num_thread", "timeout_seconds")}},
        "order": run["order"], "environment": {k: run["environment"][k] for k in ("platform", "processor", "cpu_count", "total_memory_bytes", "python_version")},
        "ollama_version": run["ollama_version"], "timing_caveats": run["timing_caveats"],
        "historical_reference_not_a_paired_control": reference_metrics(packet),
        "limitations": [
            "Eight already-exposed development images with one human reviewer and controlled annotation changes.",
            "Only three violations: one case changes recall by 0.33. No significance or generalization claim is possible.",
            "A candidate that is not strictly better than the fresh control is not evidence of improvement.",
        ],
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_score(score: dict[str, Any]) -> str:
    lines = [f"Score for run {score['run_name']}",
             f"Identity schema: {score.get('identity_schema', 'echo-v1')}",
             "COMPLETE paired comparison over the development cases." if score["complete_paired_comparison"]
             else f"INCOMPLETE: scored {score['cases_scored']} of {score['intended_development_cases']}; "
                  "this is NOT the full paired eight-case report.", ""]
    header = f"{'arm':16}{'TP':>3}{'FP':>3}{'FN':>3}{'TN':>3}{'abst':>5}{'uns':>4}{'unav':>5}{'cov':>6}{'prec':>6}{'rec':>6}{'F1':>6}{'calls':>6}{'sec':>8}"
    lines.append(header)
    for arm, m in score["arms"].items():
        calls = sum(r["arms"][arm]["model_calls"] for r in score["cases"] if r["arms"][arm])
        lines.append(f"{arm:16}{m['tp']:>3}{m['fp']:>3}{m['fn']:>3}{m['tn']:>3}{m['negative_abstentions']:>5}"
                     f"{m['unsure']:>4}{m['unavailable']:>5}{pct(m['coverage']):>6}{pct(m['precision']):>6}"
                     f"{pct(m['recall']):>6}{pct(m['f1']):>6}{calls:>6}{m['total_duration_ms'] / 1000:>8.0f}")
    if score["assessment_crops_vs_raw_control"]:
        lines.append(f"Assessment of {score.get('candidate_arm', CROP_VARIANT)} against the fresh {RAW_VARIANT} control: "
                     f"{score['assessment_crops_vs_raw_control']}")
    lines += ["", "Per case (expected | " + " | ".join(score["arms"]) + "):"]
    for row in score["cases"]:
        cells = []
        for arm in score["arms"]:
            cell = row["arms"][arm]
            cells.append("not run" if cell is None else f"{cell['outcome']} ({cell['duration_ms'] / 1000:.0f}s)")
        flag = "" if all(c is not None and c.startswith(row["expected"]) for c in cells) else "   <-- differs from expected"
        lines.append(f"  {row['case_name']}  {row['expected']:5} | " + " | ".join(cells) + flag)
        for arm in score["arms"]:
            cell = row["arms"][arm]
            if cell and cell["boxes"]:
                for box in cell["boxes"]:
                    lines.append(f"      {arm} box {box['box_number']} (object {box['object_id']}): {box['outcome']}")
    for arm, found in score["errors"].items():
        lines.append(f"{arm}: false negatives {found['false_negatives'] or 'none'}; false positives {found['false_positives'] or 'none'}")
    lines += ["", f"Model digest: {score['model']['digest']['installed']}  (historical: {score['model']['digest']['historical']}; "
                  f"same: {score['model']['digest']['installed_matches_historical']})",
              f"Order: {score['order']}.  Hardware: {score['environment']['processor']} / {score['environment']['cpu_count']} logical CPUs.",
              "Historical Mac results (context only, not a paired control):"]
    for name, metric in score["historical_reference_not_a_paired_control"].items():
        lines.append(f"  {name}: TP {metric['tp']} FP {metric['fp']} FN {metric['fn']} TN {metric['tn']} F1 {pct(metric['f1'])}")
    lines += ["Limitations:"] + [f"  - {text}" for text in score["limitations"]]
    return "\n".join(lines)


def write_score(run_dir: Path, score: dict[str, Any]) -> Path:
    return RunFiles(run_dir).write_document(SCORE_FILE, score)
