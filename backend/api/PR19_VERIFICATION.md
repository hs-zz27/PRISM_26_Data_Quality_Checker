# PR #19 merge verification

## Local resolution

Main was merged into `fix/rule-applicability`. The main branch's manual polygon editor,
revision rechecks and visual findings are retained. The incompatible response formats
are separate protocols: existing v4 uses `VisualAssessment.observations`, and the PR's
scoped assessments use v5. New audits default to v5; existing audits retain their pins.
The comparison tool defaults to v4 versus v5 and still accepts v3.

Generated OpenAPI and TypeScript were regenerated from source. Scoped results and
no-model-call eligibility explanations are displayed by the existing visual evidence
component; the v4 observation display remains available. The correction editor is unchanged.

Two aggregation cases were fixed while reconciling main's semantics: an applicable
unavailable target prevents an overall pass, and duplicate target assessments cause
uncertainty rather than trusting the first answer. A scope larger than the 32-assessment
response bound returns unavailable before inference, without dropping targets.

Tests were authored/updated but **not run by the conflict resolver**. No live inference,
database tests, browser checks, frontend build or typecheck were run during resolution.
Passing tests on either pre-merge branch do not verify this combined code.

The owner's first merged-code run reported 389 passed, four failed and one skipped.
The four failures were two cross-environment v3 request hashes and two comparison
mechanics checks whose temporary source images were unavailable. The comparison now
resolves its temporary root before importing and rejects unavailable source assets
explicitly. v3 compatibility now compares the complete request to the checksum-locked
pre-PR builder on the same machine; original golden hashes were not changed. New tests
cover a symlinked temporary root and missing source images. The owner subsequently
reported that the requested automated checks passed; no final aggregate count is
recorded here, and the resolver did not independently execute those checks.

The owner's database run reported 56 passed and two migration-test failures. Those
tests assumed migration 13 was latest and seeded a version-12 schema through a
current writer that needs version-14 columns. They now assert the discovered
migration history (including unchanged application timestamps), seed genuine old
columns directly, and verify upgrades through feedback migration 13 and then the
current schema preserve old rows and reviewer attribution. No production migration
or database runtime code was changed. The owner subsequently reported the
database-backed checks passed after supplying the test database configuration.

## Owner-run automated checks

Run from the repository root. The merge resolution should already be staged so the
contract regeneration check compares against that prepared snapshot.

```sh
mkdir -p .cache/validation/pr19
set -o pipefail

.venv/bin/python -B -m pytest \
  backend/tests/test_rule_applicability.py \
  backend/tests/test_applicability_sources.py \
  backend/tests/test_applicability_compat.py \
  backend/tests/test_scoped_visual_audit.py \
  backend/tests/test_visual_assessment.py \
  backend/tests/test_visual_audit_prompts.py \
  backend/tests/test_compact_vision_prompts.py \
  backend/tests/test_guideline_context.py \
  backend/tests/test_feedback_inference.py \
  backend/tests/test_feedback_models.py \
  backend/tests/test_image_review_feedback.py \
  backend/tests/test_feedback_api_contract.py \
  backend/tests/test_api_contract.py \
  backend/tests/test_polygon_revision_recheck.py \
  evaluation/annotation_error_pack/tests/test_compare_visual_audit.py \
  -q -rs -p no:cacheprovider | tee .cache/validation/pr19/backend-tests.txt

npm --prefix frontend run typecheck
npm --prefix frontend run test | tee .cache/validation/pr19/frontend-tests.txt
npm --prefix frontend run build
```

Database-backed checks use the local `.env` settings without displaying credentials.
PostgreSQL must be running. The fixtures create and remove isolated test schemas,
preserving application tables. Inspect any skips; database tests must execute.

```sh
PRISM_TEST_DATABASE_URL="$(.venv/bin/python -c 'from backend.app.core.config import load_database_settings; print(load_database_settings().conninfo.get_secret_value())')" \
  .venv/bin/python -B -m pytest \
  backend/tests/test_rechecks.py \
  backend/tests/test_recheck_workspace.py \
  backend/tests/test_audit_submission.py \
  backend/tests/test_feedback_repository.py \
  backend/tests/test_guideline_context.py \
  -q -rs -p no:cacheprovider | tee .cache/validation/pr19/database-tests.txt
```

