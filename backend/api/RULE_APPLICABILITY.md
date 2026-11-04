# Rule applicability and scoped visual audit

Status: v4 (observations) and v5 (scoped assessments) are implemented and were run against real local models
on 2 November 2026 with results that do **not** meet visual acceptance (see
[Verification status](#verification-status)). v6 (focused per-target evidence) is implemented and tested with
mocked models only; it is opt-in and its real-model results are not yet recorded.

## The problem

A LabelMe sample had two person polygons and **no supplied bounding boxes**; the boxes shown in the
review screen were derived from the polygons. The visual audit:

1. ran bounding-box requirements against polygons and reported failures;
2. produced one sample-level explanation for the whole image, so a claim about one person could land on
   the other (the unchanged polygon was said to miss feet and legs);
3. answered an occlusion requirement with a statement about all outlines, without addressing the
   requirement's own condition.

Cause: every visual rule runs through the same `annotation_guideline` check, which had no notion of which
geometry a rule is about or which annotations can be checked against it, and a visual finding is one
sample-level result per rule.

## Representation

### Rule applicability (metadata of a rule)

`Rule.applicability` (optional; omitted from JSON when unset, so rules saved earlier keep their JSON,
semantic hash and pins) states which **supplied** geometry the requirement judges:

| Field | Meaning |
|---|---|
| `geometry` | Kinds the requirement is about: `box`, `polygon`, `mask`. Empty means the requirement does not depend on a geometry type (a label rule, an occlusion rule): every annotation is eligible. |
| `combine` | `all`: every listed kind must have been supplied for an annotation (a polygon judged against its own box). `any`: alternatives (a box or a polygon). |
| `source` | `derived-v1` (backend derivation, algorithm version pinned in the name) or `reviewer`. |
| `status` | `clear`, or `ambiguous` when the text could not be resolved confidently. A reviewer-set applicability is always `clear`. |
| `basis` | One sentence a reviewer can read: why the backend decided this. |

**Derivation (`derived-v1`)** reads the requirement text only, never its context sentences, and looks at
the *role* of each geometry term rather than its presence:

- a sentence containing a definition marker (`means`, `refers to`, `is defined as`...) is ignored;
- a geometry term in a negated or excluded phrase ("other than boxes") is not a requirement and makes the
  result `ambiguous`;
- one geometry kind: that kind;
- two or more kinds joined by an alternation (`or`, `and/or`, `either`) with no comparison between them:
  `any`;
- two or more kinds related by a comparison between them (`inside`, `within`, `match`, `agree`, `overlap`,
  `contains`...): `all`;
- two or more kinds with neither, or both: `any` with `status: ambiguous`.

It is a deliberate, versioned heuristic, not language understanding. Anything it cannot settle is shown to
the reviewer as ambiguous rather than guessed. `Annotations other than boxes must ...` or `The box includes
the head and the polygon follows the person` are ambiguous by design; the second should probably be split
into two rules.

### Annotation provenance

Eligibility depends on how each geometry was **obtained**, per annotation:

| Geometry | Values |
|---|---|
| box | `supplied`, `derived_from_polygon` (never an annotation), `absent` |
| polygon | `supplied`, `absent` |
| mask | `supplied_not_shown` (a Penn-Fudan mask file; the visual check is never shown pixel masks), `referenced_unavailable` (referenced file missing or unreadable, distinct from never supplied), `unsupported_encoding` (kept, not decoded), `absent` |

A mask is never usable evidence for the visual check, so a mask requirement is always unavailable here, with
a reason that says which of the four situations applies.

### Target scope (record of one check)

Every v5 visual finding stores `evidence.measurements.model_proposal.scope` (`TargetScope`): the image
identity (`sample_id`, `sample_revision`, `image_key`, hashes), the applicability used and whether it
came from the rule (`rule`) or was derived at audit time (`derived_at_audit`), the `eligible` and
`ineligible` annotations (with why), the per-target `results`, rejected references, the model's own reported
outcome, and `outcome_source`. Annotations are identified by their stable `object_key` (the source key such
as `/shapes/0`, `/annotations/12`, or `penn_fudan:3`); `object_id` is only a display number.

Real output, unavailable (no model call; `outcome_source: no_model_call`):

```json
{
  "outcome": "unavailable",
  "reason": "No supplied bounding box exists in this sample. The boxes shown are derived from polygons and are not annotations, so this requirement was not checked. Ineligible annotations: /shapes/0 (person); /shapes/1 (person).",
  "scope": {
    "applicability": {"geometry": ["box"], "combine": "all", "source": "derived-v1", "status": "clear",
                      "basis": "The requirement judges the supplied bounding box only."},
    "eligible": [],
    "ineligible": [
      {"object_key": "/shapes/0", "missing": ["box"],
       "detail": "Has no supplied bounding box; the box shown for it is derived from its polygon and is not an annotation"},
      {"object_key": "/shapes/1", "missing": ["box"], "detail": "Has no supplied bounding box; ..."}],
    "unavailable": {"code": "supplied_box_not_available", "detail": "..."},
    "outcome_source": "no_model_call"
  }
}
```

Real output, judged (mock model; per-target results decide the outcome):

```json
{
  "outcome": "fail",
  "reason": "person /shapes/0: fail - <model reason>; person /shapes/1: pass - <model reason>",
  "scope": {
    "applicability": {"geometry": ["polygon"], "combine": "all", "source": "derived-v1", "status": "clear", "basis": "..."},
    "applicability_origin": "derived_at_audit",
    "eligible": [{"object_key": "/shapes/0", "object_id": 1, "label": "person",
                  "geometry": {"box": "derived_from_polygon", "polygon": "supplied", "mask": "absent"}}, "..."],
    "ineligible": [],
    "results": [
      {"object_key": "/shapes/0", "outcome": "fail", "model_outcome": "fail", "geometry": "polygon", "condition": "applies", "flags": []},
      {"object_key": "/shapes/1", "outcome": "pass", "model_outcome": "pass", "geometry": "polygon", "condition": "applies", "flags": []}],
    "model_reported_outcome": "fail",
    "outcome_source": "derived_from_target_results"
  }
}
```

## What happens at audit time (prompt version `annotation-guideline-v5`)

1. **Eligibility first, no model.** The rule's applicability (own, else derived now) is applied to each
   annotation's provenance. If nothing is eligible the finding is `unavailable` with a code
   (`supplied_box_not_available`, `no_eligible_annotation`, `no_annotations`, `mask_not_supplied`,
   `mask_referenced_unavailable`, `mask_encoding_unsupported`, `mask_not_visible_to_visual_check`,
   `measurement_not_supported`) and a sentence for the reviewer. **No request is made and the server is
   not contacted.**
2. **Mixed samples.** Eligible annotations are judged. Ineligible ones are listed as context with the
   reason; they are shown to the model as context and may not be judged.
3. **Scoped request.** The request carries `requirement_scope` (eligible targets with the geometry
   supplied for each, context objects, applicability) and a system prompt that tells the model to judge only
   eligible targets, never describe polygon geometry as a box error, report whether the requirement's own
   condition (for example occlusion) holds, and not treat blur or hidden anatomy as evidence. The original and
   overlay images, native geometry rows, compact encoding, non-truncating options and size bounds are
   unchanged from v3.
4. **Per-target response.** The model returns an assessment per eligible target
   (`object_key`, `outcome`, `geometry`, `condition`, `reason` of at most 300 characters).
5. **Deterministic combination** (`combine_results`), no second model call:
   - only assessments of eligible targets count; unknown, context-only and repeated references are rejected
     and recorded;
   - a target the model did not assess is `unsure`, never `pass`;
   - an assessment of geometry that was not supplied for that target, or not named by the requirement, cannot
     pass or fail (`unsure`, flag recorded);
   - a failure that blames a bounding box that was never supplied (without saying it is derived), or offers
     blur as proof that hidden anatomy should be annotated, is downgraded to `unsure`;
   - `condition: not_present` excludes the target; if every target is excluded the finding is `unavailable`
     (`condition_not_present`), not a pass; `condition: unclear` makes the target `unsure`;
   - sample outcome: any `fail` -> `fail`; else any `unsure` -> `unsure`; else any applicable
     `unavailable` -> `unavailable`; otherwise `pass`. Conflicting repeated assessments make that target
     `unsure`, rather than trusting whichever answer arrived first. The model's own summary outcome is kept (`model_reported_outcome`) but never overrides
     the per-target results.
6. A requirement that states a measured value (for example "at most 3 pixels") is never judged visually,
   even when a reviewer-authored rule names `annotation_guideline`: it is `unavailable` with code
   `measurement_not_supported` and no model call. The development tolerance remains unsupported until a
   measurement check exists.

The finding contract stays sample-level; identities and eligibility live in the structured evidence above.

## Preserved through the whole lifecycle

| Stage | Behaviour |
|---|---|
| Extraction | Applicability is derived for `annotation_guideline` rules from the requirement text and stored on the rule; an `ambiguous` result also adds an extraction note starting `Geometry applicability is ambiguous:`. Unsupported and non-visual rules carry none. |
| Review | Presented in the rule (`applicability.status`, `applicability.basis`) and the extraction notes. A draft edit that omits the field keeps the previous value (`PRESERVED_RULE_FIELDS`); an explicit `null` clears it; a new object sets `source: reviewer`. |
| Confirmation / persistence | Part of the stored rulebook JSON, so confirmed versions keep it unchanged. |
| Pinning | Part of `RulePin.rule` and its semantic hash, so a changed applicability is a different pinned rule. The derivation algorithm version is in `source` (`derived-v1`) and in the pin's prompt version (v5). |
| Audit | Recorded in each finding's `scope`. A rule with no stored applicability is derived at audit time and recorded as `derived_at_audit`. |
| Recheck | A recheck copies the original audit's stored request including its execution pin, so rechecking a v3 audit stays v3. Reviewer feedback may name annotations (`object_keys`); correction proposals may then edit only those. |

**Ambiguous applicability** is never silently resolved: the rule carries `status: ambiguous`, a `basis`
sentence and an extraction note; v5 treats it as alternatives (any of the mentioned kinds) so no annotation is
excluded on a guess, and each finding records which applicability was used. A reviewer settles it by setting
`applicability` (`source: reviewer`) or splitting the rule.

## Versioning and historical behaviour

- Historical pins/rules and v1/v2 requests retain the original golden hash assertions.
  Full v1/v2/v3/v4 requests are also compared to the frozen pre-PR prompt builder on the same machine.
  Rendered-PNG request hashes are not portable across Pillow builds; the original v3 hashes remain
  recorded without being overwritten. See `backend/tests/golden/README.md` for the precise test boundary.
- New audits pin v5 by default. `FEEDBACK_PROMPT_VERSION=annotation-guideline-v3` selects v3 for comparison.
  Existing audits keep their stored pin; nothing historical is rewritten.
- Main already published `annotation-guideline-v4` with `VisualAssessment.observations`. That protocol,
  its object inventory and optional inspection regions remain supported. The scoped `assessments` protocol
  is v5, not a replacement schema under the v4 name. `FEEDBACK_PROMPT_VERSION=annotation-guideline-v4`
  selects the preserved protocol. Existing v4 tests still exercise it; the new compatibility test compares
  v4/v5 request schemas and the database recheck test verifies stored v3/v4/v5 pins survive default changes.
- v5 can assess at most 32 eligible annotations per request, matching its response bound. A larger scope
  returns `unavailable` with `context_limit` before any model call; targets are never silently truncated.
- Rules saved before this change have no `applicability` key, keep their hash and are derived at audit time
  under v5.

## Prompt v6: focused per-target evidence (opt-in)

Why: on the real 768 x 1152 example the couple occupies about 120 x 330 pixels, drawn with 1-2 px lines and tiny
`#N` labels, and both real models answered about the wrong person or the wrong annotation. v6 gives the model one
annotation at a time, larger, with a distinct marking. It is **an experiment**: whether it helps a 4B CPU model is
not established until the owner's real-model comparison is recorded.

- **Version.** `annotation-guideline-v6` with preprocessing `target-focus-overlay-v1`. v3, v4 and v5 are unchanged
  (their requests are compared with a checksum-locked frozen builder, their response schemas and pins with locked
  goldens). New audits still default to v5; select v6 with `FEEDBACK_PROMPT_VERSION=annotation-guideline-v6`.
  A recheck keeps the parent audit's stored pin, v6 included.
- **One request per eligible target** (at most 8 per rule and sample; more is `unavailable` with code
  `too_many_targets` before any inference, never a shortened list). Policy examples are not supported.
  Calls run one after another with no retries.
- **Four images per request**, all drawn from the same verified bytes: (1) the original, untouched; (2) the full
  overlay with only the rule's selected target geometry in thick magenta; other supplied geometry is thin context
  (cyan polygons, yellow boxes), including geometry of the same object;
  (3) a crop of the original and (4) the same window of the overlay, at the same scale. No text labels are drawn
  over boundaries; the target's `object_key` is named in each caption and in the request.
- **Geometry selection.** `focus.judged_geometry` is the intersection of the target's supplied geometry and the
  rule's applicability. A box-only rule highlights only the box; a polygon-only rule highlights only polygons.
  Rules allowing or relating both retain both; rules without a geometry restriction retain supplied geometry.
  Native coordinates and the other geometry remain in the request. The per-request response schema restricts
  `geometry` to this selection; independent backend grounding still downgrades a wrong-geometry response.
- **Crop window** (`backend/app/services/target_focus.py`): the bounding extent of the target's own SUPPLIED
  selected geometry, widened by half its size and at least 96 px on every side, clipped to the image, never smaller than
  192 px where the image allows, long side scaled toward 768 px (enlarged at most 2x). It depends only on that geometry
  and the image size: not on other annotations, a baseline, an answer key or an earlier answer. The request records
  `crop_window_original_px`, `crop_scale`, `crop_size` and which crop sides really are image edges
  (`crop_edges_touching_image_edge`); the crop border is otherwise only the crop's own border.
  **Limit:** a person displaced further than the margin from its outline falls outside the crop; the full image
  still shows the scene. This limit is tested.
- **Response** (`FocusedAssessment`): `object_key`, then `condition` (applies / not_present / unclear), then
  `compliance` (yes / no / unclear), `geometry` (box / polygon), a one-sentence `reason`. The backend derives the
  outcome from the structured fields: not_present excludes the target; an unclear condition or compliance is
  `unsure`; otherwise yes is `pass` and no is `fail`. The explanation never decides the outcome, so it cannot contradict
  the condition field; it can still be wrong or unsupported, and it is shown to the reviewer unchecked beyond the
  existing grounding downgrades.
- **Every eligible target gets an explicit result.** A failed request (invalid reply, incomplete response, context
  limit, ...) makes only that target `unavailable` with `call_failed` and the code; an answer given for another
  annotation is rejected (`no_assessment`, `reference_mismatch`, recorded in `rejected_references`); a repeated or unknown
  key is never trusted. An applicable target without a trusted answer prevents an overall pass; a valid failure on
  another target is kept. Context annotations are never targets.
- **Conditional rules.** The condition is answered before compliance. Absent for every target: the finding is
  `unavailable` (`condition_not_present`), never a pass. Unclear: `unsure`. A failure of one rule never implies a failure of
  another; each request carries one requirement.
  The prompt first asks the model to identify the visible target and test the rule's class and visibility
  conditions. A non-person under a person-only rule should be `not_present`, even near a person. Annotation
  labels are not used as a hard filter: they may themselves be wrong. This is an instruction to the model,
  not proof of correct classification; real-model verification remains required.
- **Cost.** Requests = eligible targets per rule (2 on the example). Each carries four images, roughly
  2,800 image tokens against a 16,384 context (an estimate, not a measurement), so expect a rule to take about 2 to 3
  times a v5 rule on this sample. Measured per-call timings are recorded by the comparison script.
- **Diagnostics** (opt-in, local evaluation only): `GuidelineClient(capture_diagnostics=True)` keeps, in memory, the
  failure code and a bounded excerpt (2,000 characters) of a reply that failed validation. Prompts and images are never
  captured, and nothing is stored or returned by the API.

### Applicability algorithm `derived-v2` (v6 only)

`derived-v1` treated any geometry word within a few words after a negation as excluded. For the confirmed
occlusion rule ("... do not extend boxes or polygons to cover body parts that cannot be seen") it dropped boxes and
reported the rule ambiguous: a rectangle-only sample would have lost that rule. `derived-v2` counts a term as excluded only
when an exclusion marker applies to the term itself ("other than boxes", "without a polygon", "no masks"); a
prohibition on an action is not an exclusion. v2 resolves that rule to `box or polygon`, clear. v1 is frozen
(golden-tested): stored rules, v5 audits and extraction continue to use it, and a rule's stored applicability is never
rewritten. Moving extraction to v2 is a separate, reviewable decision.

## Correction proposals

A proposal may edit only the annotations the reviewer named (`object_keys` on the recheck request), else the
annotations the recheck judged `fail` or `unsure`, else (neither known) behaves as before. A proposal that
edits any other annotation is discarded whole and returned as `unsure`; untouched annotations are never
rewritten. It stays a draft: the existing preview and explicit apply (new revision, then recheck) are
unchanged, and nothing is approved or applied automatically.

## Frontend integration (no frontend code was changed)

Generated types are updated. All additions are optional, so existing screens still type-check and pass their
tests. To benefit, the audit screen should:

1. show `rule.applicability` (geometry, `basis`) in rulebook review, highlight `status: ambiguous`, and let the
   reviewer set or clear it by saving the rule with `applicability` (send `source: "reviewer"`,
   `status: "clear"`);
2. for a visual finding, read `evidence.measurements.model_proposal.scope`: show `unavailable.detail`
   instead of a bare reason, list `eligible`/`ineligible` annotations with their `object_key`, and show the
   per-target `results` (an annotation-level pass or fail), using `object_key` to select the annotation
   (never `object_id`);
3. optionally send `object_keys` on `POST .../recheck` to say which annotations a comment is about;
4. read `correction_proposal.editable_object_keys` to show which annotations a draft may change.

Contract changes: `Rule.applicability?`, `RecheckRequest.object_keys?`, `ExecutionPin.prompt_version`
gains `annotation-guideline-v5`, `RecordedPrediction.scope?`, new schemas `RuleApplicability`, `TargetScope`,
`ScopeObject`, `IneligibleObject`, `ObjectGeometry`, `ScopeImage`, `ScopeUnavailable`, `TargetResult`. No
path changes.

## Verification status

**Real-model results (owner, 2 November 2026; one image pair, not a benchmark).** Recorded in
`PR19_VERIFICATION.md`. qwen3-vl:4b-instruct with v4: five of six executions unusable, the one usable polygon-fit
result failed the baseline. 4B with v5: the box rules were correctly skipped without inference; the baseline passed; the
shifted man's polygon failed; the unchanged woman's assessment was omitted (recorded `unsure` / `no_assessment`, which is
a coverage gap and not an accusation); the occlusion rule's explanation called the shifted polygon correct, which is
unsupported. 8B with v5: both annotations assessed, **the shifted polygon passed**, and the explanations swapped the two
people. A larger model did not fix the example, and `model_verified` only means the client's checks passed.

**Subsequent owner-run v6 checks (3 November 2026)** correctly distinguished the baseline and shifted L01
polygons, including the unchanged person. C01 exposed a missed shortened box, and a feedback control
incorrectly failed the correct box. These are case results, not a benchmark. See `PR19_VERIFICATION.md`
for the retained reports and implementation changes between runs. New request-mechanics tests do not
establish visual accuracy; correct and erroneous examples must both be evaluated.

## Known limitations

### Core visual feedback recheck (local v6 follow-up)

A reviewer comment now accompanies a fresh visual assessment of the same image and annotation. This
follows the mentor's distinction between rechecking an unchanged error after feedback and checking a
new revision after a human edits the annotation. The recheck does not edit annotations or make a final
review decision. The comment is an allegation to verify, not an answer or replacement for the guideline.

When feedback specifies `object_keys`, only those targets receive the comment; the remaining eligible
objects receive their usual comment-free requests. With no keys, the comment is image-level context.
Unknown keys are reported before inference. Feedback cannot make an ineligible geometry eligible.
Rules, original images, annotations, overlays and crops remain unchanged by the comment. Responses can
agree, disagree or remain uncertain. Invalid replies remain unavailable. Feedback is recorded for the
recheck and does not automatically become a policy for later batches.

The text-only comparison experiment is retired: it interpreted prior answers without inspecting the
image again, so it could not recover a missed visual error. `RecordedPrediction.feedback_comparison?`
and its frontend reader remain only for saved experimental records, labelled historical. New v6
results omit that field and make no additional text-only request. The independent feet-localization
experiment remains opt-in and is not called by auditing, feedback or extraction.

The eight-target limit, per-request time/context/response limits and worker deadline remain unchanged.
V6 remains opt-in; earlier protocol requests and default v5 configuration are unchanged. Keep earlier
reports with their code hashes, since the v6 name alone does not distinguish these local iterations.

This restores the intended interaction, not proven feedback accuracy. The known C01 false alarm from
misleading feedback remains unresolved evidence. Before wider use, compare correct and erroneous
annotations both without feedback and with the same targeted comment, preserving unchanged objects
and failures. Do not retry until the desired answer appears or claim general learning from one case.

- Derivation is a heuristic over English requirement wording; unusual phrasing may be ambiguous or wrong.
  Reviewers can override per rule.
- The overlay draws supplied boxes and polygons only; nothing shows masks to the visual check.
- Requirements that mix several obligations in one sentence are judged as one rule.
- The grounding downgrades use narrow phrase patterns and cannot judge whether an explanation is true.
- A 4B CPU model may not follow the structured schema reliably; malformed output becomes `unavailable`
  (`invalid_proposal`), never a finding. Explanations can still be unsupported.
- v6 costs more calls and images per rule; its crop guarantee ends at its margin.
- The frozen-builder checksum test (`backend/tests/golden/feedback_prompt_before_pr19.py`) hashes raw bytes, so a checkout
  with CRLF line endings fails it although the content is identical; run it on LF checkouts (Linux or macOS).

Local verification commands and reporting instructions: `backend/api/PR19_VERIFICATION.md`.
