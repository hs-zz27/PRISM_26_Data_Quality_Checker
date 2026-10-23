# Pedestrian-box enclosure check (opt-in)

Rule semantics: **each annotation box must enclose a visible pedestrian; a box entirely on background is a
violation.** A partly visible but identifiable pedestrian inside the box counts. A person entirely outside the box
does not. The check does not require a whole body, walking motion, box tightness, a visibility percentage or
annotation completeness. It is a model proposal for human review, not proof of accuracy.

## How to use it

1. Confirm a rule whose check is `pedestrian_box_enclosure` (explicit name; guideline text is never matched).
2. Enable the settings below and start a snapshot audit. The pin is frozen when the audit is accepted, so queued,
   retried and rechecked work uses that pin, not later defaults.
3. Without the settings, or with `ENCLOSURE_ENABLED` unset, the rule stays unavailable (`model_execution_unavailable`).
4. A supplied feedback policy is rejected (`POLICY_INCOMPATIBLE`) for a book using this check. Feedback policies
   and `annotation_guideline` behavior are unchanged.

| Setting | Meaning |
| --- | --- |
| `ENCLOSURE_ENABLED` | `true` to opt in. Default off. |
| `ENCLOSURE_MODEL`, `ENCLOSURE_MODEL_DIGEST` | Required. No default model, no download. The digest is checked before and after every call. |
| `ENCLOSURE_SAMPLE_BUDGET_SECONDS` | Required. Must be at most `AUDIT_SAMPLE_MAX_SECONDS` minus 30. |
| `ENCLOSURE_HOST` | Loopback Ollama URL. Default `http://127.0.0.1:11434`. |
| `ENCLOSURE_NUM_CTX` / `NUM_PREDICT` | 16384 / 2048. |
| `ENCLOSURE_NUM_THREAD` | Unset keeps automatic selection. |
| `ENCLOSURE_REQUEST_TIMEOUT_SECONDS` | 600; never above the sample budget. |
| `ENCLOSURE_MAX_BOXES` | 12 (at most 32). More boxes make the sample unavailable; the list is never truncated. |
| `ENCLOSURE_MAX_REQUEST_BYTES`, `MAX_RESPONSE_BYTES`, `KEEP_ALIVE_SECONDS` | Finite bounds. |

## What it stores

One sample-level finding per enclosure rule, pending human review. `evidence.measurements.enclosure` holds the pin,
the source image and annotation hashes, and one record per box (annotation index, object ID, original box, view hash
and size, source and target windows, request hash, outcome, reason, timing, verification). Aggregation: any verified
failing box fails the sample; otherwise any unavailable box; otherwise any unsure box; only all verified passes pass.
Zero boxes, invalid geometry, too many boxes or unreadable evidence are explicit unavailable results, never a pass.
Original annotations, deterministic measurements and reviews are never changed. Views are re-renderable from the
pinned assets and recorded hashes; no mutable dataset path is used.

## Latency and bounds

One sequential request per box. On a 16 GB Windows laptop one request took about 130 s; on the Mac validation about
85 s. The default 900 s sample deadline therefore covers roughly 6 boxes (Windows) to 10 (Mac). Boxes not started
within the budget are recorded `sample_budget_exhausted` and the sample is unavailable. Raise
`AUDIT_SAMPLE_MAX_SECONDS` deliberately for deployments that enable the check; do not raise it globally to make a
smoke test pass.

## Not verified

No real model has run through this path. The request envelope is adapted from the validated experiment, so a later
owner-run smoke check with the owner's model digest is required; byte-identical validation is not claimed. The
validation cohorts were small and controlled; tiny pedestrians (under about 100 px) are unvalidated; padding, marker
and wording changed together. Not exercised: PostgreSQL acceptance and storage, spawn on Windows, revision-aware
rechecks, and the public read responses. The contract needs regeneration after the registry change (below).

Contract regeneration, owner-run (needs approval): `python -B -m backend.api.build_contract`. It changes only the
capabilities check list (4, 4 and 16 lines in `openapi.json`, `implemented-openapi.json`, `examples.json`).