The recheck tests now include v3/v4/v5 parent audits whose stored execution must survive
a change to the new-audit default. They exercise the real repository and PostgreSQL;
they cancel the parent before inference and do not call a model.

## Contract regeneration must leave no changes

```sh
.venv/bin/python -B -m backend.api.build_contract
npm --prefix frontend run gen:api
git diff --exit-code -- \
  backend/api/openapi.json backend/api/implemented-openapi.json backend/api/examples.json \
  backend/api/ENDPOINTS.md backend/api/PLANNED_ENDPOINTS.md \
  frontend/src/api/generated/openapi.d.ts frontend/src/api/generated/implemented-openapi.d.ts
```

The final command must exit zero and print no diff. This proves regeneration agrees
with the staged/committed artifacts; it does not mean the PR makes no API changes.
Attach the output and exit status to the PR.

## Real-model comparison

The first owner real-model attempt returned immediate `inference_unavailable` for
all calls that required Ollama. It establishes no model accuracy. Its two v5 box-rule
eligibility skips are expected. The report counter was corrected so service failures
are not counted as eligibility skips, and unverified empty scope results are `null`
rather than an apparent clean list. A regression test was added, and the owner
reported that it passed.
Check the native Ollama connection and configured base URL before retrying. Preserve
the original report and use a new output filename for the retry.

After automated checks pass, export the exact confirmed rulebook intended for the
comparison. Keep its rule IDs, definitions and conditions unchanged between versions.
The source image and archive pairs must already be present; the real report must not
be run with `--mock`. No test answer key enters the inference request.

Replace `RULEBOOK_ID`, `VERSION` and `POLYGON_RULE_ID` below with actual values.

```sh
curl -fsS "http://localhost:8000/api/v1/rulebooks/RULEBOOK_ID?version=VERSION" \
  -o .cache/validation/pr19/rulebook.json

.venv/bin/python -B evaluation/annotation_error_pack/tools/compare_visual_audit.py \
  --baseline-archive evaluation/annotation_error_pack/archives/labelme_baseline_3.zip \
  --altered-archive evaluation/annotation_error_pack/archives/labelme_errors_3.zip \
  --sample-stem 258421 --rulebook .cache/validation/pr19/rulebook.json \
  --versions v4,v5 --polygon-fit-rule POLYGON_RULE_ID --shifted-key /shapes/0 \
  --out .cache/validation/pr19/comparison_report.json
```

The script uses the local Ollama model and digest configured for feedback execution.
Calls can take minutes on CPU. For a first run, use a different output filename and
`--rules POLYGON_RULE_ID`; the report records which rules were omitted. The script
refuses to overwrite an existing report.

Post the test outputs, unchanged-contract result, database results and the real
`comparison_report.json` on PR #19. Review false alarms on the baseline, detection of
the shifted polygon, allegations about the unchanged person, unavailable outcomes
and latency. One image does not establish general accuracy. Missing-body-part and
feedback-on-unchanged-annotation checks remain separate acceptance checks.

### Owner-run results on 2 November 2026

The owner ran the confirmed rulebook
`rulebook_7385861ed52d4aeda8e8f7147b4e7b40@3` against LabelMe sample `258421`,
selecting RULE-2 (supplied box/head), RULE-5 (partial occlusion) and RULE-7
(polygon boundary). Only `/shapes/0`, the man's polygon, is shifted in the altered
archive; `/shapes/1`, the woman's polygon, is unchanged. These are two runs of one
image pair, not a general accuracy benchmark.

- The 4B comparison is saved locally at
  `.cache/validation/pr19/comparison_report_retry1.json`. v4 produced five
  unverified executions and incorrectly failed the baseline polygon-fit rule.
  v5 correctly skipped the two box checks, passed the baseline and detected the
  shifted polygon. For the altered polygon-fit check, the model omitted
  `/shapes/1`; the backend retained it as `unsure` with `no_assessment`. The
  report's `flagged` lists combine `fail` and `unsure`, so this omission must not
  be described as a model allegation that the woman's polygon was incorrect.
  RULE-5 nevertheless described the shifted polygon as correctly outlining the
  person, which is an unsupported explanation. v5 took 866.5 seconds.
