# Annotation audit evaluation

This folder preserves the latest local evidence for guideline extraction and visual polygon review. The visual evaluation uses one reviewed LabelMe example; it is diagnostic evidence, not a general accuracy claim.

- [Strategy](strategy.md) describes the controlled comparisons and model settings.
- [Results](results.md) summarizes completed runs, interpretation and the pending run.
- `extraction/` contains the validated 10-rule local draft and its validation summary.
- `reports/` contains the source JSON reports copied from `.cache/validation/`.
- `segmentation/` contains an offline Mask R-CNN comparison of the supplied polygons with independently predicted person masks. `run.py` was run from the git-ignored `.cache/` workspace, so its input paths do not resolve inside this folder.

The visual runs used the local Qwen3-VL 4B model. The experimental prompt overrides and overlay ablations are not production settings. Both the altered case and the matching baseline under the neutral color wording have now completed. The segmentation check is a proof on one image and is not part of the application.
