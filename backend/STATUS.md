# Backend progress and team handoffs

> Current feedback source proposal: ten feedback operations plus real bounded
> `annotation_guideline` inference are supplied. Proposed inventory: 46 implemented,
> 4 planned, 50 total. This handoff did not run tests, migrations, workers, inference
> or API contract generation. Regenerate all five artifacts together before using
> them for this feature; earlier generation/test statements below refer to the
> captured pre-feedback work. See [api/FEEDBACK.md](api/FEEDBACK.md).

Last updated: 21 October 2026 (Asia/Kolkata).
Correction/recheck handoff baseline: `be424f833820e8e527dce8b6536ea1200279c935`.
Dataset Feature 4 Phases 1–4 are applied in this workspace. All five API artifacts
are regenerated. Syntax and schema generation are verified; migrations,
HTTP scenarios and import execution were not run during application.
Audit creation/listing and an explicit audit worker are now implemented. Their
regression coverage is present. The user reports the pre-correction baseline
passed. Reported correction/recheck failures were traced to stale generated
contracts, missed route inventories and serialization before model revalidation.
Source fixes and contract regeneration are complete; tests have not been rerun.

This is the working tracker for implemented features, teammate assignments and
the next delivery steps. Update it when work is delivered, reviewed, integrated
or tested. Plans and prepared prompts do not count as completed implementation.

## What is built

The following code exists in this checkout. "Implemented" does not mean the
latest complete test suite or real-model evaluation has passed.

- **Dataset and measurement foundation:** Penn-Fudan loading, annotation and
  metadata parsing, label checks, box validity/overlap, mask IDs/dimensions,
  mask-to-box measurements and component measurements.
- **Dataset import and browsing:** streamed ZIP acceptance, explicit bounded
  import worker, immutable initial revisions, catalogue-owned heads, partial and
  damaged sample browsing, import-aware job cancellation and five HTTP operations.
- **Guideline extraction service:** configurable local Ollama client,
  registry-aware check/parameter instructions, citation-linked draft rules and
  persistence. The checkpoint adds durable extraction HTTP submission and an explicit supervised worker; runtime verification remains pending.
- **Rulebook workflow:** manual draft creation, saved-book reads/listing, version
  history, full draft replacement, confirmation, rejection, new draft revisions
  and attributed review history.
- **Audit engine:** confirmed-rule evaluation, evidence-linked findings,
  PostgreSQL batch processing, durable progress/results/failures and job state.
  Existing leases, recovery, cancellation, timeouts and retry safeguards remain.
- **Audit submission and browsing:** ready catalogue selection, exact confirmed
  book version, immutable sample pins, queued creation, public pagination and an
  explicit audit-only worker. No request-time computation or snapshot recapture.
- **Immutable assets:** snapshot capture, manifests, audit-to-revision pins,
  verified asset reads, thumbnails and mask previews. Application-owned dataset
  revision selection is supplied by the dataset checkpoint; correction publication now creates new immutable heads after this handoff is applied.
- **Human review:** audit metadata and sample queue/detail reads, finding
  decisions/history, and separate sample decisions/history. Apparently correct,
  flagged, uncertain and unavailable evidence remain reviewable.
- **Reviewed dataset exports:** frozen audit revisions and human decisions,
  durable managed jobs, a bounded explicit archive worker, metadata/list reads,
  a selection manifest and ZIP downloads. Source and API contracts are integrated;
  fresh tests, migration 011 application and runtime verification remain pending.
- **Frontend handoff:** generated combined and implemented-only OpenAPI,
  request/response examples, endpoint index and rulebook workflow documentation.
  The complete planned endpoint reference and
  screen-by-screen workflow guide describe the planning history; no operation is
  currently planned. Future job fixtures cover all six states;
  these contracts do not add runtime routes.

The current integration is **reviewed dataset exports**: terminal audit pins and
human decisions feed a frozen archive selection. Dataset import, extraction,
audits and exports retain explicit workers. Correction/recheck source is supplied by the current handoff, using the existing
audit worker and durable child lineage. Evaluated feedback source is supplied; runtime integration and actual quality validation remain unverified.