- The 8B v5 comparison is saved locally at
  `.cache/validation/pr19/comparison_report_8b.json`. It produced assessments for
  both objects but incorrectly passed the shifted polygon. Its RULE-5 reasons
  reversed the people associated with the annotation keys and said the condition
  was not triggered while the structured field said `applies`. The run took
  1006.0 seconds. The model and digest were selected through command-local
  environment overrides; the current report does not persist that execution pin.

`model_verified` establishes the client's execution and response checks; it does
not establish visual correctness. These results do not satisfy visual acceptance.
Missing assessments, incorrect explanations and the 8B false negative remain open.
Raw model reports and source images remain local evaluation artifacts, not tracked
repository content.

The current image-attachment pipeline was reconstructed locally without inference
under `.cache/validation/pr19/input-inspection/`. The altered overlay visibly
contains the shifted polygon; both outlines use cyan with small numeric labels.
This inspection does not expose the model server's internal image preprocessing
or establish why it missed the error. Target-specific visual presentation is a
proposed next experiment and is **not implemented by this merge**. Separate report
counts for failures, uncertainty and omitted assessments also remain to be added.
**Update:** target-specific presentation (opt-in prompt v6) and the separate report categories are implemented in the
follow-up below; their real-model results are not yet recorded.

## Remaining frontend work

This resolution adds display compatibility for scoped findings. The frontend teammate
still owns rule-applicability editing/ambiguity presentation and optional selection
of `object_keys` for feedback. Existing JSON rulebook editing preserves applicability;
the new optional API fields do not require the manual correction editor to change.
The v5 schema does not supply the v4 inspection-region field; no location is invented
for a scoped result. Both protocols retain the source annotation overlay.

## Follow-up: focused per-target evidence (v6) and corrected comparison report

Design: `RULE_APPLICABILITY.md` ("Prompt v6"). v4 and v5 keep their schemas, pins and behaviour; new audits still default
to v5; v6 is opt-in (`FEEDBACK_PROMPT_VERSION=annotation-guideline-v6`) until its real-model results are reviewed.

### Checks run for this follow-up (Linux sandbox; mocked model; nothing here is real-model evidence)

- New backend tests (`test_target_focus`, `test_focused_protocol`, `test_protocol_compat_focus`, additions to
  `test_rule_applicability`) and pack tests (`test_comparison_report`, additions to `test_compare_visual_audit`) pass.
  Fourteen deliberate code breakages (a failed target counted as a pass, unclear compliance as a pass, an answer for another
  annotation accepted, targets silently dropped, eligibility no longer gating the call, the v1 derivation altered, a missing
  assessment reported as an explicit failure, crop margins removed, the highlight following position instead of key,
  edge sides never reported, the overlay crop using another window, the shared overlay edited, ...) were each caught; two
  weak tests found this way were fixed.
- All 133 backend test files, run one file at a time with the database enabled: 3,232 passed, 0 skipped, 1 failed, 9 errors.
  The 9 errors are `test_detection_*` and `test_person_detector` (`ModuleNotFoundError: torch`, not installed in the
  sandbox; identical on the untouched branch). The failure is `test_dataset_migration.py::
  test_upgrade_keeps_existing_manifests_audits_findings_and_reviews_exact` (an old-schema seeding test writes a column the
  old schema lacks); it fails identically on the untouched branch, is not in the required list, and is unchanged here.
- Database-backed checks (the five files listed above, isolated per-test schemas, PostgreSQL 16.15; the project targets 18):
  110 passed, 0 skipped on this branch (109 on the untouched branch; the extra case is the v6 stored-pin recheck).
- Contracts: `build_contract` then `npm run gen:api` changed only added enum values (`ExecutionPin.prompt_version` and
  `preprocessing`, `RuleApplicability.source`, `ScopeUnavailable.code`); no paths, schemas or fields were added or removed.
  Running both again produced no further diff in any of the seven generated files.
