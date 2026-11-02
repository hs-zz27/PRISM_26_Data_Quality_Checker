# Rule applicability and scoped visual audit

Status: implemented on branch `fix/rule-applicability`. Mocked-test evidence only; no real-model
result is recorded here. See [Verification status](#verification-status).

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

Every v4 visual finding stores `evidence.measurements.model_proposal.scope` (`TargetScope`): the image
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

## What happens at audit time (prompt version `annotation-guideline-v4`)

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
   - sample outcome: any `fail` -> `fail`; else any `unsure` -> `unsure`; else any `pass` -> `pass`; else
     `unavailable`. The model's own summary outcome is kept (`model_reported_outcome`) but never overrides
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
| Pinning | Part of `RulePin.rule` and its semantic hash, so a changed applicability is a different pinned rule. The derivation algorithm version is in `source` (`derived-v1`) and in the pin's prompt version (v4). |
| Audit | Recorded in each finding's `scope`. A rule with no stored applicability is derived at audit time and recorded as `derived_at_audit`. |
| Recheck | A recheck copies the original audit's stored request including its execution pin, so rechecking a v3 audit stays v3. Reviewer feedback may name annotations (`object_keys`); correction proposals may then edit only those. |

**Ambiguous applicability** is never silently resolved: the rule carries `status: ambiguous`, a `basis`
sentence and an extraction note; v4 treats it as alternatives (any of the mentioned kinds) so no annotation is
excluded on a guess, and each finding records which applicability was used. A reviewer settles it by setting
`applicability` (`source: reviewer`) or splitting the rule.

## Versioning and historical behaviour

- `annotation-guideline-v1`, `-v2` and `-v3` pins, requests and results are unchanged byte for byte; this is
  tested against digests captured from `main` before the change (`backend/tests/golden/`).
- New audits pin v4 by default. `FEEDBACK_PROMPT_VERSION=annotation-guideline-v3` selects v3 for comparison.
  Existing audits keep their stored pin; nothing historical is rewritten.
- Rules saved before this change have no `applicability` key, keep their hash and are derived at audit time
  under v4.

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
gains `annotation-guideline-v4`, `RecordedPrediction.scope?`, new schemas `RuleApplicability`, `TargetScope`,
`ScopeObject`, `IneligibleObject`, `ObjectGeometry`, `ScopeImage`, `ScopeUnavailable`, `TargetResult`. No
path changes.

## Verification status

Mocked tests (no model): see the pull request description for exact counts. **Not established:** how a real
`qwen3-vl:4b-instruct` behaves on the per-target schema. The comparison script
`evaluation/annotation_error_pack/tools/compare_visual_audit.py` runs the unchanged and shifted samples under v3
and v4 against a real local model; its results must be recorded before this change is relied on. One flagged
altered image does not establish accuracy; the script counts false alarms and unsupported extra allegations
separately.

## Known limitations

- Derivation is a heuristic over English requirement wording; unusual phrasing may be ambiguous or wrong.
  Reviewers can override per rule.
- The overlay draws supplied boxes and polygons only; nothing shows masks to the visual check.
- Requirements that mix several obligations in one sentence are judged as one rule.
- The grounding downgrades use narrow phrase patterns and cannot judge whether an explanation is true.
- A 4B CPU model may not follow the per-target schema reliably; malformed output becomes `unavailable`
  (`invalid_proposal`), never a finding.