## Current HTTP surface

The applied source and regenerated artifacts describe **50 implemented and 0
planned operations**, 50 total: rulebooks, audits (including the audit-wide finding
list), datasets, assets, jobs, reviews, exports, feedback and system reads.
Route-inventory regression assertions pin this exact set. These counts describe
source HTTP availability only. They are not test results, runtime validation, model
accuracy or a statement that the project is complete.

Evidence is separate from availability. The developer's last full-suite run, before
the finding list, reported 2663 passed and 7 warnings. The finding-list repository,
HTTP and contract tests are authored and not yet run. CPU detector validation is
limited to the observations in CPU_DETECTION.md, and no detection accuracy
evaluation exists.

- Rulebooks: create, collection read, detail read, version history, review history,
  replace draft, confirm, reject and start a draft revision, plus extraction submission — ten operations.
- Audits: creation, listing, metadata, sample queue, sample detail, the audit-wide finding list and linked rechecks — seven operations.
- Datasets: import submission, four catalogue reads and three correction/history operations — eight operations.
- Assets: revision-pinned sample asset delivery — one operation.
- Jobs: status polling and cancellation — two operations.
- Reviews: finding write/history and sample write/history — four operations.
- Exports: creation, listing, detail, manifest and ZIP download — five operations.
- Feedback: examples, candidate policies, explicit truth, comparisons and activation — ten operations.
- System: health, capabilities and local readiness — three operations.

Use the endpoint index for the exact inventory and
implemented-openapi.json for live client
generation. The combined contract marks every operation implemented; a
future planned design would be marked in x-implementation-status and could not be called yet.

## What has been assigned or prepared for teammates

### CPU person-detection teammate

**Assignment:** standalone CPU person detector using explicitly supplied/cached
weights, with typed detections in original-image coordinates and clear failure
behavior. The user reported that this teammate is building it.

**Captured integration status:** PR #1 CPU person detection is merged at the captured
baseline. Older placeholder progress text is stale. This dataset handoff does not
modify, rerun or independently validate detector/matching behavior.

**Follow-up prompt prepared:** standalone detection-to-annotation matching.
The full prompt was provided in this chat for the user to share. Whether it has
been forwarded, accepted or started is unconfirmed.

Matching scope:

- Synthetic detection/annotation inputs; no Penn-Fudan, Ollama or detector weights
  required for implementation and unit coverage.
- Explicit confidence/IoU thresholds and documented conversion between the
  one-based inclusive annotation boxes and continuous detector coordinates.
- Conservative one-to-one matching: isolated candidate pairs become matches;
  larger candidate components remain ambiguous for review.
- Separate matched, ambiguous, unmatched, confidence-filtered and invalid inputs;
  stable references preserve duplicate-record evidence.
- Typed finite JSON evidence, with no automatic error verdict or correction.
- Proposed owned files: `app/models/detection_matching.py`,
  `app/services/detection_matching.py`, `tests/test_detection_matching.py`.
- No rulebook/API, shared geometry, audit collector/evaluator, database or worker
  changes in this standalone task. Integration is a separate reviewed step.

The detector itself needs weights and suitable images for a real inference check.
Ollama is not needed. Synthetic matching tests do not establish real-data accuracy.

### Other teammates

No additional named backend assignments are confirmed in this conversation.
Frontend developers can use the current API handoff and mock the planned routes.
Do not record a suggested task as assigned without confirmation of its owner.

## Verification and open handoff notes

- The creation implementation was reviewed against `p.md` through `p4.md`.
  Static inspection found no blocking implementation defect; this is not a
  guarantee of runtime correctness.
- Current generated artifacts were checked for freshness and live schema
  alignment. Syntax and whitespace checks passed at the last review.
- New creation and export model/service, HTTP and PostgreSQL tests exist. The
  developer supplied a failing full-suite result; corrections include shared SQL,
  transport/schema integration, fixtures and inventory expectations. Tests have
  not been rerun after those corrections.
- The earlier included-router inventory failure was fixed in `15ea976` using
  FastAPI's `iter_route_contexts`, preserving duplicate-registration detection.
