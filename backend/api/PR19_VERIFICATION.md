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

## Remaining frontend work

This resolution adds display compatibility for scoped findings. The frontend teammate
still owns rule-applicability editing/ambiguity presentation and optional selection
of `object_keys` for feedback. Existing JSON rulebook editing preserves applicability;
the new optional API fields do not require the manual correction editor to change.
The v5 schema does not supply the v4 inspection-region field; no location is invented
for a scoped result. Both protocols retain the source annotation overlay.
