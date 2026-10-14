# Example rulebook — Penn-Fudan Pedestrian (Phase 1)

**Version:** 1.1 (draft for review, 13 October 2026) · **Data type:** images with bounding boxes, segmentation masks and labels

This is a development example of the rulebook the Data Quality Checker should produce after it reads plain text guidelines and a reviewer confirms, edits or adds rules. The mentors clarified that their shared sample data and rules are just examples. This Penn-Fudan rulebook must not become the complete, fixed product specification. See [MENTOR_REQUIREMENTS.md](MENTOR_REQUIREMENTS.md) for the current requirements.

The initial tool must check every image on a **CPU**, then:

- show flagged, uncertain and apparently correct data to the reviewer
- let the reviewer accept or reject findings and control final decisions
- let the reviewer correct actual annotation errors
- record corrections and feedback and use them to improve future audits

Confidence alone must not automatically accept or reject an image. Rule check outcomes below are tool findings, not final reviewer decisions.

---

## 1. Dataset

**Penn-Fudan Pedestrian Database**

- Download: https://www.cis.upenn.edu/~jshi/ped_html/PennFudanPed.zip (51 MB)
- Project page: https://www.cis.upenn.edu/~jshi/ped_html/

| Folder | Contents |
|---|---|
| `PNGImages/` | 170 street photos (`FudanPed00001.png` …) |
| `Annotation/` | One text file per image with a bounding box and label for each pedestrian (423 in total) |
| `PedMasks/` | One mask per image (`FudanPed00001_mask.png`). Pixel value `0` = background, `N` = pedestrian `N` |

A box line looks like `Bounding box for object 1 "PASpersonWalking" (Xmin, Ymin) - (Xmax, Ymax) : (160, 182) - (302, 431)`. Coordinates start at (1, 1).

> **Licence:** download the dataset from the link above. Do not re-upload or redistribute the images; the authors' readme does not allow reposting without permission.

---

## 2. Guideline source

For this example, guideline text comes from the dataset's own `readme.txt`. Plain text guideline entry is the primary requirement; parsing guideline documents, screenshots and other formats can be implemented later. Rules that come from the readme cite it. Rules marked **Reviewer** are manually authored example rules that must be confirmed during rulebook review.

Sentences cited from `readme.txt`:

- **[R1]** "PedMasks: Mask for each image, also in PNG format. Pixels are labeled 0 for background, or > 0 corresponding to a particular pedestrian ID."
- **[R2]** "In [1], we did not label very small, highly occluded pedestrians. However in this release of the dataset, we have labeled these pedestrians for future detection."
- **[R3]** The annotation format: one `Bounding box for object N "<label>"` line per pedestrian, using the labels `PASpersonWalking` and `PASpersonStanding`.

---

## 3. Rules

Every rule in this example is **mandatory** once confirmed: a suspected violation is flagged for the reviewer. It does not automatically reject the image.

Each rule has three outcomes:

- **Pass:** the check found no issue under its available evidence. The image remains available for reviewer inspection.
- **Unsure:** the tool cannot determine whether the rule holds. Show the uncertainty and evidence to the reviewer.
- **Fail:** the check flags a suspected rule violation. The reviewer confirms or rejects the finding and decides what correction is needed.

Thresholds marked † were measured on the original 170 images. Thresholds marked ‡ are starting values that will be calibrated on the originals during the build.

These thresholds and heuristics belong to this development example. They are not universal acceptance criteria and must be checked against the supplied guidelines and reviewer-confirmed examples before use on another dataset.

### A. Bounding boxes

#### BOX-1 · Every human in the image has a bounding box
Small and partly hidden people count too. *Source: [R2] + Reviewer ("draw a bounding box for every human in the image").*

- **How it is checked:** a CPU person detector runs on the whole image. Each detected person must match an annotated box (IoU ≥ 0.5).
- **Pass:** every detection with confidence ≥ 0.5 matches a box.
- **Unsure:** an unmatched detection with confidence 0.5–0.8, or one that is shorter than 40 px or mostly hidden.
- **Fail:** an unmatched detection with confidence ≥ 0.8 that is at least 40 px tall. ‡
- **Evidence shown:** the unboxed person outlined on the image, with the detector's confidence.

#### BOX-2 · Every box contains a human
No boxes on background, objects or empty space. *Source: [R3].*

- **How it is checked:** the person detector scores the area inside each box, and the box's mask must not be empty.
- **Pass:** person score ≥ 0.5.
- **Unsure:** person score 0.2–0.5.
- **Fail:** person score < 0.2 and the mask is empty. ‡

#### BOX-3 · Each human is boxed exactly once
*Source: [R3].*