- The handoff text `p4.md` has a corrupted generator snippet near line 450. The
  actual `api/build_contract.py` is correct. Handoff notes saying regeneration
  is still pending are stale; generated artifacts are already current.
- CPU detector/matching delivery, integration and real-data quality remain
  unverified. No integration claims should be inferred from the prepared prompt.

## What to build next

**Immediate verification:** obtain a fresh full-suite result after these fixes,
including opt-in isolated PostgreSQL tests for import, audits and exports.
Do not run tests automatically; the user controls execution.

**Earlier checkpoint: plain-text rulebook extraction over HTTP.**
Source implementation now covers strict submission, a private durable ledger,
one-attempt supervised extraction, atomic version-1 draft/job publication and
existing polling/cancellation/review APIs. The worker is explicit and independent
of audit/detector work. See the extraction guide.
Its source is integrated and the API artifacts are current. Runtime verification
remains separate; do not treat syntax or schema generation as passing test evidence.

After that, the proposed delivery order is:

1. Apply current migrations, including 010, 011 and 012, explicitly and obtain developer-run dataset/API/worker and
   PostgreSQL regression results. Dataset sources and contract regeneration are
   complete; no runtime results are claimed.
2. Obtain developer-run audit submission/listing/worker and PostgreSQL regression
   results. The two APIs and worker are implemented; runtime results are not claimed.
3. Validate the applied correction/recheck source, additive migration 012 and the
   authored collector/concurrency/cancellation regressions. Contracts are
   regenerated; preserve historical evidence and decisions during runtime checks.
4. Verify the implemented export worker, download delivery and frozen manifest
   through the corrected tests and an operator-controlled end-to-end scenario.
5. Apply and validate migration 013 and the evaluated-feedback source, explicit
   comparison worker, policy-bearing audits/rechecks and actual installed-model
   smoke scenario. Mocked metrics and storage alone do not establish improvement.

In parallel, review delivered detector/matching modules and agree the smallest
integration into measurements and rule evaluation. Do not assume their completion
automatically finishes those integration steps. Add system health/capability/model
readiness routes when needed for frontend integration.

## Working agreements

- Prioritize the usable audit, review and correction prototype over new reliability
  features. Preserve safeguards required by execution behavior that remains enabled.
- Keep frontend contracts current as endpoints become available; coordinate changes
  to implemented schemas instead of treating planned contracts as frozen guarantees.
- Keep teammate-owned detector/matching modules separate from rulebook/API work.
- Respect the user's current instruction not to run tests automatically. Author
  coverage and report honestly which checks were actually performed.
- No commits or pushes are authorized by updating this tracker. Every push requires
  the specific approval defined in AGENTS.md.

## Reviewed export integration — validation status

A four-phase reviewed-dataset export implementation now covers frozen selection,
durable enqueue, one-attempt bounded archive worker, five HTTP endpoints and
contract-generator alignment. It preserves imported reviewed bytes; it does
not implement annotation corrections, rechecks, feedback improvement or detection.
All four phases are applied and the five API artifacts are regenerated. The
current source inventory is 36 implemented, 14 planned, 50 total; this is an HTTP availability
count, not a backend-completion percentage. The earlier failed run is historical; the user reports the baseline now passed.
The new correction/recheck implementation remains unexecuted. Migration application,
real PostgreSQL/worker/download verification and a fresh full-suite pass remain
open gates. Do not mark this feature tested or production-ready.

## CDIF Feature 3 checkpoint verification

The source-only checkpoint was prepared from the supplied hash-verified snapshot. No live repository, service/model readiness, migration execution, pytest, HTTP scenario or inference was verified. Three static review passes are documented in the Notion checkpoint. The real checkout and teammate changes take precedence when integrating. No production-readiness or zero-defect guarantee is made.

## Correction/recheck validation gate

All four correction/recheck source phases are applied and all five API artifacts
are regenerated. The inventory is 46 implemented and 4 planned, 50 total.
The original package application report records the earlier source-only step;
the working source now also contains the subsequently authorized integration fixes.
Regression execution after those fixes and runtime verification remain pending.
See api/CORRECTIONS.md for limits, compatibility changes and application dependencies.
