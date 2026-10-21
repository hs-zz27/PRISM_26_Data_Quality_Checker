# Annotation/mask corrections and linked rechecks

## Source and verification status

This feature targets captured commit `be424f833820e8e527dce8b6536ea1200279c935`.
All four handoff source phases are applied. The original handoff records three
static review passes. Later reported regression failures exposed stale contracts,
missed inventory assertions and serialization before model validation. Those
issues are corrected, and all five API artifacts are regenerated with user
authorization. Tests were not rerun after those fixes. HTTP scenarios, migrations,
PostgreSQL and worker execution remain separate verification gates.

Regenerate all five API artifacts together when the transport source changes:

```bash
.venv/bin/python -B -m backend.api.build_contract
```

Local assistant: run this command only after separate explicit authorization,
from the repository root with the existing environment. Do not hand-edit generated
artifacts or suppress structural/freshness failures. It regenerates openapi.json,
implemented-openapi.json, examples.json, ENDPOINTS.md and PLANNED_ENDPOINTS.md.
The checked-in files now include correction/recheck schemas, examples and status.

Four existing planned operations are promoted in place. Static source inventory
is 36 implemented, 14 planned, 50 total; runtime route/schema comparisons remain
unexecuted gates. Operation IDs are unchanged:

| Method and path (prefix /api/v1) | Operation ID | Success |
| --- | --- | --- |
| POST /datasets/{dataset_id}/samples/{sample_id}/mask-uploads | uploadCorrectionMask | 201 MaskUpload |
| POST /datasets/{dataset_id}/samples/{sample_id}/revisions | correctAnnotations | 201 AnnotationRevision |
| GET /datasets/{dataset_id}/samples/{sample_id}/revisions | listAnnotationRevisions | 200 RevisionPage |
| POST /audits/{audit_id}/rechecks | recheckSamples | 202 AuditStarted |

## Setup and application safety

Use only this handoff's original-baseline path. Check manifest hashes and exact
patch contexts first; stop on differing source, unexpected existing new files,
or a migration-012 collision. Never overwrite newer edits, apply patches twice,
or combine a previous feature's patches with this packet. Apply additive migration
012 using the project's explicit migration procedure after separate authorization.
Do not edit migrations 001–011. No automatic startup migration or worker is added.
Coordinate a maintenance window: do not start the updated API or existing audit /
generic workers until migration 012 is separately authorized and applied. Their
new linked-recheck guards reference the new table even for ordinary queue scans.

## Frontend correction sequence

1. Open the immutable audited revision and independently read the current catalogue
   head. Keep the old audit visible. Editing requires the expected CURRENT head;
   there is no silent rebase from old audited evidence.
2. Edit the complete object list, not a partial patch. Objects omitted from the
   list are removed. IDs are unique integers 1..65535 (not booleans). Zero belongs
   to mask background. Up to 10,000 objects are allowed. Boxes are exactly four
   integer coordinates, one-based inclusive, ordered and inside the verified
   image. Canvas conversion subtracts one from left/top; widths include +1.
3. Labels need not be `person`. Preserve supported non-ASCII labels and order.
   Quotes, controls, Unicode line separators, blank labels and invalid UTF-8 are
   rejected, not silently escaped or truncated. Maximum label length is 255.
4. To replace a mask, send multipart fields `mask` (one file) and
   `expected_revision` (one decimal integer). File name/type and client dimensions
   are not trusted. The server reads its pinned image and decodes the actual PNG.
   Keep the returned upload ID, base revision and expiry. Do not send it to another
   dataset, sample or revision. It is consumed only by a successful save.
5. POST the complete correction with `expected_revision`, `reviewer_id`, nonblank
   `reason`, `objects`, optional `mask_upload_id` and optional `source_audit_id`.
   Omitted/null mask ID keeps the exact old mask bytes. The resulting foreground
   mask IDs must equal the new object IDs; additions/removals usually require a
   corresponding mask. Zero objects requires a mask with no foreground IDs.