- Frontend (generated types only; no frontend source changed): typecheck, 135 unit tests (23 files), lint and the production
  build pass.
- Evaluation pack tests with the L01 source files present: 81 passed, 1 failed on this branch; 63 passed, 1 failed on the
  untouched branch. The failure is a line-ending assertion in `test_build_pack` (CRLF checkout) and is the same on both.
- Skips: none in the runs above. Without `PRISM_TEST_DATABASE_URL`, database tests skip and are not passes.

Environment note: `backend/tests/golden/feedback_prompt_before_pr19.py` is checksum-locked on raw bytes; a CRLF checkout
fails `test_applicability_compat` although the content is identical. Run it on an LF checkout (Linux or macOS).

### Not established

No real-model result exists for v6, `derived-v2` or the new report. The cost of per-target requests is an estimate
(about 2,800 image tokens and one request per eligible target) until measured. PostgreSQL 18 and native Windows were not run.

### Owner: run on the Mac, after `git fetch origin && git checkout fix/rule-applicability && git pull --ff-only`

Automated checks (record the real output; do not paste a summary):

```sh
export PRISM_TEST_DATABASE_URL="$(.venv/bin/python -c 'from backend.app.core.config import load_database_settings; print(load_database_settings().conninfo.get_secret_value())')"
.venv/bin/python -B -m pytest backend/tests/test_target_focus.py backend/tests/test_focused_protocol.py \
  backend/tests/test_protocol_compat_focus.py backend/tests/test_applicability_compat.py backend/tests/test_rule_applicability.py \
  backend/tests/test_scoped_visual_audit.py backend/tests/test_visual_assessment.py backend/tests/test_rechecks.py \
  backend/tests/test_recheck_workspace.py backend/tests/test_audit_submission.py backend/tests/test_feedback_repository.py \
  backend/tests/test_guideline_context.py evaluation/annotation_error_pack/tests -q -rs -p no:cacheprovider | tee .cache/validation/pr19/followup-tests.txt
.venv/bin/python -B -m backend.api.build_contract && npm --prefix frontend run gen:api && git diff --exit-code -- backend/api frontend/src/api/generated
```

Focused real-model experiment. Same model, digest, rulebook, annotations and settings for both versions; every run gets a NEW
filename; keep failed runs. Start with RULE-7 only (about four v6 requests per version pair; the earlier v5 requests took
140 to 320 seconds each, so allow time):

```sh
.venv/bin/python -B evaluation/annotation_error_pack/tools/compare_visual_audit.py \
  --baseline-archive evaluation/annotation_error_pack/archives/labelme_baseline_3.zip \
  --altered-archive  evaluation/annotation_error_pack/archives/labelme_errors_3.zip \
  --sample-stem 258421 --rulebook .cache/validation/pr19/rulebook.json --versions v5,v6 --rules RULE-7 \
  --polygon-fit-rule RULE-7 --shifted-key /shapes/0 --diagnostics \
  --out .cache/validation/pr19/comparison_report_rule7_v5v6_try1.json
```

Expected for RULE-7 (kept outside every request): baseline `/shapes/0` pass and `/shapes/1` pass; altered `/shapes/0` fail
and `/shapes/1` pass; no swapped identities; no omitted assessment presented as success. Then RULE-5 separately
(`--rules RULE-5`, new filename), recording whether its condition applied and whether the explanation is supported.

Additional human-reviewed pairs (only after the experiment above works; expectations come from the answer key and are applied
after the fact; read `human_review.md`, because some baseline checks there are Uncertain and "unresolved" is not a miss):

