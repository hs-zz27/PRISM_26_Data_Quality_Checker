"""Eligibility computed from REAL imported/stored samples (COCO, LabelMe, Penn-Fudan) and extraction.

Synthetic data only. No model is used.
"""
import pytest

from backend.app.core.config import AssetStorageSettings
from backend.app.models.asset_manifest import AssetManifestEntry
from backend.app.services.feedback_evidence import compute_scope
from backend.tests.applicability_fixtures import (
    imported, labelme_document, polygon_shape, rectangle_shape, requires_importer, v5_pin, visual_rule, WOMAN)
from backend.tests.feedback_fixtures import assets, case
from backend.tests.test_guideline_context import ANIMAL_LINES, ANIMAL_TEXT, ANIMALS, VISUAL, animal_rules, extract, respond
from backend.app.inference.rule_proposals import expand_response, source_units

BOX = visual_rule('R-BOX', 'The bounding box includes the visible head.')
POLYGON = visual_rule('R-POLY', 'The polygon follows the visible outline of the person.')
BOTH = visual_rule('R-BOTH', 'The polygon must stay inside its bounding box.')
MASK = visual_rule('R-MASK', 'The mask covers the whole visible person.')


def scope(rule, manifest, storage):
    return compute_scope(manifest, rule, v5_pin(), storage)


@requires_importer
def test_coco_with_supplied_boxes_and_polygons_uses_each_geometry_a_requirement_names(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'coco')
    storage = AssetStorageSettings(root=settings.asset_root)
    for rule in (BOX, POLYGON, BOTH):
        result = scope(rule, pubs[0].manifest, storage)
        assert [o.object_key for o in result.eligible] == ['/annotations/0'] and result.unavailable is None
    assert scope(BOX, pubs[0].manifest, storage).eligible[0].geometry.box == 'supplied'
    mask = scope(MASK, pubs[0].manifest, storage)
    assert mask.unavailable.code == 'mask_not_supplied'


@requires_importer
def test_polygon_only_labelme_through_the_real_evidence_path(tmp_path):
    pubs, settings, _, _ = imported(tmp_path, 'labelme')
    result = scope(BOX, pubs[0].manifest, AssetStorageSettings(root=settings.asset_root))
    assert result.unavailable.code == 'supplied_box_not_available'
    assert result.ineligible[0].geometry.box == 'derived_from_polygon'
    assert result.image.image_key == pubs[0].manifest.image_key
    assert scope(POLYGON, pubs[0].manifest, AssetStorageSettings(root=settings.asset_root)).eligible


@requires_importer
def test_labelme_rectangles_and_polygons_in_one_sample_through_the_real_evidence_path(tmp_path):
    document = labelme_document(rectangle_shape([[1, 1], [8, 12]]), polygon_shape(WOMAN))
    pubs, settings, _, _ = imported(tmp_path, 'labelme', data=document)
    result = scope(BOX, pubs[0].manifest, AssetStorageSettings(root=settings.asset_root))
    assert [o.object_key for o in result.eligible] == ['/shapes/0']
    assert [o.object_key for o in result.ineligible] == ['/shapes/1']


@pytest.mark.parametrize('availability,code', [('missing', 'mask_referenced_unavailable'),
                                               ('unreadable', 'mask_referenced_unavailable'),
                                               ('available', 'mask_not_visible_to_visual_check')])
def test_penn_fudan_boxes_are_supplied_and_masks_are_distinguished(tmp_path, availability, code):
    sample = case(tmp_path)
    manifest = sample.manifest
    if availability == 'available':
        manifest = manifest.model_copy(update={'raw_mask': AssetManifestEntry(
            availability='available', storage_key='m/mask', content_type='image/png', size_bytes=1, sha256='0' * 64)})
    else:
        manifest = manifest.model_copy(update={'raw_mask': AssetManifestEntry(availability=availability)})
    storage = assets(tmp_path)
    assert [o.object_key for o in scope(BOX, manifest, storage).eligible] == ['penn_fudan:1']
    assert scope(POLYGON, manifest, storage).unavailable.code == 'no_eligible_annotation'
    assert scope(MASK, manifest, storage).unavailable.code == code


# --------------------------------------------------------------------------- extraction

def test_extraction_derives_applicability_for_visual_rules_from_the_requirement_only():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    tail = proposals[ANIMALS[5]]                     # "A fully visible animal's box must include its tail."
    assert tail.check_name == 'annotation_guideline' and tail.applicability.geometry == ['box']
    assert tail.applicability.source == 'derived-v1' and tail.applicability.status == 'clear'
    # The scope line names boxes only through context; the label rule is not made box-specific.
    assert proposals[ANIMALS[3]].applicability.geometry == []
    assert proposals[ANIMALS[6]].applicability.geometry == []
    # Rules that are not model-assisted visual checks carry no applicability.
    assert proposals[ANIMALS[4]].applicability is None


def test_geometry_words_that_appear_only_in_context_do_not_narrow_a_requirement():
    text = '1. Scope: this guideline covers bounding boxes, polygons and masks.\n2. Every label must name the visible animal.'
    [rule] = expand_response(respond({1: 'context', 2: 'requirement'}, [(2, VISUAL, {}, [])]), source_units(text))
    assert rule.context_sentences and 'bounding boxes' in rule.context_sentences[0]
    assert rule.applicability.geometry == []


def test_ambiguous_applicability_is_stored_and_called_out_in_the_notes_for_review():
    text = '1. The box includes the head and the polygon follows the person.\n2. Report unclear evidence as uncertain.'
    [rule] = expand_response(respond({1: 'requirement', 2: 'context'}, [(1, VISUAL, {}, [])]), source_units(text))
    assert rule.applicability.status == 'ambiguous'
    assert any(note.startswith('Geometry applicability is ambiguous') for note in rule.extraction_notes)


def test_an_unsupported_numeric_tolerance_stays_unsupported_with_no_applicability():
    proposals = {p.source_sentence: p for p in extract(ANIMAL_TEXT, ANIMAL_LINES, animal_rules())}
    tolerance = proposals[ANIMALS[7]]
    assert tolerance.check_name is None and tolerance.applicability is None
