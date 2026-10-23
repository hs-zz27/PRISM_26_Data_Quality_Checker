"""The sequential runner end to end against a fake local server: retention, order, failures, safety."""

from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from backend.experiments.box_grounding.run_files import RunFilesError
from backend.experiments.box_grounding.runner import RunConfig, RunError, run_experiment

from box_helpers import DIGEST, HISTORICAL, FakeOllama, failing_boxes, make_config, read_events, read_results, run_with

DEV = {"case-02": 3, "case-03": 1, "case-04": 2, "case-13": 0}  # annotated boxes per development case


def timeout(count, reply, request):
    raise httpx.ReadTimeout("slow", request=request)


def refuse_network(request):
    raise AssertionError("no network contact is allowed here")


# --- retention and provenance ----------------------------------------------------------------------


def test_v2_keeps_geometry_locally_and_labels_requests_events_and_scores(corpus, fake, tmp_path):
    from backend.experiments.box_grounding.reporting import build_score, format_score

    run_dir = tmp_path / "v2"
    summary, _ = run_with(corpus, fake, run_dir, arms=("raw-image-v1", "box-crop-v2"))
    assert summary["stage"] == "completed" and summary["model_calls"] == 9
    cases = {case.case_name: case for case in corpus.scope().cases}
    for record in read_results(run_dir):
        assert record["arm"] in {"raw-image-v1", "box-crop-v2"}
        for box in record["boxes"]:
            source = cases[record["case_name"]].objects[box["annotation_index"]]
            assert box["bbox"] == source["bbox"]
            assert box["object_id"] == source["object_id"]
            assert sha256((run_dir / box["crop_file"]).read_bytes()).hexdigest() == box["crop_sha256"]
    crop_inputs = [json.loads(body["messages"][1]["content"]) for body in fake.chat_bodies]
    crop_inputs = [content for content in crop_inputs if "box" in content]
    assert len(crop_inputs) == 6
    for content in crop_inputs:
        assert content["task"]["variant"] == "box-crop-v2"
        assert not {"bbox", "bbox_convention", "source_image_size"} & content["box"].keys()
    events = [e for e in read_events(run_dir) if e["event"] in {"box_started", "box_finished"}]
    assert len(events) == 12 and all(e["arm"] == "box-crop-v2" for e in events)
    run = json.loads((run_dir / "run.json").read_bytes())
    assert run["variants"]["box-crop-v2"]["source_geometry_in_model_input"] is False
    assert run["variants"]["box-crop-v2"]["source_geometry_in_local_records"] is True
    score = build_score(run_dir, corpus.labels_path, corpus.packet)
    assert score["candidate_arm"] == "box-crop-v2" and score["complete_paired_comparison"]
    assert "Assessment of box-crop-v2 against" in format_score(score)


def test_two_crop_versions_require_separate_paired_runs(corpus, tmp_path):
    with pytest.raises(RunError, match="Choose one crop version"):
        make_config(corpus, tmp_path / "invalid", arms=("box-crop-v1", "box-crop-v2"))


def test_bound_identity_mode_reaches_both_arms_and_saved_reports(corpus, fake, tmp_path):
    from backend.experiments.box_grounding.reporting import build_score, format_score

    run_dir = tmp_path / "bound"
    run_with(corpus, fake, run_dir, arms=("raw-image-v1", "box-crop-v2"), identity_schema="bound-v1")
    for body in fake.chat_bodies:
        content = json.loads(body["messages"][1]["content"])
        fields = body["format"]["properties"]
        assert fields["case_id"]["const"] == content["case_id"]
        assert fields["rule_id"]["const"] == content["rule_id"]
        if "box" in content:
            assert fields["box_number"]["const"] == content["box"]["box_number"]
            assert fields["object_id"]["const"] == content["box"]["object_id"]
    run = json.loads((run_dir / "run.json").read_bytes())
    assert run["execution"]["identity_schema"] == run["provenance"]["identity_schema"] == "bound-v1"
    score = build_score(run_dir, corpus.labels_path, corpus.packet)
    assert score["identity_schema"] == "bound-v1"
    assert "Identity schema: bound-v1" in format_score(score)

