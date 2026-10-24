# Frontend audit — 21 October 2026

Scope: read-only review of the existing frontend against `backend/api/openapi.json`,
`implemented-openapi.json`, `examples.json`, the API guides, `README.md`, `SRS.md`,
`RULES.md`, `STATUS.md`, `CPU_DETECTION.md`, and `backend/tests/README.md`.
`DESIGN_PRINCIPLES.md` was also read. No application code was changed for this
audit. This file is the only artifact created by the audit.

Historical snapshot: this report records the frontend before Stage 1 fixes and
the subsequent visual redesign. Findings below describe that audit-time state;
the 46 implemented / 4 planned operation inventory remains the contract baseline.

## Verification run

Commands ran from `frontend/`:

| Command | Result |
| --- | --- |
| `npm install --ignore-scripts --no-audit --no-fund` | Exit 0, “up to date”. Warned that `@redocly/openapi-core@1.34.21` requires npm >=9.5.0; environment has npm 9.2.0. npm also printed three `Failed to create stream fd: Operation not permitted` warnings. |
| `npm run typecheck` | Exit 0; `tsc --noEmit`. |
| `npm run lint` | Exit 0; `eslint src --max-warnings 0`. Same stream-FD warnings appeared before the npm script output. |
| `npm test -- --run` | Exit 0; 3 files passed, 34 tests passed (31 coordinate, 2 polling, 1 App). Duration 2.05s. |
| `npm run build -- --outDir /tmp/prism-audit-dist` | Exit 0; TypeScript check passed, 1,722 modules transformed, Vite production build completed. Output was directed to `/tmp` to avoid changing `frontend/dist`. Vite warned that the output directory is outside the project root. |

These are frontend static/build/test results, not proof of live-backend behavior.
The local backend could not be probed: `curl http://127.0.0.1:8000/api/v1/rulebooks`
failed with `curl: (7) Failed to open socket: Operation not permitted`. The Vite
proxy target is configured, but no live HTTP integration was verified. Backend
guidance also records outstanding migration, worker, and runtime verification
gates; the frontend test suite does not exercise those gates.

The contract files currently contain **50** operations: **46 implemented and 4
planned**. The requested count of 49 does not match the machine-readable contracts
or `backend/api/ENDPOINTS.md`; this report accounts for all 50.

## API operation audit

“Correct” means the frontend either uses the implemented operation with the
contract’s method, route, payload and response shape, or (for planned operations)
uses a matching mock. It does not mean the endpoint was runtime-tested.

