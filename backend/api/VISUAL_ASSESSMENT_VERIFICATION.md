# Visual audit improvements: step 1

## Status

Implemented locally. Tests have been authored but **not run**, at the owner's
request. API JSON and TypeScript declarations were regenerated; this is code
generation, not validation. No live audit, model inference, container rebuild or
database migration was run for this change. No claim of improved model accuracy
has been established.

## Why this change

The observed demo included a misplaced box that received PASS, then FAIL after
feedback. Another audit applied box requirements to a polygon-only sample. A
single free-form whole-image verdict could not establish which objects and
annotation types had actually been assessed.

New `annotation-guideline-v4` executions require exactly one observation for every
supplied object key. Each records the geometry assessed, outcome, short observable
reason and optional inspection region. A failure requires a region, in zero-based
original image coordinates, even for a Penn-Fudan annotation. This region is not
replacement geometry and can extend outside a faulty annotation while remaining
within the image. It still needs human inspection.

The backend validates exact object coverage, supplied geometry, finite coordinates,
image bounds, source identity and model identity, then computes the sample result.
Failure takes precedence over uncertainty; without a failure, unsure or unavailable
objects prevent PASS. Objects outside the guideline scope remain visible as
not_applicable. If none is applicable, the result is unavailable. Reviews stay pending.

The inventory does not filter by label: an actual person incorrectly labelled car
must still be considered by the model. Definitions, conditions and uncertainty
instructions remain attached to the confirmed rule. No keyword heuristic invents
rule scope or a tolerance. This means semantic applicability is still model-dependent.

## Limits and compatibility

- This does not prove that a structurally valid visual observation is correct.
  The model may still miss a subtle error, misinterpret a condition, incorrectly
  exclude an object or cite an inaccurate location. A larger schema may also affect
  CPU latency. Both accuracy and latency need real-model measurement.
- Masks are not shown by this visual protocol; binary visual claims based on a
  mask are rejected. Existing deterministic mask checks remain separate.
- This step evaluates existing annotations, not detection of entirely unannotated
  objects. No-object input is unavailable. More than 64 objects or a context/output
  overflow remains unavailable; nothing is truncated or silently dropped.
- One failure produces a flagged aggregate even when other objects are unclear.
  Reviewers must inspect individual observations; a flag is not a claim that every
  object was conclusively checked.
- Existing v1/v2/v3 prompts, policies, fingerprints and linked rechecks remain
  unchanged. Create a **new audit with feedback_policy_id null** after rebuilding
  the backend to obtain v4. Rechecking an old audit retains that audit's old pin.
- No automatic acceptance, annotation edits, feedback activation or weight updates.
  Visual polygon editing and measured reuse of corrected examples are later steps.

## Owner-run automated checks

From the repository root:

```sh
.venv/bin/python -B -m pytest \
  backend/tests/test_visual_assessment.py \
  backend/tests/test_visual_audit_prompts.py \
  backend/tests/test_feedback_inference.py \
  backend/tests/test_feedback_models.py \
  backend/tests/test_guideline_context.py \
  backend/tests/test_image_review_feedback.py \
  backend/tests/test_feedback_api_contract.py \
  backend/tests/test_api_contract.py \
  -q -p no:cacheprovider
```

Frontend (from the repository root):

```sh
npm --prefix frontend run test -- \
  src/features/audits/VisualObservations.test.tsx \
  src/features/audits/AuditReviewWorkspace.test.tsx
npm --prefix frontend run typecheck
npm --prefix frontend run check:api
```

The new tests exercise real readers and immutable stored source files with mocked
Ollama responses. They cover source fidelity, polygon-only inputs, incorrect labels,
omitted/unknown/duplicate objects, unsupported geometry, coordinates, aggregate
uncertainty, model identity drift, feedback rechecks and pending reviewer decisions.
They do not test whether Qwen actually recognizes a shifted box or missing foot.

## Real-model acceptance check after automated tests

Use the local baseline/error pairs in `evaluation/annotation_error_pack/archives`
and the already prepared mentor box case in `.cache/mentor-box-live` if present.
Keep any human answer key outside the inference request. For each matching pair:

1. Start a fresh audit using the same confirmed guideline and no feedback policy.
   Confirm the result records prompt_version `annotation-guideline-v4`.
2. Inspect the baseline and erroneous annotation without feedback first. Record
   wrong passes, wrong failures, unsure/unavailable outcomes, missing objects and
   elapsed time. Compare all observations with the human-reviewed expected case.
3. For an actual missed error, submit a factual comment on the same unchanged
   revision with `propose_correction: false`. Verify image and annotation hashes
   remain identical and the result reflects visible evidence rather than copying
   the comment. Include a deliberately mistaken comment on a correct baseline to
   test whether the model resists incorrect feedback.
4. Check polygon-only and mixed COCO examples separately. A claimed box assessment
   on a LabelMe polygon must never become PASS/FAIL. A derived bound is not a box
   supplied by the annotator.
5. Record outcomes and review them before claiming improved detection. The subtle
   boundary pack is not validated human truth until its cases are reviewed.
