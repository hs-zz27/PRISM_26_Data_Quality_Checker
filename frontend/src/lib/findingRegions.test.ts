import { describe, expect, it } from 'vitest';
import { findingRegions, regionPixelBox } from './findingRegions';

// Shapes copied from real audits (Audit 042 and Audit 046 of 8 Nov 2026).
const coverage = { detail: null, reason: null, status: 'completed', result: {
  problems: ['box: feet/bottom of the person is outside the annotation (29 px)'],
  geometries: [
    { geometry: 'box', match_iou: 0.825, person_box: [379, 137, 462, 420], person_score: 0.9977, tolerance_px: 11.3,
      coordinate_system: 'zero_based_continuous',
      regions: [{ box: [379, 390, 462, 420], edge: 420, kind: 'missing', side: 'bottom', pixels: 29 }] },
    { geometry: 'polygon', match_iou: 0.875, person_box: [379, 137, 462, 420], person_score: 0.9977, tolerance_px: 11.3,
      coordinate_system: 'zero_based_continuous',
      regions: [{ box: [400, 300, 440, 330], kind: 'extra', side: 'middle', area_px: 646 }] },
  ] } };
const unannotated = { result: { people_found: 2, annotations: 1, people_annotated: 1, coordinate_system: 'zero_based_continuous',
  unannotated: [{ box: [110, 84, 213, 337], height_px: 253, score: 0.9998, inside_annotations: 0 }] } };

describe('findingRegions', () => {
  it('reads box and outline regions of a person coverage finding', () => {
    expect(findingRegions('person_coverage', coverage)).toEqual([
      { kind: 'missing', geometry: 'box', side: 'bottom', bodyPart: null, box: [379, 390, 462, 420], sizePx: 29,
        areaPx: null, coordinateSystem: 'zero_based_continuous' },
      { kind: 'extra', geometry: 'polygon', side: 'middle', bodyPart: null, box: [400, 300, 440, 330], sizePx: null,
        areaPx: 646, coordinateSystem: 'zero_based_continuous' },
    ]);
  });

  it('reads a named body part and a shape mismatch when the check records them', () => {
    const named = { result: { geometries: [{ geometry: 'polygon', coordinate_system: 'zero_based_continuous', regions: [
      { box: [1, 2, 30, 40], kind: 'missing', side: 'top', area_px: 900, body_part: 'head' },
      { box: [5, 6, 70, 80], kind: 'mismatch', side: 'left', area_px: 120 }] }] } };
    expect(findingRegions('person_coverage', named).map((r) => [r.kind, r.bodyPart])).toEqual([['missing', 'head'], ['mismatch', null]]);
  });

  it('reads each person with no annotation', () => {
    expect(findingRegions('unannotated_people', unannotated)).toEqual([
      { kind: 'unannotated', geometry: null, side: null, bodyPart: null, box: [110, 84, 213, 337], sizePx: 253,
        areaPx: null, coordinateSystem: 'zero_based_continuous' },
    ]);
  });

  it('skips malformed records and findings without regions', () => {
    const broken = { result: { geometries: [{ geometry: 'box', regions: [
      { kind: 'missing', side: 'top', box: [1, 2, 3] }, { kind: 'sideways', box: [1, 2, 3, 4] }, 'text', null] },
      { geometry: 'polygon', reason: 'no_matching_person' }] } };
    expect(findingRegions('person_coverage', broken)).toEqual([]);
    expect(findingRegions('person_coverage', { result: null })).toEqual([]);
    expect(findingRegions('person_coverage', null)).toEqual([]);
    expect(findingRegions('polygon_validity', coverage)).toEqual([]);
  });
});

describe('regionPixelBox', () => {
  it('keeps continuous boxes and shifts one-based inclusive boxes to pixels from 0', () => {
    const region = findingRegions('person_coverage', coverage)[0]!;
    expect(regionPixelBox(region)).toEqual([379, 390, 462, 420]);
    expect(regionPixelBox({ ...region, box: [10, 20, 30, 40], coordinateSystem: 'one_based_inclusive' })).toEqual([9, 19, 30, 40]);
  });
});
