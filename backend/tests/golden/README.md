# Prompt compatibility references

`guideline_requests_before_applicability.json` preserves the original hashes captured
on the PR author's environment at commit `75ec790`. Pin and rule hashes are still
asserted exactly, as are the v1/v2 request hashes. The stored v3 request hashes also
include an annotation-overlay PNG, whose encoding and default font rendering depend
on Pillow and its native libraries; they remain historical evidence rather than
portable expected values for every platform.

`feedback_prompt_before_pr19.py` is an exact copy of
`backend/app/services/feedback_prompt.py` from main commit
`7aca2fed5e6216fa4bdc5decf9fa5efa104ae103`, before PR #19 integration. Its SHA-256 is
locked in `test_applicability_compat.py`. Do not refresh it from current code to make
a failing test pass.

The compatibility test constructs requests with both the frozen and current prompt
builders on the same machine and compares every field, including attachment bytes,
for v1/v2/v3/v4. The frozen builder uses the installed shared evidence/rendering
helpers, so image decoding and native annotation collection are tested separately
by the visual-evidence tests. This checks prompt text, schema selection, attachment
ordering, context, options and payload assembly without replacing rendered images
with placeholders or changing historical hashes.
