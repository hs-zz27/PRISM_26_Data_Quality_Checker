"""Command-line behavior: check-scope, run, status, score and exit codes."""

import httpx
import pytest

from backend.experiments.box_grounding import run_experiment as cli

from box_helpers import CaseSpec, FakeOllama, build_corpus


@pytest.fixture(autouse=True)
def synthetic_packet(request, monkeypatch):
    """Point the CLI at the synthetic corpus's prompt packet instead of the real one."""
    if "corpus" in request.fixturenames:
        monkeypatch.setattr(cli, "PACKET", request.getfixturevalue("corpus").packet)


def invoke(argv, fake=None):
    lines: list[str] = []
    code = cli.main(argv, transport=None if fake is None else fake.transport(), say=lines.append)
    return code, "\n".join(lines)


def eleven_cases():
    box = ((1, "PASpersonStanding", (1, 1, 3, 3)),)
    specs = [CaseSpec(f"case-{n:02d}", "training" if n in (6, 7, 8) else "development", (6, 6), box) for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)]
    return tuple(specs)


def test_check_scope_passes_for_three_training_and_eight_development_cases_and_shows_every_crop(tmp_path, monkeypatch):
    corpus = build_corpus(tmp_path / "c", eleven_cases())
    monkeypatch.setattr(cli, "PACKET", corpus.packet)
    code, text = invoke(["check-scope", "--scope", str(corpus.scope_path)])
    assert code == 0 and "Cases: 11 (3 training, 8 development)" in text
    assert "Guided practice case-01 present: False" in text and "Label-free: yes" in text
    assert "bbox [1, 1, 3, 3] -> crop [0, 0, 3, 3] = 3x3 px" in text
    assert "Scope check passed. No model was contacted." in text


def test_check_scope_flags_a_corpus_of_the_wrong_shape_and_shows_repeated_ids(corpus):
    code, text = invoke(["check-scope", "--scope", str(corpus.scope_path)])
    assert code == 1 and "Expected exactly three training and eight development cases" in text
    assert "case-13" in text and "REJECTED" in text and "'case-02': [1]" in text


def test_dry_run_through_the_cli_exits_zero_and_writes_nothing(corpus, tmp_path):
    root = tmp_path / "runs"
    code, text = invoke(["run", "--scope", str(corpus.scope_path), "--run-name", "plan", "--runs-root", str(root), "--dry-run"])
    assert code == 0 and "Dry run" in text and not root.exists()


@pytest.mark.parametrize("name", ["", "../escape", "a b", ".hidden", "x" * 80])
def test_invalid_run_names_are_refused(corpus, tmp_path, name):
    code, text = invoke(["run", "--scope", str(corpus.scope_path), "--run-name", name or "''", "--runs-root", str(tmp_path / "r")])
    assert code != 0


def test_a_full_cli_cycle_run_status_score_with_the_documented_exit_codes(corpus, tmp_path):
    root = tmp_path / "runs"
    common = ["--scope", str(corpus.scope_path), "--runs-root", str(root)]
    code, _ = invoke(["run", *common, "--run-name", "full", "--threads", "2"], FakeOllama())
    assert code == 0
    code, text = invoke(["status", "--run-dir", str(root / "full")])
    assert code == 0 and "Stage: completed" in text
    code, text = invoke(["score", "--run-dir", str(root / "full"), "--labels", str(corpus.labels_path)])
    assert code == 0 and "COMPLETE paired comparison" in text and (root / "full/score.json").exists()
    code, text = invoke(["score", "--run-dir", str(root / "full"), "--labels", str(corpus.labels_path)])
    assert code == 1 and "ERROR: Refusing to overwrite" in text
    code, text = invoke(["run", *common, "--run-name", "full"], FakeOllama())
    assert code == 1 and "already exists" in text


def test_an_aborted_run_exits_with_code_two(corpus, tmp_path):
    def down(count, reply, request):
        raise httpx.ConnectError("down", request=request)
    code, text = invoke(["run", "--scope", str(corpus.scope_path), "--run-name", "bad", "--runs-root", str(tmp_path / "r")],
                        FakeOllama(behavior=down))
    assert code == 2 and "Run aborted" in text


@pytest.mark.parametrize("value,expected", [("auto", None), ("4", 4)])
def test_thread_option_accepts_auto_or_a_positive_integer(value, expected):
    assert cli.threads(value) == expected


@pytest.mark.parametrize("value", ["0", "-1", "many", "1.5", ""])
def test_thread_option_rejects_everything_else(value):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        cli.threads(value)


def test_output_is_console_safe_ascii_even_for_non_ascii_model_text():
    from backend.experiments.box_grounding.runner import ascii_text
    assert ascii_text("caf\u00e9 \u4eba") == "caf? ?"


def test_non_loopback_hosts_are_refused_with_a_clear_error(corpus, tmp_path):
    code, text = invoke(["run", "--scope", str(corpus.scope_path), "--run-name", "x", "--runs-root", str(tmp_path / "r"),
                         "--host", "http://192.168.1.9:11434"], FakeOllama())
    assert code == 1 and "loopback" in text