| # | Operation | Contract | Frontend use and assessment |
| ---: | --- | --- | --- |
| 1 | `GET /health` (`getHealth`) | Planned | Navbar calls it. Dev MSW returns a synthetic response of the right shape, but the navbar does not identify this status as mocked; production has no MSW handler and the route is not callable. |
| 2 | `GET /capabilities` (`getCapabilities`) | Planned | Not called by the UI. The MSW handler is stale: its check names differ from `examples.json`/the contract (for example `bbox_valid`, `bbox_overlap`, and `person_detected`). |
| 3 | `GET /system/status` (`getSystemStatus`) | Planned | Navbar calls it and MSW returns a plausible synthetic shape. Its model/readiness values are synthetic and not labelled as mock in the navbar; production has no mock fallback. |
| 4 | `POST /rulebooks` (`createRulebook`) | Implemented | Not used; the UI only offers extraction, despite manual draft creation being implemented. |
| 5 | `GET /rulebooks` (`listRulebooks`) | Implemented | Called by rulebook and audit pages, but response is incorrectly typed/used as `Rulebook[]`. Contract returns `RulebookPage` (`items`, pagination). `.map`/`.filter` can fail at runtime. |
| 6 | `POST /rulebooks/extractions` (`extractRulebook`) | Implemented | Called with guideline text and reviewer attribution; returns a job and is polled. No API integration test was run. |
| 7 | `GET /rulebooks/{rulebook_id}` (`getRulebook`) | Implemented | Used with optional version query. Shape and version selection align with contract. |
| 8 | `PUT /rulebooks/{rulebook_id}` (`replaceDraftRulebook`) | Implemented | Sends expected version, reviewer, text, rules. Handles `409 VERSION_CONFLICT`, retains current local edits, and provides an explicit reload button. No diff is shown. |
| 9 | `GET /rulebooks/{rulebook_id}/versions` (`listRulebookVersions`) | Implemented | Used as a `RulebookPage`; version selector reads `items`. |
| 10 | `GET /rulebooks/{rulebook_id}/reviews` (`listRulebookReviews`) | Implemented | Not used; reviewer attribution history is absent from the editor. |
| 11 | `POST /rulebooks/{rulebook_id}/confirm` (`confirmRulebook`) | Implemented | Sends expected version and nonblank reviewer ID. UI blocks editing on confirmed versions and offers revise. |
| 12 | `POST /rulebooks/{rulebook_id}/reject` (`rejectRulebook`) | Implemented | Sends expected version and nonblank reviewer ID. |
| 13 | `POST /rulebooks/{rulebook_id}/revisions` (`reviseRulebook`) | Implemented | Sends expected version and reviewer ID; navigates to the returned new version. |
| 14 | `POST /datasets` (`importDataset`) | Implemented | Multipart upload sends `archive`, `name`, `format`; response wrapper is read as `res.job.job_id`. Payload shape is consistent. No upload progress is shown. |
| 15 | `GET /datasets` (`listDatasets`) | Implemented | Called by catalogue and audit pages but typed as `Dataset[]`; contract returns paginated `DatasetPage`. Catalogue `.map` and audit `.filter` can fail. |
| 16 | `GET /datasets/{dataset_id}` (`getDataset`) | Implemented | Used by dataset detail with the expected `Dataset` shape. Errors are not rendered on that screen. |
| 17 | `GET /datasets/{dataset_id}/samples` (`listDatasetSamples`) | Implemented | Used as `SamplePage`; displays `items`. It does not follow `next_offset`, so large catalogues are incomplete. |
| 18 | `GET /datasets/{dataset_id}/samples/{sample_id}` (`getDatasetSample`) | Implemented | Used in detail and review; review requests the audit’s explicit `revision`, supported by contract. |
| 19 | `GET /datasets/{dataset_id}/samples/{sample_id}/assets/{asset_kind}` (`getSampleAsset`) | Implemented | Used indirectly through revision-pinned `assets.*.url` image links. No image/mask decode error state exists. Review displays `mask` rather than the display-only `mask_preview`. |
| 20 | `POST /datasets/{dataset_id}/samples/{sample_id}/mask-uploads` (`uploadCorrectionMask`) | Implemented | Not used; correction UI cannot stage a replacement mask. |
| 21 | `POST /datasets/{dataset_id}/samples/{sample_id}/revisions` (`correctAnnotations`) | Implemented | Called with expected revision, reviewer, reason, objects. **Unsafe payload behavior:** UI fabricates one object and submits it as the complete replacement list, while the contract replaces the entire object list. Existing objects can be dropped. Mask upload is unsupported; no stale-revision conflict recovery. |
| 22 | `GET /datasets/{dataset_id}/samples/{sample_id}/revisions` (`listAnnotationRevisions`) | Implemented | Used for history, but the page has no query error display and uses default IDs/revision values. |
| 23 | `POST /audits` (`startAudit`) | Implemented | Sends required dataset/rulebook/version. **Response mismatch:** contract returns `AuditStarted` (`audit` and `job`), but code treats it as bare `Job` and reads top-level `job_id`; polling/navigation receives an undefined ID. |
| 24 | `GET /audits` (`listAudits`) | Implemented | Called but consumed as `AuditRun[]`; contract returns paginated `AuditPage`. Audit list can crash on array methods. |
| 25 | `GET /audits/{audit_id}` (`getAudit`) | Implemented | Used with `AuditRun` shape. Loading/error states are not shown in the review workspace. |
| 26 | `GET /audits/{audit_id}/samples` (`listAuditSamples`) | Implemented | Uses page `items`, but only fetches the default first page and never follows `next_offset`. Status filters are applied only to that partial page. |
| 27 | `GET /audits/{audit_id}/samples/{sample_id}` (`getAuditSample`) | Implemented | Used for findings, review and pinned asset links. The queue/detail request itself is correct. |
| 28 | `GET /audits/{audit_id}/findings` (`listAuditFindings`) | Planned | Not used. MSW returns an empty page with the expected broad shape. |
| 29 | `POST /audits/{audit_id}/rechecks` (`recheckSamples`) | Implemented | Sends selected IDs, revision map, reviewer. On success it only shows a message; it does not poll the returned job or take the reviewer to its progress/result. |
| 30 | `GET /jobs/{job_id}` (`getJob`) | Implemented | Used by `useJobPolling`. All six status values are represented; polling remains fixed at 1.5 seconds and the hook has no backoff. |
| 31 | `POST /jobs/{job_id}/cancel` (`cancelJob`) | Implemented | Used by the hook with no request body, as required. Export/recheck/feedback screens do not consistently use the job lifecycle UI. |
| 32 | `GET /findings/{finding_id}/reviews` (`listFindingReviews`) | Implemented | Not used; finding history is not visible. |
| 33 | `PUT /findings/{finding_id}/review` (`reviewFinding`) | Implemented | Sends expected review version, resolution and reviewer. **Contract error:** omits required `comment` (empty string is allowed but required), so these writes can receive 422. UI also omits the supported `unsure` resolution and does not surface 409 conflicts. |
| 34 | `PUT /audits/{audit_id}/samples/{sample_id}/review` (`reviewSample`) | Implemented | Separates sample decisions from finding resolutions and sends expected sample/review revisions and reviewer. **Contract error:** blank comment becomes `undefined`, so JSON omits the required `comment` field and the normal no-comment action can receive 422. No optimistic update or visible conflict recovery. |
| 35 | `GET /audits/{audit_id}/samples/{sample_id}/reviews` (`listSampleReviews`) | Implemented | Not used; sample review history is not visible. |
| 36 | `POST /exports` (`startExport`) | Implemented | Sends required audit, format, selection and image flag; reads `ExportStarted` wrapper. Uses a hard-coded demo audit by default and does not require reviewer attribution because the contract does not define it. |
| 37 | `GET /exports` (`listExports`) | Implemented | Used as a paginated `ExportPage`; query uses valid limit/offset. It polls the collection every five seconds rather than following each export job. |
| 38 | `GET /exports/{export_id}` (`getExport`) | Implemented | Not called; page relies on collection polling instead of reading export detail. |
| 39 | `GET /exports/{export_id}/download` (`downloadExport`) | Implemented | Download link uses the correct route. A 409/not-ready JSON response is left to browser download behavior; the page does not present actionable conflict state. |
| 40 | `GET /exports/{export_id}/manifest` (`getExportManifest`) | Implemented | Called with the right route and displayed as a manifest. |
| 41 | `GET /feedback/examples` (`listFeedbackExamples`) | Implemented | Not used. |
| 42 | `POST /feedback/policies` (`createFeedbackPolicy`) | Implemented | Not used. MSW does provide a synthetic handler, but this operation is implemented and should not be treated as planned/live-missing. |
| 43 | `GET /feedback/policies` (`listFeedbackPolicies`) | Implemented | Called but response is typed as `FeedbackPolicy[]`; contract returns `FeedbackPolicyPage`. In dev, MSW intercepts it with synthetic fixture data. |
| 44 | `GET /feedback/policies/{policy_id}` (`getFeedbackPolicy`) | Implemented | Not used. MSW shadows this live route in dev and returns a synthetic record if requested. |
| 45 | `POST /feedback/ground-truth` (`createGroundTruth`) | Implemented | Not used. MSW shadows the live route in dev and returns a synthetic record. |
| 46 | `GET /feedback/ground-truth/{ground_truth_id}` (`getGroundTruth`) | Implemented | Not used. MSW shadows the live route in dev and returns a synthetic record. |
| 47 | `POST /feedback/evaluations` (`evaluateFeedbackPolicy`) | Implemented | Called with hard-coded `policy_id` plus `dataset_id`; contract requires `policy_id`, `ground_truth_id`, and `reviewer_id`, and does not define `dataset_id`. Dev MSW masks this mismatch with a canned 202; live backend will reject it. |
| 48 | `GET /feedback/evaluations` (`listFeedbackEvaluations`) | Implemented | Not used. |
| 49 | `GET /feedback/evaluations/{evaluation_id}` (`getFeedbackEvaluation`) | Implemented | Not used. MSW returns synthetic performance data for this route; this must never be presented as measured accuracy. |
| 50 | `POST /feedback/policies/{policy_id}/activate` (`activateFeedbackPolicy`) | Implemented | Not used. No explicit activation UI exists; MSW has no matching handler, so the “mock feedback” classification is inconsistent even within dev. |

