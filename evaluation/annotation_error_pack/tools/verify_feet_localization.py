"""Explicit experiment: one image-only localization, compared with two supplied boxes.

The caller selects a confirmed fully-visible-person feet rule and identifies the
person neutrally. This does not route arbitrary guidelines or write audit records.
No answer-key verdict, annotation geometry, or reviewer complaint enters inference.
"""
import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.app.core.feedback import load_execution_pin
from backend.app.inference.feet_localization import FeetLocalizationClient
from backend.app.models.feet_localization import FeetLocalizationPin, FeetLocalizationRecord
from backend.app.models.feedback import EvidenceLimits, RulePin
from backend.app.services.feedback_evidence import _evidence
from backend.app.services.feet_localization import canonical_box, compare_feet_to_box, prepare_feet_localization
from backend.app.services.rule_applicability import object_key
from evaluation.annotation_error_pack.tools.compare_visual_audit import build_sample, load_rulebook


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def render_review(image, box, record, out):
    """Display-only estimated edge ranges; never an input to the localization model."""
    with Image.open(BytesIO(image)) as source:
        original = source.convert('RGB')
    canvas = Image.new('RGB', (max(original.width, 640), original.height + 84), 'white')
    canvas.paste(original, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(box, outline='#0066ff', width=2)
    if record is not None and record.observation is not None:
        for region in record.observation.regions:
            outer = (region.x_min.low, region.y_min.low, region.x_max.high, region.y_max.high)
            inner = (region.x_min.high, region.y_min.high, region.x_max.low, region.y_max.low)
            draw.rectangle(outer, outline='#ff9900', width=2)
            draw.rectangle(inner, outline='#00ffff', width=2)
            draw.text((outer[0], max(0, outer[1] - 12)), region.region_id, fill='#ff9900', stroke_width=1, stroke_fill='black')
    draw.text((8, original.height + 8), 'BLUE: supplied box. ORANGE/CYAN: widest/tightest estimated region.', fill='black')
    draw.text((8, original.height + 26), 'Estimates are rectangles around lower legs and feet, not silhouettes.', fill='black')
    draw.text((8, original.height + 44), 'Model coordinates and uncertainty are uncalibrated. Human review required.', fill='black')
    draw.text((8, original.height + 62), 'This preview was NOT shown to the model.', fill='black')
    canvas.save(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-archive', type=Path, required=True)
    parser.add_argument('--altered-archive', type=Path, required=True)
    parser.add_argument('--format', choices=['coco', 'labelme'], required=True)
    parser.add_argument('--sample-stem', required=True)
    parser.add_argument('--object-key', required=True)
    parser.add_argument('--target-description', required=True, help='Identity only: no defect claim or annotation coordinates.')
    parser.add_argument('--rulebook', type=Path, required=True)
    parser.add_argument('--rule', required=True, help='Explicitly selected confirmed fully-visible-person feet requirement.')
    parser.add_argument('--out', type=Path, required=True, help='New directory; never overwritten.')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--reuse-localization', type=Path, help='Recompare saved estimates without another model call.')
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error('Output directory already exists; use a new path.')
    book = load_rulebook(args.rulebook)
    rule = next((rule for rule in book.rules if rule.rule_id == args.rule), None)
    if rule is None:
        parser.error('Selected rule is missing.')
    target = RulePin(rulebook_id=book.rulebook_id, rulebook_version=book.version, rule=rule)
    if rule.check_name != 'annotation_guideline':
        parser.error('This experiment requires an explicitly selected visual feet guideline.')
    reused = (FeetLocalizationRecord.model_validate_json(args.reuse_localization.read_text())
              if args.reuse_localization else None)
    pin = reused.execution if reused else FeetLocalizationPin.from_transport_settings(load_execution_pin())
    source_limits = EvidenceLimits.model_validate(pin.model_dump(include=set(EvidenceLimits.model_fields)))
    candidates = {}
    with tempfile.TemporaryDirectory() as folder:
        for variant, archive in [('baseline', args.baseline_archive), ('altered', args.altered_archive)]:
            work = Path(folder) / variant
            work.mkdir()
            manifest, assets = build_sample(archive, args.sample_stem, work, annotation_format=args.format)
            image, _, objects = _evidence(manifest, source_limits, assets, compact=True, native_table=True)
            matches = [obj for obj in objects if object_key(obj) == args.object_key]
            if len(matches) != 1:
                parser.error(f'{variant}: target key must identify exactly one annotation.')
            obj = matches[0]
            origin = obj.get('bbox_origin')
            candidates[variant] = {'image': image, 'image_sha256': manifest.original_image.sha256,
                'annotation_sha256': manifest.annotation_text.sha256, 'box': obj.get('bbox'), 'origin': origin}
    if candidates['baseline']['image'] != candidates['altered']['image']:
        parser.error('Candidate images differ; a single localization cannot judge both.')
    image = candidates['baseline']['image']
    payload, metadata = prepare_feet_localization(image, args.target_description, pin)
    if reused and (reused.localization_id != metadata['localization_id']
                   or reused.image_size != metadata['image_size']
                   or reused.request_sha256 != metadata['request_sha256']):
        parser.error('Saved localization does not match this image, target and exact request preparation.')
    for row in candidates.values():
        row['canonical_box'] = canonical_box(row['box'], args.format, row['origin'], metadata['image_size'])
    args.out.mkdir(parents=True)
    with Image.open(BytesIO(image)) as picture:
        suffix = '.jpg' if picture.format == 'JPEG' else '.png'
    (args.out / ('original' + suffix)).write_bytes(image)
    # A readable request description without duplicating base64 image data.
    request_text = {**payload, 'messages': [
        {k: v for k, v in message.items() if k != 'images'} for message in payload['messages']]}
    write_json(args.out / 'request_text.json', request_text)
    report = {'status': 'prepared', 'operation': pin.protocol, 'execution': pin.model_dump(mode='json'),
        'rule': target.model_dump(mode='json'), 'rule_selection': 'explicit_caller_selection_not_automatic_routing',
        'target_description': args.target_description, 'object_key': args.object_key,
        'image_sha256': metadata['image_sha256'], 'request_sha256': metadata['request_sha256'],
        'candidates': {key: {k: v for k, v in row.items() if k != 'image'} for key, row in candidates.items()},
        'code_sha256': {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in [
            ROOT / 'backend/app/services/feet_localization.py', ROOT / 'backend/app/inference/feet_localization.py',
            ROOT / 'backend/app/models/feet_localization.py']},
        'limitations': ['Model-localized anatomy and its uncertainty are not ground truth or calibrated bounds.',
            'This profile only checks lower-leg and foot inclusion for a fully visible person.',
            'Two visible lower legs/feet are required by this prototype; other cases remain unresolved.',
            'Target-to-annotation association is selected by the caller and needs human verification.',
            'No box tightness, polygons, masks, hidden anatomy, or automatic correction is assessed.']}
    write_json(args.out / 'report.json', report)
    for name, row in candidates.items():
        render_review(image, row['canonical_box'], None, args.out / f'{name}_input.png')
    print('Prepared original-image-only localization. Box coordinates and feedback are absent from the request.', flush=True)
    print('Output:', args.out, flush=True)
    if args.prepare_only:
        print('No model call made.', flush=True)
        return 0
    if reused:
        record = reused
        report['reused_localization'] = str(args.reuse_localization)
    else:
        client = FeetLocalizationClient(pin)
        try:
            print('Localizing once from the original image...', flush=True)
            record = client.localize(image, args.target_description)
        finally:
            client.close()
    write_json(args.out / 'localization.json', record.model_dump(mode='json'))
    outcomes = {}
    for name, row in candidates.items():
        result = compare_feet_to_box(record, image_sha256=row['image_sha256'],
            annotation_sha256=row['annotation_sha256'], object_key=args.object_key, box=row['box'],
            annotation_format=args.format, origin=row['origin'])
        outcomes[name] = result.model_dump(mode='json')
        render_review(image, row['canonical_box'], record, args.out / f'{name}_localization.png')
        print(name, result.outcome, '-', result.reason, flush=True)
    report.update(status='completed' if record.model_verified else 'unavailable', results=outcomes,
                  model_verified=record.model_verified, failure=record.failure,
                  same_localization_used_for_both=True, annotation_edits=False)
    write_json(args.out / 'report.json', report)
    print('Review both localization previews before accepting the result. Saved:', args.out / 'report.json', flush=True)
    return 0 if record.model_verified else 1


if __name__ == '__main__':
    raise SystemExit(main())
