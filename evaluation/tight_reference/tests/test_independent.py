"""A reference drawn from the person model alone, without the outline being judged (independent.py)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

SPEC = importlib.util.spec_from_file_location("tight_reference_independent",
                                              Path(__file__).resolve().parents[1] / "independent.py")
ind = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ind)
H, W = 300, 400


def person(x0, x1, y0=40, y1=280):
    mask = np.zeros((H, W), bool)
    mask[y0:y1, x0:x1] = True
    return SimpleNamespace(mask=mask, box=(x0, y0, x1 - 1, y1 - 1), score=0.99)


class Recorder:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def predict(self, point_coords, point_labels, box, multimask_output):
        self.calls.append({"coords": point_coords, "labels": point_labels, "box": box})
        return np.stack([self.answer, ~self.answer, self.answer]), np.array([0.9, 0.95, 0.5]), None


def test_the_prompt_comes_from_the_person_model_and_the_result_stays_off_other_people():
    this, other = person(100, 180), person(170, 260)
    this.mask[:, 170:180] = False  # the model gave the shared strip to the other person
    answer = np.zeros((H, W), bool)
    answer[40:280, 95:200] = True  # the segmentation spills into the neighbour
    recorder = Recorder(answer)
    reference = ind.reference_from_detection(recorder, [this, other], 0)
    [call] = recorder.calls
    assert list(call["box"]) == [100, 40, 180, 280]  # the inclusive box, ended after its last pixel
    inside = [this.mask[int(y), int(x)] for (x, y), label in zip(call["coords"], call["labels"]) if label == 1]
    on_other = [other.mask[int(y), int(x)] for (x, y), label in zip(call["coords"], call["labels"]) if label == 0]
    assert inside and all(inside) and on_other and all(on_other)
    assert reference[150, 120] and not reference[150, 190]  # the neighbour's body is taken out
    assert not (reference & cv2.erode(other.mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)).any()
