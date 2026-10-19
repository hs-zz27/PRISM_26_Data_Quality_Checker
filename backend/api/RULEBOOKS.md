# Rulebook HTTP workflow

Four read endpoints are implemented under `/api/v1/rulebooks`: list latest books,
read a book (optional `version`), list versions and list real review attribution.
Four review mutations are also implemented: PUT `/{rulebook_id}`, POST
`/{rulebook_id}/confirm`, POST `/{rulebook_id}/reject` and POST
`/{rulebook_id}/revisions` (201). Creation and extraction-job HTTP routes remain
planned. Mutations reuse the existing transactional Python review services.

Use `limit` (1–100, default 20) and `offset` (nonnegative, default 0) on lists.
Omit `version` for the latest snapshot, including drafts and rejected books;
an explicit positive version never falls back to latest. Version/review histories
are ascending. Unknown books/versions return 404; existing books can have no
attributed review records, and offsets beyond the end return empty pages.
Listing preserves repository order and returns only one latest version per book.

Identifiers must contain non-whitespace text. Invalid fields return safe 422
ErrorResponse payloads; missing records return 404, storage errors 503 and
unexpected defects 500. Each error has a generated request ID. Reads do not
start jobs, repair data, inspect dataset assets or perform model inference.
No GET declares mutation-only 409 responses. Dependency configuration is
resolved at request time, not import time.

Use [implemented-openapi.json](implemented-openapi.json) for live client generation
and [examples.json](examples.json) for request/response fixtures. The combined
`openapi.json` also includes planned operations. Rulebooks must already exist;
creation and extraction are not callable through HTTP yet.

Generated `openapi.json`, `implemented-openapi.json`, `examples.json` and
`ENDPOINTS.md` must be refreshed
with `.venv/bin/python -B -m backend.api.build_contract`. They are generated
outputs, not hand-edited sources. Examples are independent synthetic snapshots.

## Review sequence and conflict handling

1. Read the latest book and retain its version.
2. PUT the complete `guideline_text` and `rules` plus `expected_version` and
   nonblank `reviewer_id`. Rule decisions are explicit; the book remains draft.
   Omitted rules are removed, retained IDs retain origin, new IDs use reviewer
   origin and exact citations must match the submitted text. Duplicate IDs fail.
3. Confirm only a nonempty latest draft whose retained rules are all confirmed.
   Remove rejected rules first. Unsupported checks remain visible for review;
   confirmation does not promise an executable check or invent a threshold.
4. Reject only the latest draft; individual decisions are preserved.
5. Revise a confirmed/rejected latest book to a new draft with every included
   rule reset. A draft cannot be revised because it is already editable.

Every successful mutation appends exactly one version and attributed history
record atomically. Historical snapshots are immutable. Stale versions return
409 VERSION_CONFLICT; refresh and ask the reviewer to resubmit, never merge or
retry a decision automatically. All mutations can return INVALID_RULEBOOK_STATE;
only confirm can return RULEBOOK_NOT_READY. PUT also returns safe 422 errors
for invalid origins, duplicate IDs and mismatched citations. Invalid requests
reject unknown fields and boolean/string `expected_version` values. Reviewer
IDs are attribution, not authenticated identities; authentication is out of scope.

For editing controls, `examples.check_catalog` provides registered check names,
human-readable meanings, scopes and parameter schemas. For example, 5% outside
a box maps to `mask_outside_box` with `max_outside_fraction: 0.05`; this is an
illustrative threshold, not a default. Do not invent thresholds for vague rules.
Confirmation records reviewer approval; it does not guarantee that a check is
supported or its parameters can be evaluated. Invalid content uses safe 422
responses; stored corruption and unexpected failures remain server errors.

The generated examples show edit v1→v2, confirm v2→v3 and revise v3→v4.
Reject-v1→v2 is an alternative branch, not an action after confirmation.
Do not treat these independent fixtures as a stateful server. No calls to
Ollama, inference, dataset inspection or job recovery occur in this workflow.