### Transport and contract-wide findings

- At the Stage 0 audit snapshot, `transport.ts` described **27 implemented / 22 planned** operations. Stage 1 corrected routing to derive implementation status from `examples.json`; the contract is 46 implemented / 4 planned. At the snapshot, `PLANNED_PATTERNS` classified every
  `/feedback/*` route as planned even though all ten feedback operations are now
  implemented. MSW handlers consequently shadow implemented feedback reads and
  writes in development; some other feedback routes bypassed to the backend. This
  split created misleading and inconsistent behavior and was corrected in Stage 1.
- The MSW handlers are handwritten stale snapshots, not loaded from
  `examples.json`. Capability names and feedback request/response models differ
  from the current contracts. The comments also claimed 22 planned endpoints;
  Stage 1 replaced those stale comments and fixtures.
- `initTransport()` runs after React renders and races worker startup against an
  800ms timeout. Queries can start before MSW is ready and go to the real server;
  the mock/live source can therefore vary by startup timing. MSW only starts in
  dev, so “Mock” does not mean mocked in a production build.
- API calls use caller-supplied generic response types instead of generated
  operation request/response types. TypeScript therefore accepts incorrect page
  shapes and wrappers. `openapi.d.ts` is generated, but it does not constrain
  `apiGet`/`apiPost` call sites.
