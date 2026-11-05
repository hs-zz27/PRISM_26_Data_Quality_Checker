import { describe, expect, it } from 'vitest';
import { imagePoint, insertVertex, moveVertex, removeVertex, selfCrossings, vertices } from './polygonEditing';
import { polygonObjects } from './polygonFixtures';

const selected = { key: '/shapes/0', part: 0, point: 0 };

describe('native polygon edits', () => {
  it('maps scaled and scrolled canvas coordinates without integer rounding', () => {
    expect(imagePoint([140.5, 65], { left: 100, top: -15, width: 200, height: 160 }, [100, 80])).toEqual([20.25, 40]);
    expect(imagePoint([-200, 500], { left: 100, top: -15, width: 200, height: 160 }, [100, 80])).toEqual([0, 80]);
    expect(imagePoint([0, 0], { left: 0, top: 0, width: 0, height: 10 }, [100, 80])).toBeNull();
  });

  it('changes one vertex and its derived bounds while preserving every other object and identity', () => {
    const original = polygonObjects(); const before = structuredClone(original);
    const changed = moveVertex(original, selected, [5.125, 8.5], [100, 80]);
    expect(original).toEqual(before);
    expect(changed[0]?.polygons?.[0]?.[0]).toEqual([5.125, 8.5]);
    expect(changed[0]?.bbox).toEqual([5.125, 8.5, 40, 60]);
    expect(changed[0]).toMatchObject({ source_key: '/shapes/0', object_id: 1, source_id: null, group_id: 42, label: 'person' });
    expect(changed[1]).toBe(original[1]);
    expect(changed[0]?.polygons?.[0]?.slice(1)).toEqual(original[0]?.polygons?.[0]?.slice(1));
  });

  it('keeps a COCO supplied box independent of its edited polygon', () => {
    const original = polygonObjects();
    original[0] = { ...original[0]!, source_key: '/annotations/0', source_id: 90, bbox_origin: 'supplied', bbox: [0, 0, 95, 75] };
    const changed = moveVertex(original, { ...selected, key: '/annotations/0' }, [3.5, 9], [100, 80]);
    expect(changed[0]?.bbox).toEqual([0, 0, 95, 75]);
    expect(changed[0]?.source_id).toBe(90);
  });

  it('recomputes bounds across all parts without changing the untouched part', () => {
    const original = polygonObjects();
    original[0]!.polygons!.push([[70, 1], [80, 1], [75, 5]]);
    const changed = moveVertex(original, selected, [5, 8], [100, 80]);
    expect(changed[0]?.bbox).toEqual([5, 1, 80, 60]);
    expect(changed[0]?.polygons?.[1]).toBe(original[0]?.polygons?.[1]);
  });

  it('preserves an explicitly closed ring when moving, inserting and removing points', () => {
    const original = polygonObjects();
    original[0]!.polygons![0]!.push([10.25, 10]);
    const moved = moveVertex(original, selected, [5, 8], [100, 80]);
    expect(moved[0]?.polygons?.[0]?.at(-1)).toEqual([5, 8]);
    const inserted = insertVertex(moved, { ...selected, point: 3 });
    expect(vertices(inserted[0]!.polygons![0]!)).toHaveLength(5);
    expect(inserted[0]?.polygons?.[0]?.[4]).toEqual([7.625, 34]);
    const removed = removeVertex(inserted, { ...selected, point: 4 });
    expect(removed).toEqual(moved);
  });

  it('enforces finite bounded coordinates and a minimum of three distinct vertices', () => {
    const original = polygonObjects();
    const changed = moveVertex(original, selected, [-2, 200], [100, 80]);
    expect(changed[0]?.polygons?.[0]?.[0]).toEqual([0, 80]);
    expect(() => moveVertex(original, selected, [NaN, 2], [100, 80])).toThrow('finite');
    const triangle = removeVertex(original, selected);
    expect(() => removeVertex(triangle, selected)).toThrow('three different points');
    expect(() => moveVertex(original, { ...selected, key: 'unknown' }, [1, 2], [100, 80])).toThrow('existing polygon');
  });
});

describe('selfCrossings', () => {
  it('finds no crossing on a simple outline', () => {
    expect(selfCrossings([[0, 0], [10, 0], [10, 10], [0, 10]])).toEqual([]);
  });

  it('locates the tiny crossing from two swapped neighbouring points (PennPed00052, person #2 foot)', () => {
    const found = selfCrossings([[238, 466], [239, 475], [242, 486], [261, 489], [249, 490], [263, 487], [260, 479], [251, 465]]);
    expect(found).toHaveLength(1);
    expect(found[0]![0]).toBeGreaterThan(249);
    expect(found[0]![0]).toBeLessThan(263);
    expect(found[0]![1]).toBeGreaterThan(486);
    expect(found[0]![1]).toBeLessThan(490);
  });

  it('ignores a closing endpoint that repeats the first point', () => {
    expect(selfCrossings([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])).toEqual([]);
  });
});