- **How it is checked:** overlap (IoU) between every pair of boxes.
- **Pass:** < 0.70.
- **Unsure:** 0.70–0.85.
- **Fail:** ≥ 0.85. † (On the originals, the largest overlap between two people is 0.50.)

#### BOX-4 · The box tightly fits the whole visible person
Head to feet, arm to arm. *Source: Reviewer.*

- **How it is checked:** the largest gap between each box edge and the person's mask edge.
- **Pass:** ≤ 3 px.
- **Unsure:** 4–8 px.
- **Fail:** > 8 px. † (422 of the 423 original boxes are within 3 px.)

### B. Segmentation

#### SEG-1 · Every boxed person has a mask, and every mask has a box
The mask ID must equal the box number. *Source: [R1].*

- **How it is checked:** compare the mask IDs with the box numbers. The answer is exact, so this rule is always Pass or Fail.

#### SEG-2 · Every human in the image is segmented
No person is left without a mask. *Source: [R1], [R2] + Reviewer.*

- **How it is checked:** for each person found by BOX-1's detector, the share of that area covered by mask pixels.
- **Pass:** every detected person is covered by a mask.
- **Unsure / Fail:** the same confidence and size limits as BOX-1, applied to people with less than 10% mask coverage. ‡

#### SEG-3 · The mask covers the whole person and nothing outside them
*Source: [R1] + Reviewer.*

- **How it is checked:** (a) how much of its box the mask fills, and (b) how many mask pixels fall outside the box.
- **Pass:** fill 25–80% and no pixels outside the box. †
- **Unsure:** fill 20–25% or 80–85%, or up to 2% of mask pixels outside the box.
- **Fail:** fill below 20% (body parts missing) or above 85% (background included), or more than 2% of mask pixels outside the box. † (On the originals, fill ranges from 27% to 78% and no pixels fall outside.)

#### SEG-4 · One mask covers one person
Two people must not be merged into one mask. *Source: [R1].*

- **How it is checked:** how many people the detector finds inside one mask, and whether the mask splits into separate large pieces.
- **Pass:** one person per mask, and the mask is one piece.
- **Unsure:** the mask has two or more separate pieces each ≥ 15% of its area, or a second person is detected inside it with confidence 0.5–0.8.
- **Fail:** a second person with confidence ≥ 0.8 is detected inside the mask. ‡

### C. Labels

#### LBL-1 · The label is an allowed class name
The only allowed labels are `PASpersonWalking` and `PASpersonStanding`, spelled exactly. *Source: [R3].*

- **How it is checked:** exact text match, so this rule is always Pass or Fail.

#### LBL-2 · The label matches what the person is doing
`PASpersonWalking` means the person is moving or mid-stride. `PASpersonStanding` means the person is stationary. *Source: Reviewer (definition of the two labels).*

- **How it is checked:** two signals on CPU:
  1. **Leg posture from the mask:** the gap between the legs in the lower third of the mask. Legs apart suggests walking.
  2. **A small local vision model** (Ollama), asked "Is this person walking or standing?" about the cropped person.
- **Pass:** both signals agree with the label.
- **Unsure:** only one signal disagrees, or either signal is uncertain.
- **Fail:** both signals confidently disagree with the label. ‡
- **Evidence shown:** the person crop, the measured leg gap and the vision model's answer.

### Integrity check (not a guideline rule)

Before any rule runs, the tool confirms that the annotation file can be read and matches its image and mask (same name, image size and object count). If it doesn't, flag the affected file as "annotation file broken" and show the reason to the reviewer. Dependent checks are marked as unable to run until the issue is resolved. This is the only basic file check in this example.

---

## 4. How an image is reviewed

1. The tool presents check results for every image, including those with no detected issue.
2. Findings show the rule, measured value, applicable threshold and cited source. Uncertainty and checks that could not run are visible.
3. The reviewer accepts or rejects findings, corrects actual annotation errors and controls the final decision.
4. Log the original annotation, correction, reviewer feedback, evidence and rule version so changes remain traceable.
5. Use the recorded feedback to improve later audits through an implemented and evaluated feedback mechanism. Saving the history alone is not sufficient.

Confidence can help order the review queue, but it cannot replace the reviewer's final decision at this stage.

---

## 5. Changing these rules

Rules are added, removed or edited in this document. When a threshold moves after calibration, the version number goes up and the change is recorded below.

- **1.0:** First draft for review.
- **1.1, 13 October 2026:** Marked the rulebook as an example; prioritized plain text guidelines; replaced automatic image decisions with reviewer control; recorded annotation correction and feedback requirements.