- Several implemented pages are labelled `mock` in the sidebar (Corrections,
  Exports, Feedback). Corrections and Exports are live implementations; Feedback
  is also implemented but currently is deliberately shadowed by MSW in dev.
- No end-to-end backend check was possible in this environment. Passing typecheck
  and build cannot establish route, schema, database or worker compatibility.

## Business-rule audit

| Requirement | Finding |
| --- | --- |
| Human reviewer retains final authority | No confidence-based automatic acceptance/rejection was found. Machine finding outcomes remain separate from sample review. Good foundation. The benchmark screen's hard-coded “94.2% / 96.8% / 0.955” reads like measured performance without provenance; replace with actual immutable evaluation results and nullable metrics. |
| Finding decision vs sample decision | Separate components and endpoints exist. Sample accept/reject/needs-correction are distinct from finding accept/reject. Finding `unsure` is unavailable in the UI. Both write payloads omit contract-required `comment` when no text is entered, likely causing 422s. |
| Confirmed rulebooks are frozen | Confirmed and rejected versions are read-only; confirmed books expose “Revise” to create a new draft. This matches the versioned contract. The rulebook list shape bug currently blocks reaching this workflow reliably. |
| Audits pin an exact rulebook version | Launch payload includes explicit `rulebook_version`; audit header displays it. This matches contract intent, but launch response handling prevents the returned job from being tracked. |
| Assets are revision-pinned | Audit detail uses supplied pinned asset URLs and sample read passes `summary.sample_revision`. Dataset thumbnails use supplied asset links. Corrections currently submit a single fabricated full replacement object list and lack mask upload, which can destroy the rest of the catalogue object list. |
| 1-based inclusive coordinates | `lib/coords.ts` contains forward and inverse conversion and validation; 31 coordinate tests passed. Canvas uses the utility for boxes. This is a positive invariant. Cursor coordinate mapping and canvas behavior have no UI-level test evidence. |
| Reviewer attribution | Frontend checks nonblank reviewer ID on its attributed rulebook, review, correction and recheck mutations. Contract-required reviewer fields must stay enforced. Some implemented mutations (dataset import, audit creation, export, extraction, mask staging, manual creation) do not define reviewer_id in their current request schema; do not invent it client-side. Reviewer ID is attribution, not authentication. |
| 409/stale conflicts | Rulebook draft save detects `VERSION_CONFLICT`, retains local form values and provides reload, but no diff. Finding/sample review, correction, and recheck do not show conflict-specific recovery. Do not automatically retry mutations. |
| Six job states | Hook classifies queued/running/cancelling as active and completed/failed/cancelled as terminal, and has cancellation and invalidation logic. Poll cadence is constant (no backoff); its terminal deduplication ref is keyed only by status, so a second job with the same terminal status on the same mounted page can skip its callback. Rechecks and exports do not consistently use the hook; no global job tray exists. |
| “Ready” is catalogue coherence, not clean data | Catalogue shows issue evidence in its own column and audit launch selects `ready`. No obvious clean-data gate was found. However generic “Coherent” language for zero listed issues should not be read as a quality verdict; retain the explicit distinction in redesign. |