def test_every_case_arm_and_box_is_retained_in_the_results(corpus, fake, tmp_path):
    summary, _ = run_with(corpus, fake, tmp_path / "run")
    results = read_results(tmp_path / "run")
    assert summary["stage"] == "completed" and summary["completed_units"] == summary["planned_units"] == 8
    assert {(r["arm"], r["case_name"]) for r in results} == {(a, c) for a in ("raw-image-v1", "box-crop-v1") for c in DEV}
    for record in results:
        assert record["boxes_in_source"] == DEV[record["case_name"]]
        if record["arm"] == "box-crop-v1" and DEV[record["case_name"]]:
            assert [b["box_number"] for b in record["boxes"]] == list(range(1, DEV[record["case_name"]] + 1))
    assert not any(r["case_name"] == "case-06" for r in results)  # training cases are not part of this comparison


def test_repeated_object_ids_stay_separate_boxes_in_the_record(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")
    crop = next(r for r in read_results(tmp_path / "run") if r["arm"] == "box-crop-v1" and r["case_name"] == "case-02")
    assert [b["object_id"] for b in crop["boxes"]] == [1, 1, 3]
    assert [b["annotation_index"] for b in crop["boxes"]] == [0, 1, 2]
    assert len({b["crop_sha256"] for b in crop["boxes"]}) == 3


def test_an_unrepresentable_case_is_retained_as_unavailable_with_zero_requests(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")
    zero = [r for r in read_results(tmp_path / "run") if r["case_name"] == "case-13"]
    assert len(zero) == 2
    for record in zero:
        assert (record["outcome"], record["reason"], record["model_calls"]) == ("unavailable", "unsupported_origin_or_box_count", 0)
        assert record["aggregate_reason"] == "rejected_before_request"
    assert fake.chat_count == 3 + 6  # three raw requests plus six crop requests; never one for the zero-object case


def test_hashes_describe_the_exact_requests_and_crops_that_were_sent(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")
    sent = {sha256(raw).hexdigest() for raw in fake.chat_raw}
    recorded = set()
    for record in read_results(tmp_path / "run"):
        if record["request_sha256"]:
            recorded.add(record["request_sha256"])
        for box in record["boxes"]:
            recorded.add(box["request_sha256"])
            assert sha256((tmp_path / "run" / box["crop_file"]).read_bytes()).hexdigest() == box["crop_sha256"]
    assert recorded == sent


def test_settings_versions_and_provenance_are_written_before_any_inference(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run", num_thread=3, order="blocked")
    run = json.loads((tmp_path / "run/run.json").read_bytes())
    assert run["execution"]["num_thread"] == 3 and run["execution"]["model_digest"] == DIGEST
    assert run["execution"]["temperature"] == 0 and run["execution"]["num_ctx"] == 16384
    assert run["digest"]["installed_matches_historical"] is False and run["digest"]["historical"] == HISTORICAL
    assert run["variants"]["box-crop-v1"]["crop_padding_pixels"] == 0 and run["variants"]["raw-image-v1"]["examples"] == 0
    assert run["provenance"]["rule"]["description"].startswith("Each annotation box")
    assert set(run["provenance"]["schema_sha256"]) == {"prediction-v1", "box-prediction-v1"}
    assert "runner.py" in run["provenance"]["code_sha256"] or "per_box_instruction_v1.txt" in run["provenance"]["code_sha256"]
    assert run["ollama_version"] == "0.0.0-test" and run["environment"]["python_version"]
    assert run["sequential"] is True and run["retries"] == "none" and run["labels_read_during_inference"] is False
    assert read_events(tmp_path / "run")[0]["event"] == "run_started"


def test_source_files_are_not_modified_by_a_run(corpus, fake, tmp_path):
    before = {p: p.read_bytes() for p in (corpus.scope_path.parent).rglob("*") if p.is_file()}
    summary, _ = run_with(corpus, fake, tmp_path / "run")
    assert {p: p.read_bytes() for p in before} == before and summary["source_files_changed"] == []


# --- sequencing and progress -------------------------------------------------------------------------

def test_requests_are_strictly_sequential(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")
    assert fake.max_in_flight == 1


def test_interleaved_order_alternates_the_first_arm_per_case(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")
    order = [(e["case_name"], e["arm"]) for e in read_events(tmp_path / "run") if e["event"] == "unit_started"]
    assert order == [("case-02", "raw-image-v1"), ("case-02", "box-crop-v1"), ("case-03", "box-crop-v1"),
                     ("case-03", "raw-image-v1"), ("case-04", "raw-image-v1"), ("case-04", "box-crop-v1"),
                     ("case-13", "box-crop-v1"), ("case-13", "raw-image-v1")]


def test_blocked_order_runs_one_arm_completely_first(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run", order="blocked")
    arms = [e["arm"] for e in read_events(tmp_path / "run") if e["event"] == "unit_started"]
    assert arms == ["raw-image-v1"] * 4 + ["box-crop-v1"] * 4


def test_progress_is_printed_before_and_after_each_sample_and_box(corpus, fake, tmp_path):
    _, lines = run_with(corpus, fake, tmp_path / "run")
    assert lines[0].startswith("Box-grounding experiment starting")
    text = "\n".join(lines)
    assert "[2/8] case-02 box-crop-v1: starting (3 annotation boxes)" in text
    assert "box 1/3 (object 1): sending crop 4x3" in text and "box 3/3 (object 3): pass in" in text
    assert "[2/8] case-02 box-crop-v1: done -> pass" in text and "Run completed: 8/8 units, 9 model calls." in text
    kinds = [e["event"] for e in read_events(tmp_path / "run")]
    assert kinds.count("box_started") == kinds.count("box_finished") == 6 and kinds[-1] == "run_finished"


def test_a_scope_subset_runs_only_the_named_development_cases(corpus, fake, tmp_path):
    summary, _ = run_with(corpus, fake, tmp_path / "run", cases=("case-03",))
    assert summary["planned_units"] == 2 and {r["case_name"] for r in read_results(tmp_path / "run")} == {"case-03"}


# --- outcomes -----------------------------------------------------------------------------------------

def test_a_supported_failing_box_fails_the_sample_and_names_the_box(corpus, tmp_path):
    fake = FakeOllama(decide=failing_boxes((5, 4, 8, 6)))
    run_with(corpus, fake, tmp_path / "run")
    results = {(r["arm"], r["case_name"]): r for r in read_results(tmp_path / "run")}
    crop = results[("box-crop-v1", "case-02")]
    assert (crop["outcome"], crop["reason"]) == ("fail", "supported_failing_box: boxes 2")
    assert [b["outcome"] for b in crop["boxes"]] == ["pass", "fail", "pass"]  # every box is judged, none skipped
    assert results[("raw-image-v1", "case-02")]["outcome"] == "fail"
    assert results[("box-crop-v1", "case-03")]["outcome"] == "pass"


def test_a_model_unsure_box_makes_the_sample_unsure_but_never_a_pass(corpus, tmp_path):
    def decide(content):
        return ("unsure", "Only part of a person is visible.") if "box" in content and content["box"]["box_number"] == 3 else ("pass", "ok")
    run_with(corpus, FakeOllama(decide=decide), tmp_path / "run")
    crop = next(r for r in read_results(tmp_path / "run") if r["arm"] == "box-crop-v1" and r["case_name"] == "case-02")
    assert (crop["outcome"], crop["reason"]) == ("unsure", "unsure_box: boxes 3")


def test_one_failed_box_request_is_unavailable_and_does_not_hide_a_supported_failure(corpus, tmp_path):
    # chat 1 is case-02 raw; chats 2-4 are its crops. Time out the first crop; fail the second.
    fake = FakeOllama(decide=failing_boxes((5, 4, 8, 6)), behavior=lambda c, r, req: timeout(c, r, req) if c == 2 else None)
    run_with(corpus, fake, tmp_path / "run")
    crop = next(r for r in read_results(tmp_path / "run") if r["arm"] == "box-crop-v1" and r["case_name"] == "case-02")
    assert [b["outcome"] for b in crop["boxes"]] == ["unavailable", "fail", "pass"]
    assert crop["boxes"][0]["reason"] == "request_timeout" and crop["boxes"][0]["model_verified"] is False
    assert crop["outcome"] == "fail"  # the supported failure wins over an unavailable box
    assert fake.chat_count == 9  # the timeout was not retried


def test_an_unavailable_box_without_a_failure_makes_the_sample_unavailable(corpus, tmp_path):
    fake = FakeOllama(behavior=lambda c, r, req: timeout(c, r, req) if c == 4 else None)  # case-02 crop 3
    run_with(corpus, fake, tmp_path / "run")
    crop = next(r for r in read_results(tmp_path / "run") if r["arm"] == "box-crop-v1" and r["case_name"] == "case-02")
    assert (crop["outcome"], crop["reason"]) == ("unavailable", "unavailable_box: boxes 3")


def test_an_oversized_request_keeps_its_case_in_coverage_instead_of_truncating(corpus, fake, tmp_path):
    summary, _ = run_with(corpus, fake, tmp_path / "run", max_request_bytes=1024)
    results = read_results(tmp_path / "run")
    assert summary["stage"] == "completed" and fake.chat_count == 0
    assert all(r["outcome"] == "unavailable" for r in results) and len(results) == 8
    assert {r["reason"] for r in results if r["arm"] == "raw-image-v1"} == {"context_limit", "unsupported_origin_or_box_count"}


# --- safety: aborts, interruption, overwrite, digest ---------------------------------------------------

def test_a_broken_server_stops_the_run_instead_of_burning_hours(corpus, tmp_path):
    def down(count, reply, request):
        raise httpx.ConnectError("down", request=request)
    fake = FakeOllama(behavior=down)
    summary, _ = run_with(corpus, fake, tmp_path / "run", max_consecutive_failures=3)
    assert summary["stage"] == "aborted" and "consecutive server failures" in summary["reason"]
    assert fake.chat_count == 3 and summary["not_run"]  # no further requests after the third failure
    assert json.loads((tmp_path / "run/summary.json").read_bytes())["stage"] == "aborted"


def test_an_interrupted_run_preserves_partial_results_and_says_so(corpus, tmp_path):
    def interrupt(count, reply, request):
        if count == 3:
            raise KeyboardInterrupt
    fake = FakeOllama(behavior=interrupt)
    summary, lines = run_with(corpus, fake, tmp_path / "run")
    assert summary["stage"] == "interrupted" and summary["completed_units"] == 1  # case-02 raw finished
    assert len(read_results(tmp_path / "run")) == 1 and read_events(tmp_path / "run")[-1]["stage"] == "interrupted"
    assert any("Run interrupted" in line for line in lines)


def test_an_unexpected_error_is_recorded_and_not_swallowed(corpus, tmp_path):
    def boom(count, reply, request):
        raise RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        run_with(corpus, FakeOllama(behavior=boom), tmp_path / "run")
    assert json.loads((tmp_path / "run/summary.json").read_bytes())["stage"] == "failed"


def test_an_existing_run_is_never_overwritten_or_resumed(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")
    snapshot = {p.name: p.read_bytes() for p in (tmp_path / "run").iterdir() if p.is_file()}
    second = FakeOllama()
    with pytest.raises(RunFilesError, match="already exists"):
        run_with(corpus, second, tmp_path / "run")
    assert second.calls == []  # refused before any network contact
    assert {p.name: p.read_bytes() for p in (tmp_path / "run").iterdir() if p.is_file()} == snapshot


def test_a_refused_digest_leaves_no_run_directory_and_sends_no_inference(corpus, tmp_path):
    fake = FakeOllama()
    with pytest.raises(RunError, match="differs from the requested"):
        run_with(corpus, fake, tmp_path / "run", digest_request="historical")
    assert fake.chat_count == 0 and not (tmp_path / "run").exists()
    with pytest.raises(RunError):
        run_with(corpus, fake, tmp_path / "run2", digest_request="sha256:" + "99" * 32)
    assert not (tmp_path / "run2").exists()


def test_the_historical_digest_can_be_demanded_and_is_recorded(corpus, tmp_path):
    fake = FakeOllama(digest=HISTORICAL)
    _, lines = run_with(corpus, fake, tmp_path / "run", digest_request="historical", cases=("case-03",))
    run = json.loads((tmp_path / "run/run.json").read_bytes())
    assert run["digest"]["installed_matches_historical"] is True and not any("WARNING" in line for line in lines)


def test_a_differing_installed_digest_is_reported_up_front(corpus, fake, tmp_path):
    _, lines = run_with(corpus, fake, tmp_path / "run", cases=("case-03",))
    assert any("WARNING" in line and "differs from the historical" in line for line in lines)


def test_a_digest_that_changes_mid_run_makes_results_unavailable_not_trusted(corpus, tmp_path):
    fake = FakeOllama(digest_after_chat="sha256:" + "33" * 32)
    summary, _ = run_with(corpus, fake, tmp_path / "run", cases=("case-03",), max_consecutive_failures=2)
    first = read_results(tmp_path / "run")[0]
    assert first["outcome"] == "unavailable" and first["reason"] == "model_identity_changed"


def test_a_dry_run_checks_everything_but_sends_and_writes_nothing(corpus, tmp_path):
    lines: list[str] = []
    summary = run_experiment(make_config(corpus, tmp_path / "run", dry_run=True),
                             transport=httpx.MockTransport(refuse_network), say=lines.append, packet=corpus.packet)
    assert summary == {"stage": "dry-run", "planned_units": 8} and not (tmp_path / "run").exists()
    text = "\n".join(lines)
    assert "Total chat requests that would be sent: 9." in text and "case-13: 0 boxes -> REJECTED" in text


def test_a_scope_with_labels_is_refused_before_any_network_contact(corpus, tmp_path):
    document = json.loads(corpus.scope_path.read_bytes())
    document["cases"][0]["expected"] = "fail"
    corpus.scope_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(Exception, match="label or review field"):
        run_experiment(make_config(corpus, tmp_path / "run"), transport=httpx.MockTransport(refuse_network),
                       say=lambda _: None, packet=corpus.packet)


def test_a_changed_system_prompt_is_refused(corpus, fake, tmp_path):
    (corpus.packet / "system_prompt_v1.txt").write_bytes(b"A different prompt.")
    with pytest.raises(RunError, match="system prompt changed"):
        run_with(corpus, fake, tmp_path / "run")
    assert fake.calls == []


# --- truth leakage ---------------------------------------------------------------------------------------

def test_inference_never_opens_the_labels_file(corpus, fake, tmp_path, monkeypatch):
    opened: list[str] = []
    real_open, real_read = Path.open, Path.read_bytes

    def tracking_open(self, *args, **kwargs):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    def tracking_read(self):
        opened.append(str(self))
        return real_read(self)

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(Path, "read_bytes", tracking_read)
    run_with(corpus, fake, tmp_path / "run")
    assert opened and not any("labels" in name for name in opened)


def test_no_request_or_result_contains_an_expected_label_or_decision(corpus, fake, tmp_path):
    run_with(corpus, fake, tmp_path / "run")

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    forbidden = {"expected", "decision", "reviewer_id", "adjudication", "training_example"}
    for body in fake.chat_bodies:
        assert not forbidden & set(keys(body)) and not forbidden & set(keys(json.loads(body["messages"][1]["content"])))
    for record in read_results(tmp_path / "run"):
        assert not forbidden & set(keys(record))


def test_run_configuration_rejects_invalid_choices(corpus, tmp_path):
    base = dict(scope_path=corpus.scope_path, run_dir=tmp_path / "run")
    for bad in ({"arms": ()}, {"arms": ("raw-image-v1", "raw-image-v1")}, {"arms": ("other",)}, {"order": "random"},
                {"digest_request": "latest"}, {"max_consecutive_failures": 0}):
        with pytest.raises(RunError):
            RunConfig(**base, **bad)
