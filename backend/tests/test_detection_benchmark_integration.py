"""Benchmark options added for audit integration: evidence listing and isolated-process timing."""

import json

from backend.benchmarks import benchmark_detection as benchmark
from backend.tests.test_detection_benchmark import (  # noqa: F401 - reuse fixtures and helpers
    assert_refused,
    base,
    comparison_args,
    workspace,
    write_annotations,
)


def test_evidence_listing_needs_annotations(workspace, capsys):  # noqa: F811
    assert_refused([*base(workspace), "--include-evidence"], capsys, "--include-evidence only applies")


def test_a_negative_isolated_run_count_is_refused(workspace, capsys):  # noqa: F811
    assert_refused([*base(workspace), "--isolated-runs", "-1"], capsys, "--isolated-runs")


def test_full_evidence_can_be_listed_for_manual_review(workspace):  # noqa: F811
    annotations = write_annotations(workspace)
    output = workspace.root / "report.json"
    code = benchmark.main(comparison_args(
        workspace, annotations, "--warmup", "0", "--iterations", "1", "--include-evidence", "--output", str(output),
    ))
    assert code == 0
    files = json.loads(output.read_text(encoding="utf-8"))["images"]["files"]
    first = files[0]["evidence"]
    assert first["status"] == "compared" and first["evidence_schema_version"] == 1
    assert len(first["matching"]["matches"]) == 1
    assert files[1]["evidence"]["matching"]["matches"] == []  # b.png has no annotations
    # Evidence from the benchmark is the same JSON the audit stores; it holds no local paths.
    assert str(workspace.root) not in json.dumps(files)


def test_evidence_is_left_out_unless_requested(workspace):  # noqa: F811
    annotations = write_annotations(workspace)
    output = workspace.root / "report.json"
    benchmark.main(comparison_args(workspace, annotations, "--warmup", "0", "--iterations", "1", "--output", str(output)))
    files = json.loads(output.read_text(encoding="utf-8"))["images"]["files"]
    assert all("evidence" not in entry for entry in files)


def test_isolated_timing_is_reported_only_when_requested(workspace, monkeypatch):  # noqa: F811
    monkeypatch.setattr(benchmark, "_run_isolated", lambda args, annotations: {"runs": args.isolated_runs})
    output = workspace.root / "report.json"
    benchmark.main([*base(workspace), "--warmup", "0", "--iterations", "1", "--output", str(output)])
    assert json.loads(output.read_text(encoding="utf-8"))["isolated_process_runs"] is None

    second = workspace.root / "report2.json"
    benchmark.main([
        *base(workspace), "--warmup", "0", "--iterations", "1", "--isolated-runs", "2", "--output", str(second),
    ])
    assert json.loads(second.read_text(encoding="utf-8"))["isolated_process_runs"] == {"runs": 2}


def test_an_isolated_run_with_an_unusable_checkpoint_stops_with_a_clear_error(workspace, capsys):  # noqa: F811
    """Starts a real spawned process; the fake checkpoint is not a model, so it must fail cleanly."""
    output = workspace.root / "report.json"
    code = benchmark.main([
        *base(workspace), "--warmup", "0", "--iterations", "1", "--isolated-runs", "1", "--output", str(output),
    ])
    assert code == 1
    assert "an isolated run failed" in capsys.readouterr().err
    assert not output.exists()
