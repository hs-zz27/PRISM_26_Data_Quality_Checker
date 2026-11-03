#!/usr/bin/env python3
"""Build an --expectations file for compare_visual_audit.py from the evaluation pack's answer key.

    python -B evaluation/annotation_error_pack/tools/expectations_from_answer_key.py \
        --case C01 --rulebook rulebook.json --out expected_C01.json

For one case (C01 .. C05, L01 .. L03) it writes the human-reviewed expectation for the pair's TARGET annotation under
the rule whose text starts with the case's primary guideline number ("6. ..." for guideline 6): the baseline entry
and the altered entry, keyed by the annotation's stable key in the packaged archive (``packaged_json_pointer``).
The file is read only after a comparison has produced its results; it never enters a model request. Only the
target annotation is listed, so every other annotation in the image has no expectation and is not scored.

Read ``human_review.md`` before relying on a pair: some baseline guideline checks there are marked Uncertain, and an
"unresolved" verdict in the report is not a miss.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]


def rule_for_guideline(rulebook: dict, guideline: int) -> str | None:
    """The confirmed visual rule whose description starts with '<guideline>.'; None if the rulebook has none."""
    for rule in rulebook.get("rules", []):
        if rule.get("check_name") == "annotation_guideline" and re.match(rf"\s*{guideline}\.\s", rule.get("description", "")):
            return rule["rule_id"]
    return None


def build(answer_key: dict, rulebook: dict, case: str) -> tuple[dict, str, str]:
    """Return (expectations, sample_stem, annotation_format) for one case, or raise ValueError with the reason."""
    entries = {entry["case_id"]: entry for entry in answer_key["entries"]}
    base, altered = entries.get(f"{case}-baseline"), entries.get(f"{case}-altered")
    if base is None or altered is None or "ready" not in (base["status"], altered["status"]):
        raise ValueError(f"{case} has no implemented baseline/altered pair in the answer key.")
    rule = rule_for_guideline(rulebook, base["primary_guideline"])
    if rule is None:
        raise ValueError(f"The rulebook has no confirmed visual rule for guideline {base['primary_guideline']}.")
    if base["packaged_json_pointer"] != altered["packaged_json_pointer"]:
        raise ValueError("The pair's target annotation differs between baseline and altered.")
    key = altered["packaged_json_pointer"]
    stem = Path(altered["image_file"]).stem
    return ({"baseline": {rule: {key: base["expected_target_result"]}},
             "altered": {rule: {key: altered["expected_target_result"]}}}, stem, altered["annotation_format"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", required=True, help="C01 .. C05 or L01 .. L03")
    parser.add_argument("--rulebook", required=True, type=Path)
    parser.add_argument("--answer-key", type=Path, default=PACK / "answer_key.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"Refusing to overwrite {args.out}", file=sys.stderr)
        return 2
    document = json.loads(args.rulebook.read_text(encoding="utf-8"))
    if isinstance(document, dict) and "items" in document:
        document = document["items"][0]
    try:
        expectations, stem, annotation_format = build(json.loads(args.answer_key.read_text(encoding="utf-8")), document, args.case)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    rule = next(iter(expectations["altered"]))
    print(f"{args.case}: format={annotation_format} sample-stem={stem} rule={rule} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