```sh
.venv/bin/python -B evaluation/annotation_error_pack/tools/expectations_from_answer_key.py --case C01 \
  --rulebook .cache/validation/pr19/rulebook.json --out .cache/validation/pr19/expected_C01.json
.venv/bin/python -B evaluation/annotation_error_pack/tools/compare_visual_audit.py --format coco \
  --baseline-archive evaluation/annotation_error_pack/archives/coco_baseline_5.zip \
  --altered-archive  evaluation/annotation_error_pack/archives/coco_errors_5.zip \
  --sample-stem 000000199055 --rulebook .cache/validation/pr19/rulebook.json --versions v5,v6 --rules RULE-3 \
  --expectations .cache/validation/pr19/expected_C01.json --out .cache/validation/pr19/comparison_report_C01_try1.json
```

Cases: C01 (feet cut off, RULE-3), C02 (head cut off, RULE-2), C05 (COCO polygon shifted, RULE-7), L02 (`--sample-stem 2011_000006`,
LabelMe, RULE-7). C03 is refused by the tool (its guideline is the unsupported tolerance). Report misses and false alarms as
they are.

Record, for each report: the counts per category, per-call timings and total duration, any unsupported explanation, and any
swapped identity. If v6 does not improve a result, record that and the next evidence-based step; do not report success on
valid JSON or on a single flagged image.

### Owner follow-up on 3 November: request budget and mixed geometry

The initial v6 RULE-7 run rejected all four requests before inference: the prepared text was 9,790 bytes
against the conservative 9,728-byte allowance at 16,384 context and 2,048 output tokens. Redundant system
wording was compacted without increasing limits or dropping guideline/annotation evidence. The owner then
obtained all four expected RULE-7 outcomes on L01. A separate same-annotation feedback run also correctly
failed the shifted man and passed the unchanged woman. These are single-case results, not general accuracy.
RULE-5 had one 600-second execution failure; its separate retry completed, but the conditional explanations
remain generic and require review.

C01 (`comparison_C01_v6_20261103T143122.json`, local only) exposed a different problem: every reply named
`polygon` under the feet/bounding-box rule. The backend correctly downgraded these to `unsure` with
`geometry_not_in_requirement`. The renderer highlighted BOTH the supplied box and polygon in magenta.
The model also incorrectly applied the person-only rule to the skateboard. Neither target result counted
as a successful box assessment.

The local fix selects highlighted and cropped geometry from rule applicability, keeps other geometry as
context, restricts the response's geometry enum, and clarifies visible-object class as a prerequisite for
compliance. No label-based exclusion is introduced. Real C01 and L01 requests, including the prior feedback,
were prepared offline within the unchanged budget and C01 overlays were visually inspected. This is not
a real-model result for the fix. Earlier reports must be retained; prompts and rendered inputs changed.

Owner-run checks for this fix (not yet run by the assistant):

```sh
.venv/bin/python -B -m pytest backend/tests/test_focused_geometry.py \
  backend/tests/test_target_focus.py backend/tests/test_focused_protocol.py \
  backend/tests/test_focused_prompt_budget.py backend/tests/test_protocol_compat_focus.py \
  backend/tests/test_applicability_compat.py backend/tests/test_rule_applicability.py \
  -q -rs -p no:cacheprovider
```

After the checks pass, repeat C01 with a new report filename, then recheck L01 polygon fit and feedback
because the v6 instructions changed. A schema-conforming reply still does not establish visual correctness.

### C01 feedback follow-up and presentation

`comparison_C01_v6_geometryfix_20261103T151215.json` completed the altered box requests: the model
used box geometry and excluded the skateboard, but incorrectly passed the person box. Both baseline
requests failed near 600 seconds; sleep was reported during the run, but the saved error cannot prove
the cause. The completed altered-box pass remains a real miss.

`feedback_C01_rule3_v6_20261103T135832076419Z.json` rechecked the exact altered image and annotation
with a comment about the excluded shoes. The person correctly failed after 398.9 seconds. The skateboard
was marked `condition=not_present`, but its explanation repeated the feet complaint (389.3 seconds).
The backend excluded that target from the compliance decision. Input hashes matched and the annotation
manifest was unchanged. This is one feedback-assisted recovery, not proof of learning or robustness.

The local presentation fix labels absent conditions as **Not applicable** in v6 summaries and per-object
UI rows. Verbatim reasons remain in `scope.results` and expandable raw explanations; outcomes, correction
eligibility, API schemas, and earlier protocol aggregation remain unchanged. An execution failure still
displays as unavailable. Regression tests cover evidence preservation and this distinction; the assistant
has not run them.