## Bugs and correctness risks

### P0 — blocks real workflow or can lose data

1. **Paginated response/array mismatch:** rulebooks, datasets and audits list
   endpoints return pages. Their screens treat responses as arrays. This affects
   the primary catalogue, rulebook and audit workflows and can throw at render.
2. **Audit start response mismatch:** `startAudit` returns `{audit, job}`; the UI
   reads `job_id` at the response root, so polling starts with an undefined ID.
3. **Review request schema mismatch:** finding and sample review contracts require
   `comment` (it may be an empty string). The UI omits it when blank, so the common
   no-comment path can fail validation. Finding review also lacks an `unsure`
   control.
4. **Correction can remove annotations:** correction payload is a complete object
   replacement, but the form submits a one-object array from defaults rather than
   loading/editing the selected sample's full current object list. This can
   silently remove every other object on a corrected sample.
5. **Feedback evaluation cannot be live:** payload omits required truth ID and
   reviewer ID and adds a non-contract `dataset_id`; the dev mock conceals it.
   Static synthetic scores must not be displayed as an observed result.

### P1 — misleading or incomplete operational behavior

1. MSW routes `/feedback/*` to mocks even though feedback is implemented; mock
   coverage is partial, stale and timing-sensitive. Sidebar badges also say
   Corrections/Exports are mock when they are implemented.
2. Queue and catalogue sample lists do not follow pagination. Audit filters only
   affect the fetched page, not the full review queue.
3. Recheck returns a job but is not polled. Export uses five-second list polling
   rather than job/detail lifecycle state. There is no global job tray.
4. Stale finding/sample review and correction conflicts have no actionable
   reload/review path and are not shown clearly. No optimistic updates/rollback
   exist for review writes.
5. Canvas uses React state on every pointer move and renders one positioned DOM
   element per box; there is no resizable pane, space-to-pan, pinch zoom, per-object
   visibility control, or UI-level evidence of large-image performance. It uses
   raw mask rather than colored `mask_preview`; image decode failures are not
   handled. It also contains inline hex colors.
