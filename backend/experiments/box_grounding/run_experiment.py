"""Command line for the box-grounding experiment: check-scope, run, status and score.

Run from the repository root on Windows, macOS or Linux, for example:

    python -B -m backend.experiments.box_grounding.run_experiment check-scope --scope .cache/box-grounding/inputs/scope.json
    python -B -m backend.experiments.box_grounding.run_experiment run --scope ... --run-name smoke --cases case-03
    python -B -m backend.experiments.box_grounding.run_experiment status --run-dir .cache/box-grounding/runs/smoke
    python -B -m backend.experiments.box_grounding.run_experiment score --run-dir ... --labels backend/experiments/box_grounding/reviewed_labels.json

Only "score" reads expected labels. Nothing here changes the application, contracts or database.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Sequence

import httpx
from pydantic import ValidationError

from .crops import render_box_crops, validate_scope
from .prompts import PACKET, VARIANTS, load_prompt_texts
from .reporting import ReportError, build_score, build_status, format_score, write_score
from .run_files import DEFAULT_RUNS_ROOT, RunFilesError
from .runner import RunConfig, RunError, Say, ascii_text, case_origin, print_line, run_experiment
from .scope import DEVELOPMENT, ScopeError, load_scope
from .settings import DEFAULT_HOST, DEFAULT_MODEL, SettingsError
from .views import RenderError, ViewLimits

RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
EXPECTED_CASES = {"training": 3, DEVELOPMENT: 8}


def threads(value: str) -> int | None:
    if value == "auto":
        return None
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("threads must be 'auto' or a positive integer")
    return int(value)


def comma_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check-scope", help="Verify the prepared corpus and print the crop coordinates. No model contact.")
    check.add_argument("--scope", type=Path, required=True)

    run = commands.add_parser("run", help="Run the raw control and the per-box crop candidate, one request at a time.")
    run.add_argument("--scope", type=Path, required=True)
    run.add_argument("--run-name", required=True, help="New directory name under --runs-root; existing runs are never reused.")
    run.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    run.add_argument("--cases", type=comma_list, default=None, help="Comma-separated development cases. Default: all.")
    run.add_argument("--arms", type=comma_list, default=VARIANTS, help=f"Comma-separated, from {list(VARIANTS)}.")
    run.add_argument("--order", choices=("interleaved", "blocked"), default="interleaved")
    run.add_argument("--host", default=DEFAULT_HOST, help="Loopback Ollama URL.")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--model-digest", default="installed", help="installed, historical or sha256:<digest>.")
    run.add_argument("--threads", type=threads, default=None, help="auto (default) or a fixed CPU thread count.")
    run.add_argument("--num-ctx", type=int, default=16384)
    run.add_argument("--num-predict", type=int, default=2048)
    run.add_argument("--timeout-seconds", type=int, default=600)
    run.add_argument("--max-request-bytes", type=int, default=64 * 1024**2)
    run.add_argument("--max-response-bytes", type=int, default=65536)
    run.add_argument("--keep-alive-seconds", type=int, default=300)
    run.add_argument("--max-consecutive-failures", type=int, default=3)
    run.add_argument("--dry-run", action="store_true", help="Check scope and request sizes; send and write nothing.")

    status = commands.add_parser("status", help="Summarize saved progress. Runs no inference.")
    status.add_argument("--run-dir", type=Path, required=True)

    score = commands.add_parser("score", help="Score a finished run against reviewed labels (the only step that reads labels).")
    score.add_argument("--run-dir", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    return parser


def check_scope(scope_path: Path, say: Say) -> int:
    """Phase 1 verification: counts, label-freedom, coordinate conventions and crop sizes."""
    scope = load_scope(scope_path)
    texts = load_prompt_texts(PACKET)
    counts = {name: sum(case.partition == name for case in scope.cases) for name in EXPECTED_CASES}
    say(f"Scope: {scope_path}  (sha256 {scope.sha256[:12]})")
    say(f"Cases: {len(scope.cases)} ({counts.get('training', 0)} training, {counts.get(DEVELOPMENT, 0)} development). "
        f"Guided practice case-01 present: {any(c.case_name == 'case-01' for c in scope.cases)}.")
    say("Label-free: yes (no expected labels, reviewer fields or training answers were found).")
    say(f"System prompt hash matches the scope: {scope.document.get('system_prompt_sha256') == texts.system_sha256}.")
    problems = []
    if counts != EXPECTED_CASES or any(case.case_name == "case-01" for case in scope.cases):
        problems.append("Expected exactly three training and eight development cases, without case-01.")
    limits = ViewLimits()
    say("Coordinates: source boxes are one-based inclusive; PIL crops are (xmin-1, ymin-1, xmax, ymax).")
    for case in scope.cases:
        say(f"  {case.case_name} [{case.partition}] {case.image_size[0]}x{case.image_size[1]}, "
            f"{len(case.objects)} boxes, image sha256 {case.inputs['image_sha256'][:12]}")
        try:
            validate_scope(case.image_bytes, case.objects, case_origin(case), limits)
            crops = render_box_crops(case.image_bytes, case.objects, case_origin(case), limits)
        except RenderError as error:
            problems.append(f"{case.case_name}: {error}")
            say(f"      REJECTED: {error}")
            continue
        for crop in crops:
            say(f"      box {crop.box_number} (index {crop.annotation_index}, object {crop.object_id}, {ascii_text(crop.label)}): "
                f"bbox {list(crop.bbox)} -> crop {list(crop.pil_box)} = {crop.width}x{crop.height} px")
    ids = {c.case_name: [o["object_id"] for o in c.objects] for c in scope.cases}
    repeated = {name: sorted({i for i in values if values.count(i) > 1}) for name, values in ids.items()}
    say("Repeated object IDs (kept as distinct boxes): " + (str({k: v for k, v in repeated.items() if v}) or "none"))
    say("PROBLEMS: " + "; ".join(problems) if problems else "Scope check passed. No model was contacted.")
    return 1 if problems else 0


def run_command(args: argparse.Namespace, transport: httpx.BaseTransport | None, say: Say) -> int:
    if not RUN_NAME.fullmatch(args.run_name):
        raise RunError("--run-name may contain letters, digits, dot, underscore and hyphen only.")
    config = RunConfig(
        scope_path=args.scope, run_dir=args.runs_root / args.run_name, arms=tuple(args.arms), order=args.order,
        cases=args.cases, host=args.host, model=args.model, digest_request=args.model_digest,
        num_ctx=args.num_ctx, num_predict=args.num_predict, num_thread=args.threads,
        timeout_seconds=args.timeout_seconds, max_request_bytes=args.max_request_bytes,
        max_response_bytes=args.max_response_bytes, keep_alive_seconds=args.keep_alive_seconds,
        max_consecutive_failures=args.max_consecutive_failures, dry_run=args.dry_run,
    )
    summary = run_experiment(config, transport=transport, say=say, packet=PACKET)
    return {"completed": 0, "dry-run": 0, "aborted": 2, "interrupted": 130}.get(summary["stage"], 1)


def main(argv: Sequence[str] | None = None, *, transport: httpx.BaseTransport | None = None,
         say: Say = print_line) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check-scope":
            return check_scope(args.scope, say)
        if args.command == "run":
            return run_command(args, transport, say)
        if args.command == "status":
            say(build_status(args.run_dir))
            return 0
        score = build_score(args.run_dir, args.labels, PACKET)
        path = write_score(args.run_dir, score)
        say(format_score(score))
        say(f"Score saved to {path}.")
        return 0
    except (RunError, ScopeError, ReportError, RunFilesError, SettingsError, ValidationError, OSError) as error:
        say(f"ERROR: {ascii_text(str(error))}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
