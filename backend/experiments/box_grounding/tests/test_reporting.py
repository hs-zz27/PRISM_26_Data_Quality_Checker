"""Status reads saved files only; scoring is the one step that reads labels."""

import json

import httpx
import pytest

from backend.experiments.box_grounding.reporting import ReportError, build_score, build_status, format_score, write_score
from backend.experiments.box_grounding.run_files import RunFilesError

from box_helpers import FakeOllama, read_results, run_with


@pytest.fixture
def finished(corpus, tmp_path):
    """A complete paired run in which the raw control misses the case-02 violation and crops catch it."""
    def decide(content):
        crop = "box" in content
        raw_sees_it = not crop
        if crop and tuple(content["box"]["bbox"]) == (5, 4, 8, 6):
            return "fail", "Only background is visible."
        return ("pass", "All boxes enclose a pedestrian.") if raw_sees_it else ("pass", "A pedestrian is visible.")
    run_with(corpus, FakeOllama(decide=decide), tmp_path / "run")
    return tmp_path / "run"


def test_status_of_a_finished_run_reports_each_arm_without_any_network(finished, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("status must not create an HTTP client")
    monkeypatch.setattr(httpx, "Client", forbidden)
    text = build_status(finished)
    assert "Stage: completed" in text and "Planned units: 8   Completed: 8" in text
    assert "raw-image-v1: 4 done" in text and "box-crop-v1: 4 done" in text and "9 model calls" not in text
    assert "ran no inference" in text


def test_status_of_a_crashed_run_shows_the_unit_that_was_in_progress(finished):
    (finished / "summary.json").unlink()
    lines = (finished / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if json.loads(line)["event"] != "run_finished"]
    started = max(i for i, line in enumerate(kept) if json.loads(line)["event"] == "unit_started")
    (finished / "progress.jsonl").write_text("\n".join(kept[: started + 1]) + "\n", encoding="utf-8")  # stopped mid-unit
    results = (finished / "results.jsonl").read_text(encoding="utf-8").splitlines()
    (finished / "results.jsonl").write_text("\n".join(results[:-1]) + "\n", encoding="utf-8")
    text = build_status(finished)
    assert "unfinished" in text and "In progress: case-13 raw-image-v1" in text and "Completed: 7" in text


def test_status_tolerates_a_truncated_final_progress_line(finished):
    with (finished / "progress.jsonl").open("ab") as stream:
        stream.write(b'{"event": "unit_start')
    assert "Stage: completed" in build_status(finished)


def test_status_of_a_directory_that_is_not_a_run_is_an_explicit_error(tmp_path):
    with pytest.raises(ReportError, match="run.json"):
        build_status(tmp_path)


def test_scoring_reads_labels_and_reports_the_paired_comparison(finished, corpus):
    score = build_score(finished, corpus.labels_path, corpus.packet)
    assert score["complete_paired_comparison"] is True
    raw, crops = score["arms"]["raw-image-v1"], score["arms"]["box-crop-v1"]
    assert (raw["tp"], raw["fn"], raw["tn"], raw["negative_abstentions"]) == (0, 1, 2, 1)
    assert (crops["tp"], crops["fn"], crops["tn"], crops["negative_abstentions"]) == (1, 0, 2, 1)
    assert crops["unavailable"] == 1 and crops["coverage"] == 0.75  # the zero-object case stays in the denominator
    assert score["errors"]["raw-image-v1"]["false_negatives"] == ["case-02"] and score["errors"]["box-crop-v1"]["false_positives"] == []
    # Both arms hold an unavailable result (the zero-object case), so the production rule is inconclusive.
    assert score["assessment_crops_vs_raw_control"] == "inconclusive"
    case02 = next(c for c in score["cases"] if c["case_name"] == "case-02")
    assert case02["expected"] == "fail" and [b["outcome"] for b in case02["arms"]["box-crop-v1"]["boxes"]] == ["pass", "fail", "pass"]
    assert score["model"]["digest"]["installed"].startswith("sha256:") and score["order"] == "interleaved"


def test_without_unavailable_results_better_recall_is_assessed_as_improved_but_flagged_partial(corpus, tmp_path):
    def decide(content):
        caught = "box" in content and tuple(content["box"]["bbox"]) == (5, 4, 8, 6)
        return ("fail", "Only background is visible.") if caught else ("pass", "All boxes enclose a pedestrian.")
    run_with(corpus, FakeOllama(decide=decide), tmp_path / "run", cases=("case-02", "case-03", "case-04"))
    score = build_score(tmp_path / "run", corpus.labels_path, corpus.packet)
    assert score["assessment_crops_vs_raw_control"] == "improved"
    assert score["complete_paired_comparison"] is False  # case-13 was intended but not run


def test_a_partial_run_is_labelled_incomplete_not_presented_as_the_full_comparison(corpus, tmp_path):
    run_with(corpus, FakeOllama(), tmp_path / "run", cases=("case-03",))
    score = build_score(tmp_path / "run", corpus.labels_path, corpus.packet)
    assert score["complete_paired_comparison"] is False
    assert "INCOMPLETE" in format_score(score) and "NOT the full paired" in format_score(score)


def test_the_report_keeps_worse_outcomes_historical_context_and_limitations_visible(finished, corpus):
    text = format_score(build_score(finished, corpus.labels_path, corpus.packet))
    assert "Per case" in text and "false negatives" in text and "Historical Mac results" in text
    assert "not a paired control" in text and "Limitations:" in text and "case-13" in text
    assert "<-- differs from expected" in text  # a miss is flagged, never hidden


def test_scoring_refuses_results_without_a_development_label(finished, corpus):
    labels = json.loads(corpus.labels_path.read_bytes())
    labels["cases"] = [c for c in labels["cases"] if c["case_id"] != "case-04"]
    corpus.labels_path.write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(ReportError, match="without a development label"):
        build_score(finished, corpus.labels_path, corpus.packet)


def test_a_score_file_is_written_once_and_never_overwritten(finished, corpus):
    score = build_score(finished, corpus.labels_path, corpus.packet)
    path = write_score(finished, score)
    assert json.loads(path.read_bytes())["run_name"] == "run"
    with pytest.raises(RunFilesError, match="Refusing to overwrite"):
        write_score(finished, score)


def test_scoring_changes_no_saved_run_record(finished, corpus):
    before = {p.name: p.read_bytes() for p in finished.iterdir() if p.is_file()}
    write_score(finished, build_score(finished, corpus.labels_path, corpus.packet))
    after = {p.name: p.read_bytes() for p in finished.iterdir() if p.is_file() and p.name != "score.json"}
    assert after == before and read_results(finished)
