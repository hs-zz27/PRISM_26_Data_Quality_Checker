"""Offline proof: compare supplied LabelMe polygons with independent Mask R-CNN person masks.

Not part of the application. No database, Ollama, audit or annotation changes.
The segmenter never sees the supplied annotations: it predicts people from the image
alone, then plain code matches each supplied polygon to a predicted person and
measures overlap and boundary distance. One image; not an accuracy evaluation.
"""
import hashlib
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw
from torchvision.models.detection import MaskRCNN_ResNet50_FPN_V2_Weights, maskrcnn_resnet50_fpn_v2
from torchvision.transforms.functional import to_tensor

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / '.cache/validation/core-workflow-20261104T012520Z'
OUT = Path(__file__).resolve().parent / ('run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
CASES = {'baseline': SOURCE / 'baseline-258421.zip', 'altered': SOURCE / 'altered-258421.zip'}
PERSON = 1            # COCO category id for person
MIN_SCORE = 0.5       # detection score floor (diagnostic choice, reported)
MASK_THRESHOLD = 0.5  # soft-mask binarisation (torchvision convention)


def load(path):
    with zipfile.ZipFile(path) as archive:
        image_bytes = archive.read('258421.jpg')
        annotation = json.loads(archive.read('258421.json'))
    return image_bytes, annotation


def polygon_mask(points, size):
    canvas = Image.new('L', size, 0)
    ImageDraw.Draw(canvas).polygon([tuple(p) for p in points], fill=1, outline=1)
    return np.array(canvas, dtype=bool)


def boundary(mask):
    m = mask.astype(np.uint8)
    return (m - cv2.erode(m, np.ones((3, 3), np.uint8))).astype(bool)


def boundary_distances(a, b):
    """Distances (px) from each boundary pixel of a to the nearest boundary pixel of b."""
    b_edges = boundary(b)
    dist = cv2.distanceTransform((~b_edges).astype(np.uint8), cv2.DIST_L2, 5)
    return dist[boundary(a)]


def compare(supplied, predicted):
    inter = np.logical_and(supplied, predicted).sum()
    union = np.logical_or(supplied, predicted).sum()
    d_ab = boundary_distances(supplied, predicted)
    d_ba = boundary_distances(predicted, supplied)
    both = np.concatenate([d_ab, d_ba])
    return {
        'iou': round(float(inter / union), 4) if union else 0.0,
        'supplied_outside_person_fraction': round(float(1 - inter / supplied.sum()), 4),
        'person_not_covered_fraction': round(float(1 - inter / predicted.sum()), 4),
        'boundary_distance_mean_px': round(float(both.mean()), 1),
        'boundary_distance_p95_px': round(float(np.percentile(both, 95)), 1),
    }


def overlay(image, supplied_masks, people, path):
    base = np.array(image.convert('RGB')).astype(np.float32)
    for person in people:
        base[person['mask']] = base[person['mask']] * 0.5 + np.array([0, 200, 0]) * 0.5
    out = base.astype(np.uint8)
    colours = [(255, 0, 255), (0, 160, 255)]
    for (key, mask), colour in zip(supplied_masks.items(), colours):
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(out, contours, -1, colour, 3)
    ys, xs = np.where(np.any(list(supplied_masks.values()) + [p['mask'] for p in people], axis=0))
    pad = 40
    crop = out[max(ys.min() - pad, 0):ys.max() + pad, max(xs.min() - pad, 0):xs.max() + pad]
    Image.fromarray(crop).save(path)


def main():
    torch.manual_seed(0)
    torch.set_num_threads(10)
    weights = MaskRCNN_ResNet50_FPN_V2_Weights.COCO_V1
    load_started = time.monotonic()
    model = maskrcnn_resnet50_fpn_v2(weights=weights).eval()
    load_seconds = time.monotonic() - load_started
    OUT.mkdir(parents=True)
    report = {
        'experiment': 'Offline segmentation proof; not an application feature or accuracy evaluation',
        'model': 'torchvision maskrcnn_resnet50_fpn_v2', 'weights': str(weights),
        'device': 'cpu', 'torch': torch.__version__, 'min_score': MIN_SCORE,
        'mask_threshold': MASK_THRESHOLD, 'model_load_seconds': round(load_seconds, 1),
        'note': 'The segmenter receives only the image. Supplied polygons are compared afterwards by code. '
                'No tolerance is applied: numbers are evidence, not verdicts.',
        'cases': {},
    }
    for case, path in CASES.items():
        image_bytes, annotation = load(path)
        image = Image.open(BytesIO(image_bytes)).convert('RGB')
        started = time.monotonic()
        with torch.inference_mode():
            prediction = model([to_tensor(image)])[0]
        seconds = time.monotonic() - started
        people = []
        for i in range(len(prediction['labels'])):
            if int(prediction['labels'][i]) == PERSON and float(prediction['scores'][i]) >= MIN_SCORE:
                people.append({'index': len(people), 'score': round(float(prediction['scores'][i]), 4),
                               'box_xyxy': [round(float(v), 1) for v in prediction['boxes'][i]],
                               'mask': prediction['masks'][i, 0].numpy() >= MASK_THRESHOLD})
        supplied = {f'/shapes/{n}': polygon_mask(s['points'], image.size) for n, s in enumerate(annotation['shapes'])}
        rows = {}
        for key, mask in supplied.items():
            candidates = [{'person': p['index'], **compare(mask, p['mask'])} for p in people]
            candidates.sort(key=lambda c: c['iou'], reverse=True)
            rows[key] = {'best_match': candidates[0] if candidates else None, 'all_people': candidates}
        overlay(image, supplied, people, OUT / f'{case}-overlay.png')
        report['cases'][case] = {
            'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
            'annotation_sha256': hashlib.sha256(json.dumps(annotation, sort_keys=True).encode()).hexdigest(),
            'inference_seconds': round(seconds, 2),
            'people_detected': [{k: v for k, v in p.items() if k != 'mask'} for p in people],
            'supplied_polygons': rows,
        }
        print(f'{case}: {len(people)} people, {seconds:.1f}s', flush=True)
        for key, row in rows.items():
            print(' ', key, json.dumps(row['best_match']), flush=True)
    (OUT / 'report.json').write_text(json.dumps(report, indent=2))
    print('Saved', OUT, flush=True)


if __name__ == '__main__':
    sys.exit(main())