6. A stale revision returns 409 STALE_SAMPLE_REVISION. Refresh head and evidence,
   reconcile edits explicitly and upload again if needed. Do not auto-retry POST.
   An expired, consumed or scope-mismatched mask returns 409 MASK_UPLOAD_UNAVAILABLE.
   A source audit must pin the same dataset/sample/base revision; otherwise return
   409 SOURCE_AUDIT_MISMATCH. Correcting a newer head from an old audit requires
   refreshed evidence and an appropriate source audit (or an omitted source ID).
7. Read history with limit 1..100 (default 20), nonnegative BIGINT offset. Order is
   newest revision first. Total/items share a repeatable-read snapshot; an empty
   past-the-end page is 200. Revision 1 uses its actual created timestamp and null
   parent/reviewer/reason/source audit, with mask_changed=false. Unknown historical
   later revisions without correction provenance have null attribution and
   mask_changed=null; this is an explicit compatibility extension, not a fabricated
   review or backfill.

The saved annotation is UTF-8 Penn-Fudan text with real dimensions/channel count,
object count, origin (1,1), and the complete ordered boxes/labels. Serialization
is checked against the existing parser. New sample objects, issues and counts
are recomputed; integrity_status=valid denotes basic supported structural
consistency, NOT rulebook compliance or acceptance. A damaged old annotation can
be replaced if the pinned image and resulting mask are usable. Missing/corrupt
image or mask evidence is refused safely, never assigned invented dimensions.

## Mask representation and resource policy

Supported masks are raw PNG grayscale 8/16-bit or palette-indexed 1/2/4/8-bit.
Palette indices, including high values, are IDs; palette colors are not IDs.
RGB/RGBA previews, one-bit grayscale and animated PNGs are unsupported. Foreground
IDs are limited to 65535, deliberately matching supported 16-bit storage. Source
image and retained/replacement mask bytes are never re-encoded. Image replacement,
other formats and automatic label/box/mask repair are out of scope.

| Environment override | Default | Server bound |
| --- | --- | --- |
| CORRECTION_MAX_MASK_BYTES | 16777216 | 1..67108864 |
| CORRECTION_MAX_ASSET_BYTES | 67108864 | 1..268435456 |
| CORRECTION_MAX_ANNOTATION_BYTES | 2097152 | 1024..16777216 |
| CORRECTION_MAX_PIXELS | 16000000 | 1..64000000 |
| CORRECTION_UPLOAD_TTL_SECONDS | 3600 | 60..86400 |

Mask/annotation caps must fit the verified-asset cap. Actual request bytes are
bounded by mask cap plus the existing multipart overhead (default 65536 bytes),
including chunked bodies; a declared Content-Length is not the measurement.
Multipart fields/header/spool limits remain localized to this feature. Blocking
file/PNG/SQL work runs off the event loop. A bounded asset is buffered individually
for verification/decoding; this is NOT a streaming PNG decoder or a hard wall-clock
sandbox. Pixel/byte caps bound work but do not establish benchmark, memory,
concurrency, socket or decompression-time guarantees. Use trusted ingress body,
header, timeout and concurrency limits, and validate deployment budgets locally.

## Persistence, locks, counts and files

Correction publication locks dataset -> sample head -> optional staged mask.
It revalidates exact base/manifest, source audit and staged identity after waiting.
New manifest/revision, provenance, known aggregate-count delta, head compare-and-
swap and once-only upload consumption commit together. Server expiry is checked
again at the final consumption statement after SQL waits. This statement is the
expiry cutoff, not eventual HTTP acknowledgement. Decoding and file preparation
occur before write locks, never while a database transaction remains open.

Dataset object_count describes current heads where known: subtract the old known
annotation count and add the new count in O(1). A previously unknown total stays
null, even if this one sample becomes known; no huge-dataset recount or invented
zero is performed. Sample count and original dataset issues remain import evidence.

Correction files are immutable unique UUID-owned files below the existing private
asset root at corrections/v1/<uuid>/. Annotation and raw PNG bytes have recorded
size/SHA-256; reads verify confined ordinary files from the same open descriptor.
The asset root must remain disjoint from import/export roots. No source-file
symlinks, mutable dataset source paths or public static directory are used.
Files and relevant directory metadata are flushed before SQL publication. A
staged mask's private immutable key can become the final revision's mask key;
no move/rename of a possibly referenced file is needed.

