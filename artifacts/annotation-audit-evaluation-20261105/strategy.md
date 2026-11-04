# Evaluation strategy

## Purpose

Investigate why the visual audit sometimes returns an outcome that appears correct while its explanation describes the wrong person or annotation outline. Keep the evaluation small and tied to the core workflow: a confirmed guideline, supplied annotation, model proposal, and human review.

## Test case

- One public LabelMe image, sample `258421`, containing a man and a woman.
- Compare the human-reviewed baseline annotation with an altered annotation whose man polygon is shifted right by 76.8 pixels.
- The target is `/shapes/0`, the man's supplied polygon. The woman is unchanged.
- Expected for this example: baseline polygon acceptable; shifted polygon erroneous. These expectations were not included in model requests.
- Image SHA-256: `fffae741a0fd8453cf199338cf1988ad992fb38208b37aac28808e25244572a8`.
- Baseline annotation SHA-256: `6dff908bc14e61aee78aef2a9e83a027027f2635c6920efa14de14b0efa7268a`.
- Altered annotation SHA-256: `77efb47d13a1c518e185d960df84e0cd4521d9fac3c5b43f92477187277baf43`.

## Procedure

1. Pin the model and inference configuration so the baseline and altered cases use the same model, rulebook, geometry, image, and inference settings.
2. Send the full original image, the target-highlighted overlay, and aligned original/overlay crops. Use one request for the target polygon.
3. Apply the v7 backend consistency safeguards during aggregation. For prompt comparisons, locally override only the system wording; do not change the production version or its configuration.
4. Compare outcomes and read the full explanation. A pass/fail label alone does not count as a reliable result if its explanation refers to the wrong object or geometry.
5. Run an overlay ablation on the altered case: hide all other annotation outlines while keeping the four image inputs otherwise byte-identical. This checks whether contextual outlines explain target confusion.
6. Run a neutral-color wording ablation with the same images to check whether naming colors in the prompt primes the model's explanation. The altered case completed; run the matching baseline with the same wording before claiming the prompt both detects errors and preserves correct annotations.

## Inference settings

- Model: `qwen3-vl:4b-instruct`
- Model digest: `sha256:ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b`
- Local Ollama CPU inference, temperature 0, 16,384 context, 2,048 output tokens, 10 threads.
- Diagnostic request timeout: 1,200 seconds for the completed focused runs. This override applied only to the local comparison script; production settings were not changed.
- All visual prompt/overlay comparison runs used the same four attachments per target. The target-only ablation changed only whether non-target annotation marks were drawn on overlay attachments.

## Boundaries

This is a one-image diagnostic with a single target and a single local model. It cannot establish general precision, recall, or suitability for production. No annotation was changed, no database was written, and no reviewer feedback policy was activated by these comparisons.
