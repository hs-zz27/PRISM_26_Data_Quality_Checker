# Backend progress and team handoffs

Last updated: 20 October 2026 (Asia/Kolkata).
Source snapshot: `c2ba410` — Implement manual rulebook creation workflow.

This is the working tracker for implemented features, teammate assignments and
the next delivery steps. Update it when work is delivered, reviewed, integrated
or tested. Plans and prepared prompts do not count as completed implementation.

## What is built

The following code exists in this checkout. "Implemented" does not mean the
latest complete test suite or real-model evaluation has passed.

- **Dataset and measurement foundation:** Penn-Fudan loading, annotation and
  metadata parsing, label checks, box validity/overlap, mask IDs/dimensions,
  mask-to-box measurements and component measurements.
- **Guideline extraction service:** configurable local Ollama client,
  registry-aware check/parameter instructions, citation-linked draft rules and
  persistence. Its extraction HTTP endpoint is still planned.
- **Rulebook workflow:** manual draft creation, saved-book reads/listing, version
  history, full draft replacement, confirmation, rejection, new draft revisions
  and attributed review history.
- **Audit engine:** confirmed-rule evaluation, evidence-linked findings,
  PostgreSQL batch processing, durable progress/results/failures and job state.
  Existing leases, recovery, cancellation, timeouts and retry safeguards remain.
- **Immutable assets:** snapshot capture, manifests, audit-to-revision pins,
  verified asset reads, thumbnails and mask previews. Application-owned dataset
  revision selection and annotation corrections remain unfinished.
- **Human review:** audit metadata and sample queue/detail reads, finding
  decisions/history, and separate sample decisions/history. Apparently correct,
  flagged, uncertain and unavailable evidence remain reviewable.
- **Frontend handoff:** generated combined and implemented-only OpenAPI,
  request/response examples, endpoint index and rulebook workflow documentation.
  [The complete planned endpoint reference](api/PLANNED_ENDPOINTS.md) and
  [screen-by-screen workflow guide](api/FRONTEND_WORKFLOW.md) cover the remaining
  30 operations for frontend mocks. Future job fixtures cover all six states;
  these contracts do not add runtime routes.

The latest completed implementation is **manual draft rulebook creation**:
`POST /api/v1/rulebooks`. All four creation phases are present in the checkout:
strict models/service, HTTP/error handling, authored PostgreSQL coverage, and
updated contracts/docs. The server assigns a fresh ID, version 1, reviewer origin
and draft states. Creation does not confirm rules or fabricate review history.

## Current HTTP surface

There are **19 implemented operations and 30 planned operations** in the combined
49-operation reference. These counts describe HTTP availability, not completion
percentages or equal amounts of implementation work.

- Rulebooks: create, collection read, detail read, version history, review history,
  replace draft, confirm, reject and start a draft revision — nine operations.
- Audits: metadata, sample queue and sample detail — three operations.
- Assets: revision-pinned sample asset delivery — one operation.
- Jobs: status polling and cancellation — two operations.
- Reviews: finding write/history and sample write/history — four operations.

Use [the endpoint index](api/ENDPOINTS.md) for the exact inventory and
[implemented-openapi.json](api/implemented-openapi.json) for live client
generation. [The combined contract](api/openapi.json) includes future designs;
planned routes cannot be called yet.

## What has been assigned or prepared for teammates

### CPU person-detection teammate

**Assignment:** standalone CPU person detector using explicitly supplied/cached
weights, with typed detections in original-image coordinates and clear failure
behavior. The user reported that this teammate is building it.

**Integration status:** completion and delivery have not been verified here.
`app/inference/person_detector.py` remains a placeholder in this checkout; that
does not establish the status of her separate branch or clone. No person-detector
implementation or audit integration should be marked complete until delivered
and reviewed.

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
- New creation model/service, HTTP and PostgreSQL tests exist. A passing full-suite
  result for this latest feature has not been supplied in the conversation.
  Tests were not executed during the review.
- The earlier included-router inventory failure was fixed in `15ea976` using
  FastAPI's `iter_route_contexts`, preserving duplicate-registration detection.
- The handoff text `p4.md` has a corrupted generator snippet near line 450. The
  actual `api/build_contract.py` is correct. Handoff notes saying regeneration
  is still pending are stale; generated artifacts are already current.
- CPU detector/matching delivery, integration and real-data quality remain
  unverified. No integration claims should be inferred from the prepared prompt.

## What to build next

**Immediate verification:** obtain the latest full-suite result, including the
opt-in isolated PostgreSQL tests, for manual creation and resolve any failures.
Do not run tests automatically; the user controls execution.

**Recommended next main feature: plain-text rulebook extraction over HTTP.**
Implement the currently planned `POST /api/v1/rulebooks/extractions` workflow,
connecting the existing extraction/persistence service to the existing durable
job and polling conventions. Preserve reviewer confirmation, safe model/storage
errors and valid result links. Scope the worker execution/cancellation behavior
explicitly; current audit workers must not be assumed to execute extraction jobs.
Do not add a new queue or reliability framework merely to expose the feature.
This is a recommendation, not authorization to start coding it.

After that, the proposed delivery order is:

1. Dataset import and dataset/sample read APIs, including authoritative revision
   selection and managed snapshot capture.
2. Audit creation and listing APIs, reusing the existing batch engine and explicit
   confirmed rulebook/revision pins.
3. Annotation and mask correction revisions, correction history and rechecks that
   retain old audit evidence and human decisions.
4. Reviewed dataset exports, download delivery and an inclusion/exclusion manifest.
5. Reviewer-feedback improvement with evaluated baseline/candidate behavior and
   explicit activation; feedback storage alone does not satisfy the SRS.

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
  the specific approval defined in [AGENTS.md](../AGENTS.md).
