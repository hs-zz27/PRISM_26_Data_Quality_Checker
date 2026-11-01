# Compact vision prompt verification

This is the historical v2 transport verification. New audits now use the original
and overlay v3 view; see [overlay verification](OVERLAY_AUDIT_VERIFICATION.md).

Date: 1 November 2026. Scope: the local Qwen3-VL 4B Instruct CPU prototype with
a 16,384-token context. This record distinguishes fitting a request from judging
annotation quality correctly.

## Change

New audits use the versioned `annotation-guideline-v2` encoding. Existing v1 pins
retain the previous encoding. Source JSON annotation records and their geometry
are sent once in a table with shared column names; native fields and coordinates
are retained. Correction requests use a shared object-column definition and retain
every polygon vertex. Rule descriptions, parameters and distinct citations remain
available to the model. Duplicate representations and schema display titles are
removed.

The text preflight remains bounded. V2 requires Ollama 0.33.3 or newer and requests
`truncate: false` and `shift: false`. The server enforces actual context capacity;
response validation still checks model identity, completion and prompt-token count
against the pinned output reservation. Image/file/response limits, source hashes,
pending review and explicit correction application remain in place.

## Actual-source preflight

The same confirmed guideline-10 test rule, including the person definition,
visible-only occlusion rule and uncertainty condition, was used for both versions.
The proposed 3-pixel tolerance was excluded.

- LabelMe L01, two objects and 65 vertices: text preflight size decreased from
  7,338 bytes to 4,550 bytes.
- COCO C05, 21 objects and 389 vertices: decreased from 25,311 bytes to 11,907 bytes.
- Correction payloads prepared successfully for both original and altered sources:
  5,382 bytes for LabelMe and 11,538 bytes for COCO with the short diagnostic comment.
  These preflight correction checks used a mock client and are not model-quality results.

Local preflight evidence: `.cache/validation/context-fix-final-20261101/preflight.json`.

## Server safeguard

A separate bounded negative test submitted 110,000 characters with the same model,
16,384-token context, and truncation/context shifting disabled. Ollama 0.33.3
returned HTTP 400: the request contained 110,008 tokens and exceeded its context.
It did not return a shortened-input completion. The check waited behind an active
CPU request; its total elapsed time is not a tokenizer benchmark.

Evidence: `.cache/validation/context-fix-final-20261101/no-truncation-result.json`.

## Automated checks

- Final backend suite: **2,650 passed, 516 skipped**. The skipped tests are not
  counted as validation; the real HTTP/model scenarios below provide separate
  runtime evidence for the tested paths. Full output, including warnings, is in
  `.cache/validation/context-fix-final-20261101/backend-tests.log`.
- The 16 compact-prompt regressions cover native source fields/vertices, large
  complete COCO input, feedback context, immutable inputs, schema constraints,
  server-version refusal, model identity and incomplete responses.
- The frontend review workspace's **10 tests passed**, including preview and
  explicit application of both v1 and v2 proposals. TypeScript and generated API
  freshness checks passed. The proposal parser accepts both protocol versions;
  generated API types include the new execution version.

## Live judgment limits

The final format reached real model inference. The two LabelMe L01 predictions
were verified against the installed model identity but were wrong relative to
Harkamal Singh's recorded visual review: baseline was reported fail and the shifted
polygon was reported pass. Both stored results remain available; no favorable
preliminary result substitutes for these final-format outcomes.

The final large COCO baseline returned unavailable after its model request hit
the configured 600-second timeout. The altered COCO run was explicitly cancelled
to let the smaller feedback scenario proceed. Neither is scored as a correct
visual judgment. The context failure is addressed, but this does not establish
acceptable annotation-audit accuracy or CPU throughput.

The smaller real feedback/proposal run reached processing but was cancelled
before publication when the host was using about 8 GB of swap. Its model was
unloaded to release memory. A completed real v2 correction proposal, its quality,
and application of such a proposal were therefore not verified. The correction
preflights and mocked validation/UI tests above are the evidence for that path;
they must not be described as a successful real model repair.

The audit model currently sees the original image and source geometry as text;
the correction model additionally receives the annotation overlay. Follow-up
accuracy work should evaluate visual annotation evidence against the separate
human-reviewed fixtures, without copying expected outcomes into audit prompts or
changing the answer key to fit model results.

Final live-resource IDs, responses and test logs are retained under
`.cache/validation/context-fix-final-20261101/`. No model weights or dependencies
were changed by this fix, and no model-proposed correction is treated as approved
automatically.