6. Job hook uses a constant interval rather than backoff, and terminal callback
   deduplication is not reset/keyed when `jobId` changes.
7. `DatasetDetailPage`, editor, review workspace, corrections and feedback have
   missing or incomplete query error states. Several mutation errors in review
   are not rendered. The build/test suite does not exercise backend contracts.

### P2 — maintainability and interface quality

1. API call sites use handwritten generic types and several feature-local DTOs
   despite generated schemas; this is how response/payload drift compiles cleanly.
2. Generated and hand-authored docs were stale in places (operation counts, mock
   labels, sample values). Stage 1 updated `frontend/README.md` to 46/4 and current
   verification; at the Stage 0 snapshot this audit recorded 34 passing tests.
3. Hard-coded demo IDs and prefilled sample guideline text make live screens look
   populated before the user has selected real backend resources.
4. Coordinate round-trip unit tests are strong; coverage for API errors, route
   response shapes, review mutations, transport startup and screen states is thin.

## Design critique by screen

The current palette and typography are close to the supplied direction in the
token file, but the page compositions still read as a generic dark admin template.
Repeated large page padding, `max-w-*` wrappers, repeated rounded cards, uppercase
mono labels, broad tables, and identical header/action rows flatten the hierarchy.
`rounded-lg`, backdrop blur and a few decorative shadows recur across screens.
`ReviewCanvas.tsx` hardcodes status hex values rather than consuming tokens. The
navbar encodes API/Ollama/detector readiness as colored dots without readable
state labels, contrary to the color+icon+text requirement. Pending uses indigo,
and the canvas mask palette is purple rather than the specified restrained status
system. The shell has no responsive compact navigation strategy.

| Screen | Specific critique |
| --- | --- |
| Global shell / navbar | Conventional fixed sidebar plus 48px header; brand mark is decorative. Three readiness indicators are mostly dots and remain ambiguous to color-blind users. Mock badges are based on stale route classification. No job tray or clear connection/error state. |
| Rulebook list / extraction | Generic table wrapped in a page header; the “AI Rule Extraction Studio” card and repeated icons look like template chrome. Prepopulated sample text risks being mistaken for user-authored content. No explicit manual draft action despite live endpoint. |
| Rulebook editor | Split is useful, but panes use equal halves and spacious card stacks rather than a text-and-evidence workstation. Citation and rule panes do not synchronize scroll position; exact sentence matching only works at line granularity. Parameter JSON is displayed, not edited/validated against the check catalogue. Review attribution sidebar/history is absent. |
| Dataset catalogue | Repeated table/card treatment; all metadata has similar visual weight. Integrity evidence is present, a good semantic choice, but “Coherent” and green could overstate what Ready means. ZIP area advertises drag/drop but only implements file-input click; upload progress is absent. |
| Dataset detail | Generic five-column thumbnail card grid makes images small and low-density. No catalogue error state or pagination. Image tile has a nice preview but no integrity status evidence until opening detail. |
| Audit list / launch | Generic table and launch modal; hard to distinguish lifecycle progress, rulebook pin and dataset integrity. Launch should surface exact versions/pins and selected sample scope with less card chrome. |
| Audit review workspace | Three-pane structure is the right product shape and canvas is central, but fixed 288/384px side panes are not resizable and can crowd the image. Canvas lacks promised space-pan/pinch/per-object visibility and large-image rendering approach. Right pane stacks card-like findings and dense controls without a strong evidence hierarchy; status is sometimes text-only and box colors are hardcoded. This is the screen where the canvas should dominate. |
| Corrections | Form is a generic admin editor with demo IDs and a one-object replacement model, not an image-led annotation editor. No current image/boxes, mask staging or whole-object editing, so a reviewer cannot safely inspect the proposed correction in context. |
| Exports | Configuration form and repeated package cards; hard-coded audit ID; no explicit waiting/error state for not-ready downloads. Entire screen is marked “mock” despite a live backend contract. |
| Benchmark / feedback | Four generic metric cards present fabricated fixed precision/recall/F1 values as factual, with no case-level evidence, coverage/abstention or evaluation provenance. The policy table models stale “margin/active” concepts and the screen offers a fixed synthetic evaluation instead of explicit training examples, ground truth and activation decisions. Mock badge partially signals synthetic data but conflicts with implemented routes. |

