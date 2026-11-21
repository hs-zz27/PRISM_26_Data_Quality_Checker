import { describe, expect, it } from 'vitest';
import { personParts } from './personParts';

const shape = (object_id: number, label: string, group_id: number | null = null) => ({ object_id, label, group_id });

describe('personParts', () => {
  it('joins the parts of one person: same group ID and same label', () => {
    const parts = personParts([
      shape(1, 'person'), shape(2, 'person', 7), shape(3, 'person'), shape(4, 'person', 7), shape(5, 'person', 7),
    ]);
    expect(parts.get(2)).toEqual({ memberIds: [2, 4, 5], partNumber: 1, partCount: 3, leaderId: 2 });
    expect(parts.get(4)).toEqual({ memberIds: [2, 4, 5], partNumber: 2, partCount: 3, leaderId: 2 });
    expect(parts.get(5)).toEqual({ memberIds: [2, 4, 5], partNumber: 3, partCount: 3, leaderId: 2 });
    expect(parts.get(1)).toEqual({ memberIds: [1], partNumber: 1, partCount: 1, leaderId: 1 });
    expect(parts.get(3)).toEqual({ memberIds: [3], partNumber: 1, partCount: 1, leaderId: 3 });
  });

  it('keeps different labels apart even with the same group ID, as LabelMe does', () => {
    const parts = personParts([shape(1, 'person', 1), shape(2, 'bottle', 1), shape(3, 'person', 1)]);
    expect(parts.get(1)!.memberIds).toEqual([1, 3]);
    expect(parts.get(2)!.memberIds).toEqual([2]);
  });

  it('treats a missing or empty group ID as a single part, and keeps groups apart', () => {
    const parts = personParts([
      shape(1, 'person', null), { object_id: 2, label: 'person' }, shape(3, 'person', 0), shape(4, 'person', 0),
      shape(5, 'person', 1),
    ]);
    expect(parts.get(1)!.partCount).toBe(1);
    expect(parts.get(2)!.partCount).toBe(1);
    // 0 is a real LabelMe group ID, not "no group".
    expect(parts.get(3)!.memberIds).toEqual([3, 4]);
    expect(parts.get(5)!.memberIds).toEqual([5]);
  });

  it('works for formats with no group IDs and for an empty list', () => {
    const boxes = personParts([{ object_id: 1, label: 'person' }, { object_id: 2, label: 'person' }]);
    expect([...boxes.values()].every((entry) => entry.partCount === 1)).toBe(true);
    expect(personParts([]).size).toBe(0);
  });
});