Filesystem and PostgreSQL are not one transaction. Lost commit acknowledgement
returns a safe 503 and requires reconciliation through current sample/history,
audit/job/list reads before any new POST. Never delete possibly committed files
or retry writes automatically. Failed preparation/publication can leave harmless
unregistered or expired-upload files. Retain them for a separately reviewed
operator cleanup procedure after checking durable references; no automatic GC,
whole-root deletion or arbitrary traversal is supplied.

## Linked recheck execution

The parent must be terminal (completed, failed or cancelled), snapshot-backed,
and resolvable to immutable catalogue revisions. Active parents return 409
AUDIT_NOT_READY. Legacy sources or non-null historical feedback policy return
409 AUDIT_NOT_RECHECKABLE. Selected IDs must be parent members; their unique
nonempty list and expected-current-revision mapping must have exactly matching
keys. The endpoint supports at most 3,000 selected IDs per request.

The exact recorded confirmed rulebook ID/version is loaded, never its latest
version. Original connectivity/allowed-label configuration is retained. The
write transaction rereads parent identity, locks the dataset and selected heads
in stable sample-ID order, validates all expected pins and enqueues while retaining
those locks. That transaction is the revision-selection cutoff. Later corrections
do not change the saved manifests. Child audit, job, pins and durable parent /
requester linkage commit atomically. Public AuditRun is unchanged; parent context
remains in AuditStarted, with private lineage stored in audit_recheck_lineage.

Compatibility decision: recheck jobs use kind `audit`, NOT planned kind `recheck`.
The existing explicit worker already claims AUDIT and runs the supervised engine:

```bash
.venv/bin/python -B -m backend.app.workers.audit --once
```

Local assistant: do not run this command without separate worker/database
execution authorization. Use the existing audit worker, never a new recheck
queue or a generic job runner. Remove --once only for an explicitly authorized
normal audit queue loop.

Poll existing /jobs/{job_id}, /audits/{audit_id} and sample queue/detail routes.
The completed result href is /api/v1/audits/{child_audit_id}. Use the existing
/jobs/{job_id}/cancel route: queued cancels immediately; running enters cancelling;
terminal requests conflict with JOB_ALREADY_FINISHED. Existing audit recovery,
leases, claim tokens, supervised timeouts, sample isolation and retry rules remain.
Generic claim-without-kind and generic recovery exclude linked rechecks; generic
direct claim, progress/terminal mutations and cancellation reject them. AUDIT
worker queue claims and lease renewal remain allowed. Audit-aware cancellation
owns its existing transaction, not a nested generic job lock.

A correction or successful machine recheck never means accepted. Child samples
start pending_review with no inherited human decisions. Parent findings/reviews
and frozen export membership/provenance/bytes are unchanged. To export corrected
revisions, review the child explicitly and request a new export from that audit.

## Safe failures and remaining gates

404 NOT_FOUND covers missing resources. 409 covers the documented conflicts and
an unconfirmed recorded rulebook. 422 VALIDATION_ERROR covers malformed structural
input/selection or invalid rule parameters; SOURCE_NOT_CORRECTABLE covers unsupported
source evidence. 413 UPLOAD_TOO_LARGE and 415 UNSUPPORTED_MEDIA_TYPE cover bounds
and transport. 503 DEPENDENCY_UNAVAILABLE covers configuration, integrity/storage
and unresolved durable outcomes. Unexpected programming failures remain 500
INTERNAL_ERROR. All use existing safe envelopes/request IDs; no public SQL, paths,
private keys, claim tokens or exception internals are returned.

Runtime gates remain: dependency/pinned-framework compatibility; migration upgrade
and rollback/deferred constraints; simultaneous saves and head-lock enqueue;
PNG modes/resource bounds and exact annotation collector reads; mask expiry/
consumption; upload disconnect cleanup; audit CLI and process supervision;
claim replacement/cancellation/stale recovery; parent/child review/export isolation;
all five generated artifacts and full regression suite. Authored tests must be
run separately; syntax/patch/hash checks are not substitute evidence.

Authentication, image editing, cross-format conversion, automatic correction,
feedback evaluation/learning, GC and generic job-framework changes are not added.
Reviewer_id is untrusted prototype attribution, not authentication or authorization.
Do not deploy as a public multi-user service without a separate security review.