The local diagnostic `verify_C01_baseline_feedback.py` prepares the correct baseline with and without the
same complaint. Both modes passed offline input/hash and request-budget preparation. No new inference has
been run. Run `--without-feedback` first, then the default mode; retain both timestamped reports, including
false alarms or execution failures. The expected person result is pass in both, with the skateboard's
condition absent. This checks feedback suggestibility on one case, not general model accuracy.

### Historical experiment: feedback false alarm and independent inspection

The baseline control (`baseline_C01_rule3_v6_without_feedback_20261103T141749930494Z.json`) passed the
person; the matching feedback run (`baseline_C01_rule3_v6_with_feedback_20261103T143620150055Z.json`)
incorrectly failed it. Both completed with identical image/annotation hashes, model settings, prompt code
and renderer. This is evidence of suggestibility on this case. The altered-box feedback recovery alone
is not evidence of reliable improvement.

An intermediate implementation removed the complaint from visual requests and added a text-only
comparison afterward. The saved `independent_feedback_C01_altered_20261103T164226185254Z.json` still
passed the shortened box. Its text comparison then rejected the reviewer's correct concern based on
that mistaken inspection. The split avoided direct priming but could not recover a missed visual error.
The local `verify_C01_independent_feedback.py` and those reports describe this retired experiment;
do not use that script to verify the current workflow.

### Return to the core workflow

The current local v6 path restores **guideline → audit → reviewer comment → fresh visual recheck**.
Explicit `object_keys` limit the comment to those targets, while other eligible objects retain their
ordinary visual requests. The same image and annotation remain unchanged; human edits use the existing
new-revision workflow. The model may reject a mistaken complaint or report uncertainty. Reviewer
decisions stay pending, and no feedback policy is activated automatically.

The extra text-only model call and its active helper were removed. The optional schema and frontend
reader remain to display saved experimental results as historical evidence. Feet localization stays
outside production routing; its first real response contained out-of-image coordinates and was rejected.
Neither experiment is a prerequisite for completing the prototype's review workflow.

`test_visual_feedback_recheck.py` covers fresh visual requests, exact preservation of image and
annotation inputs, per-object comment scoping, invalid object references, invalid replies, no feedback
inheritance and pending human decisions. Existing revision/recheck tests cover human-edited annotations.
These mechanics do not prove the model handles feedback accurately. The C01 missed error and false
alarm remain recorded limitations, not solved results.

Run the focused regression checks locally:

```sh
.venv/bin/python -B -m pytest \
  backend/tests/test_visual_feedback_recheck.py \
  backend/tests/test_focused_protocol.py \
  backend/tests/test_focused_geometry.py \
  backend/tests/test_focused_prompt_budget.py \
  backend/tests/test_protocol_compat_focus.py \
  backend/tests/test_image_review_feedback.py \
  backend/tests/test_polygon_revision_recheck.py \
  -q -rs -p no:cacheprovider

npm --prefix frontend run test -- \
  src/features/audits/FeedbackComparison.test.tsx \
  src/features/audits/VisualObservations.test.tsx
npm --prefix frontend run typecheck
```

The owner reports that all listed return-to-core regression checks passed. No new real-model inference
was started for this change; the successful tests verify workflow mechanics, not visual accuracy.
Offline payload preparation succeeded for baseline and altered L01/RULE-7 and C01/RULE-3, with both
image-level and object-scoped comments. Requests used 8,980–9,448 UTF-8 text bytes against the unchanged
9,728-byte preparation allowance (16K context, 2,048 output tokens, four image reservations). Scoped
comments were absent from the other object's request. This checks preparation, not model execution.
For the next model check, retain both correct and erroneous examples and the unchanged object control.
Use the same targeted comment on both versions so the baseline also tests rejection of a mistaken
complaint. Save all outcomes, including misses, false alarms and unavailable results. These case checks
are not held-out policy evaluation and do not authorize learning activation.
