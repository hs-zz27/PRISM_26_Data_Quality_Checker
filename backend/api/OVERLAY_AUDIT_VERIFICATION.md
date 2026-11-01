# Original-plus-overlay audit verification

Date: 1 November 2026. Local CPU validation with the installed
`qwen3-vl:4b-instruct` model and a 16,384-token context.

## Implemented behavior

New audits pin `annotation-guideline-v3` and
`original-and-annotation-overlay-v1`. Each target and each training example has
separate, labelled original/overlay image messages. Original bytes remain intact;
the shared correction renderer draws supplied boxes in yellow and polygons in
cyan, with display object IDs. Derived boxes are not presented as supplied boxes.
The model receives the complete source geometry and native annotation attributes
using shared field names. Missing attributes stay distinct from explicit nulls.

The system instruction explains the drawings and asks the model to compare them
with the original scene, coordinates and confirmed rule. The target receives no
expected outcome. Pixel masks are explicitly outside this drawing aid. The view
does not change a supplied annotation, review decision or feedback policy.

Existing v1/v2 executions retain their historical views and bounds. Non-truncating
requests, source verification, request/image/context/response bounds, model identity
checks and pending human review are preserved. All image messages and captions
are included in preflight budgeting. A request that cannot fit remains unavailable.

## Automated and rendering checks

- Backend suite: **2,662 passed, 516 skipped**, with 7 pre-existing warnings. The
  skipped tests are not counted as verified behavior. Full output is retained in
  `.cache/validation/overlay-audit-20261101/backend-tests.log`.
- Frontend: **10 review-workspace tests passed**; TypeScript and generated API
  freshness checks passed. Backend and frontend container builds succeeded.
- Real-source preflight: the two LabelMe L01 inputs and the two COCO C05 inputs
  fit the pinned window with both images and a short feedback comment. Their text
  budgets were 5,608 and 11,705 bytes respectively. All native fields and vertices
  reconstructed exactly from the compact source table.
- Both original-image attachments retain the manifest's image hash. Runtime
  overlay PNG hashes match the prepared attachments. The Mac-generated previews
  and Docker-generated overlays are pixel-identical; their compressed PNG bytes
  differ, so byte identity between platforms is not claimed.
- Tests cover both coordinate origins, supplied versus derived geometry, source
  immutability, example/target association, held-out truth exclusion, feedback
  scope, request/overlay limits and mismatched execution/view versions.

## Live reviewed L01 pair

The guideline-10 validation book retained the person definition, visible-only
occlusion rule and uncertainty condition. It excluded the unapproved 3-pixel
tolerance. Both audits used the same original photo and confirmed rulebook version.
The altered input contains only the documented 76.8-pixel horizontal shift of
`/shapes/0`. Expected answers were kept outside the requests.

- Baseline: **pass**. Audit `audit_7e9f27e741e74c0a88083364d03083cc`.
- Shifted polygon: **fail**, with a reason identifying the man's outline as
  displaced over background. Audit `audit_810a5c5ccd5f4748bb253f53de7dd5d8`.

Both outcomes match Harkamal Singh's recorded human review for the target under
guideline 10, and both responses passed the model-identity and completion checks.
The results and findings remain pending human decisions; no correction was applied.

The altered response additionally alleged an error in the woman's unchanged
outline. The human review found no significant misalignment there. This extra
claim is unsupported by that review and must not be counted as a verified finding.
Two matching overall outcomes do not establish general accuracy or reliable
object-specific explanations. No live COCO model-accuracy result is claimed by
the preflight checks, and no full-dataset benchmark was run.

## CPU run conditions and evidence

Only the two small L01 samples were sent for live inference. The test session's
Ollama server was launched with `LLAMA_ARG_CACHE_RAM=0`, using the installed
server's supported cache setting to avoid retaining extra prompt snapshots after
the earlier memory-pressure problem. This is a session setting, not a model-weight
or persistent system configuration change. CPU inference remains slow; see the
saved per-response durations rather than treating this pair as a throughput benchmark.

Resource IDs, API responses, prepared-view hashes, runtime previews and results are
retained under `.cache/validation/overlay-audit-20261101/`.
