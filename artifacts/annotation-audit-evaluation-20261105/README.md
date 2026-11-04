# Annotation audit evaluation

This folder preserves the latest local evidence for guideline extraction and visual polygon review. The visual evaluation uses one reviewed LabelMe example; it is diagnostic evidence, not a general accuracy claim.

- [Strategy](strategy.md) describes the controlled comparisons and model settings.
- [Results](results.md) summarizes completed runs, interpretation and the pending run.
- `extraction/` contains the validated 10-rule local draft and its validation summary.
- `reports/` contains the source JSON reports copied from `.cache/validation/`.

The visual runs used the local Qwen3-VL 4B model. The experimental prompt overrides and overlay ablations are not production settings. The neutral-color-wording altered case completed; the matching baseline under that same wording is still needed to check for false alarms.