## Prioritized fix list

### P0 — restore safe, contract-correct live flows

1. Change list queries to generated page response types and render `items` plus
   `next_offset` for rulebooks, datasets and audits.
2. Type and unwrap `AuditStarted`; poll `data.job.job_id` and navigate using the
   returned audit identity.
3. Always send required `comment` (empty string when blank) for finding/sample
   reviews; expose finding `unsure`; show server errors and stale-version states.
4. Replace demo correction inputs with selected sample data and edit the complete
   object list; require explicit mask staging only when a replacement mask is
   supplied. Preserve source revision pin and surface 409 refresh/reapply choices.
5. Remove synthetic benchmark scores and stale feedback payload; implement the
   actual explicit examples → policy/truth → evaluation → inspect → activate flow
   before presenting feedback as functional.

### P1 — make transport and review operations trustworthy

1. Rebuild live/mock routing from each operation’s `implementation` flag in
   `examples.json`; mock only the four planned operations. Start/await MSW before
   mounting queries in dev. Correct fixtures to current schemas and label every
   mocked status display.
2. Use generated endpoint-level request/response types so invalid body and page
   shapes fail at compile time.
3. Follow pagination and implement server-side audit filters; poll/reconcile every
   accepted job, including rechecks and exports. Reset terminal tracking for each
   job and add bounded backoff/cancellation behavior.
4. Add optimistic review updates with rollback, 409 preserve-and-reload/diff flow,
   and explicit status/error/empty/loading states across every route.
5. Redesign review canvas around a single SVG/canvas overlay layer and refs for
   pointer movement; add resizable panes, mask preview, per-object controls,
   image-load errors, coordinate readout, and keyboard/pointer affordances.

### P2 — redesign and polish the scientific workstation

1. Replace generic card-first layouts with compact evidence tables, aligned
   metadata rails, hairline grouping and screen-specific density. Make the review
   canvas the dominant surface.
2. Keep colors in CSS variables/Tailwind tokens only; use icon+label+color for
   statuses, tune status hierarchy, and remove decorative blur/shadows/oversized
   radii. Add responsive shell behavior and accessibility review.
3. Replace default demo IDs/content with explicit empty states; show real system
   readiness and an unobtrusive per-screen Mock badge based on actual transport.
4. Add API contract/transport and interaction coverage, then update README claims
   and record actual backend runtime verification separately from frontend build
   success.

## Workspace boundary

No files under `backend/` were changed. Before this audit, the workspace already
had an edited `frontend/README.md` plus untracked frontend files and `.vscode/`;
these pre-existing changes were left in place. Install reported “up to date”; the
production build output was directed to `/tmp/prism-audit-dist`.

## Stage 1 implementation and verification — 21 October 2026

Correctness changes were applied in `frontend/` only. The API client now derives
path, query, JSON body, multipart body, and success response types from the
generated OpenAPI path map. List helpers follow `next_offset`; uploads report
browser transfer progress. Development operation metadata and planned mock
fixtures are built from `backend/api/examples.json`, and MSW startup is awaited
before React renders. Responses mark their actual source for mock badges. Review
writes always include `comment`, optimistically update cached records, roll back
on error, and retain entered comments on 409. Corrections load and edit the full
selected sample object list and can upload a replacement mask before committing
a pinned revision. Job polling uses job-specific terminal deduplication and
bounded backoff; tracked jobs remain in a global tray across route changes.

