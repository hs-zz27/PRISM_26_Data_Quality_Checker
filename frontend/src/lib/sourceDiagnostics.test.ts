import { describe, expect, it } from 'vitest';
import { sourceDiagnostics } from './sourceDiagnostics';

const objects = [
  { object_id: 1, label: 'person', source_key: '/shapes/0' },
  { object_id: 2, label: 'person', source_key: '/shapes/1' },
  ...Array.from({ length: 9 }, (_, index) => ({ object_id: index + 3, label: 'sofa', source_key: `/shapes/${index + 2}` })),
];

describe('sourceDiagnostics', () => {
  it('names the annotation each problem belongs to by its stable key and display number', () => {
    const rows = sourceDiagnostics([
      { code: 'repeated_polygon_vertex', message: 'Polygon repeats a vertex other than its closing endpoint.', location: '/shapes/0/points', severity: 'error' },
      { code: 'self_intersecting_polygon', message: 'Non-adjacent polygon edges intersect or touch.', location: '/shapes/10/points', severity: 'error' },
    ], objects);
    expect(rows).toEqual([
      { key: '/shapes/0', displayNumber: 1, label: 'person', location: '/shapes/0/points', message: 'Polygon repeats a vertex other than its closing endpoint.', unsupported: false },
      // "/shapes/10" must not be matched to "/shapes/1".
      { key: '/shapes/10', displayNumber: 11, label: 'sofa', location: '/shapes/10/points', message: 'Non-adjacent polygon edges intersect or touch.', unsupported: false },
    ]);
  });

  it('keeps file-level problems and marks unsupported content', () => {
    const rows = sourceDiagnostics([
      { code: 'dimension_mismatch', message: 'Declared and decoded image dimensions disagree.', location: '', severity: 'error' },
      { code: 'unsupported_shape', message: 'Only explicit polygon and rectangle shapes are normalized.', location: '/shapes/1/shape_type', severity: 'unsupported' },
    ], objects);
    expect(rows[0]).toEqual({ key: null, displayNumber: null, label: null, location: '', message: 'Declared and decoded image dimensions disagree.', unsupported: false });
    expect(rows[1]).toMatchObject({ key: '/shapes/1', displayNumber: 2, unsupported: true });
  });

  it('returns nothing for missing, empty or malformed diagnostics, and never invents a message', () => {
    expect(sourceDiagnostics(undefined, objects)).toEqual([]);
    expect(sourceDiagnostics(null, objects)).toEqual([]);
    expect(sourceDiagnostics([], objects)).toEqual([]);
    expect(sourceDiagnostics('broken', objects)).toEqual([]);
    expect(sourceDiagnostics([null, 3, { code: 'x' }], objects)).toEqual([
      { key: null, displayNumber: null, label: null, location: '', message: 'x', unsupported: false },
    ]);
  });

  it('keeps the file location when the annotation list is not loaded, instead of blaming the whole file', () => {
    expect(sourceDiagnostics([{ code: 'c', message: 'Crosses itself.', location: '/shapes/4/points', severity: 'error' }], [])).toEqual([
      { key: null, displayNumber: null, label: null, location: '/shapes/4/points', message: 'Crosses itself.', unsupported: false },
    ]);
  });
});