Audit launch unwraps `{audit, job}` and follows the returned job. The frontend
now uses manual rulebook creation, rulebook/finding/sample review history,
export detail and manifest, and the feedback examples → policy → frozen ground
truth → evaluation → inspect → activate flow. Fabricated benchmark values and
demo IDs in correction/export/feedback entry flows were removed. The OpenAPI
contract defines `GET /exports` (`listExports`) and POST-only
`/feedback/ground-truth`, plus `GET /feedback/ground-truth/{ground_truth_id}`
for an individual frozen record. Export history reads all list pages, preserves
server order (the contract does not specify an ordering), and avoids fixed
collection polling. In-progress exports poll their job and `getExport` detail.
Ground truth is created from explicitly entered adjudication cases and its
returned immutable identity is used for evaluation. The generated activation
request defines `expected_policy_version: 1`.

Stage 1 verification from `frontend/`:

| Command | Result |
| --- | --- |
| `npm run typecheck` | Exit 0 (`tsc --noEmit`). |
| `npm run lint` | Exit 0 (`eslint src --max-warnings 0`). |
| `npm test -- --run` | Exit 0; after the export history correction, 7 files passed and 41 tests passed, including export pagination and job/detail tracking. |
| `npm run build -- --outDir /tmp/prism-export-fix-dist` | Exit 0; 1,961 modules transformed. Vite warned that the output directory is outside the project root. |

No live backend flows were verified. The backend was not started or probed in
this stage; the earlier audit’s `curl` attempt could not open the local socket
in the execution environment. Please live-check rulebook create/edit/confirm/
revise, multipart dataset import and worker progress, audit launch and filtered
queue, finding/sample review and stale conflicts, correction and mask revision,
recheck, export readiness/download, and each feedback policy, ground-truth,
evaluation, and activation response against the running service.

The per-stage `git status` showed the pre-existing `.vscode/` untracked
directory outside `frontend/`; Stage 1 edits are in `frontend/`. The status for
`backend/` was empty. Existing untracked `frontend/node_modules/`,
`frontend/dist/`, and scaffold files remain visible in the workspace; build
output for this stage was written under `/tmp`.

### Contract correction — export history and Stage 1 follow-up

The earlier Stage 1 report incorrectly said that `GET /exports` did not exist.
I checked both `backend/api/openapi.json` and
`backend/api/implemented-openapi.json`: each defines `GET /exports` as
`listExports`, returning `ExportPage` with `items`, `next_offset`, and
pagination metadata. Both define `POST /feedback/ground-truth` as
`createGroundTruth`; neither defines `GET /feedback/ground-truth` as a
collection route, while both define the per-ID GET route. I regenerated both
frontend OpenAPI declaration files from those contracts; the prior generated
files were stale and omitted `listExports`.

The export history screen follows every `listExports` page. It refreshes on user
request or after creating an export, with no fixed collection polling. Active
rows poll the returned job and `GET /exports/{export_id}` detail. Manifest
inspection remains an explicit detail action. The contract does not specify
newest-first ordering, so the view preserves the server's returned order.

I rechecked the other “not used”/missing-route claims from the original
operation table by matching their operation IDs against both OpenAPI files.
Manual rulebook creation, rulebook review history, mask uploads, finding/sample
review histories, export detail, feedback examples, policy create/detail,
ground-truth create/detail, evaluation list/detail, and policy activation are
all present in both contracts. Those table rows described the pre-Stage-1
frontend; the Stage 1 notes record their subsequent uses. The exception is the
planned `GET /audits/{audit_id}/findings`, which is present in the full contract
and remains unused. The current contract count is 50 operations: 46 implemented
and 4 planned.

### Export history verification follow-up

After regenerating declarations, `listExports` is present as `GET /exports` in
both generated files, and `ExportPage` includes `items`, `next_offset`, `limit`,
`offset`, and `total`. `ExportsPage` consumes the page helper and renders the
history; active export rows use their returned `job_id` and detail resource,
while the collection query has no interval polling. Typecheck, lint, tests, and
build all pass after this correction. The final test run had 7 files and 41
passing tests.
