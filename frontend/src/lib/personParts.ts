/**
 * Which annotations are parts of the same person.
 *
 * When someone in front hides part of a person, the visible parts can be separate polygons. LabelMe joins
 * them with the same `group_id`; a shape is one instance per (label, group_id), so a group ID never joins
 * different labels. Formats without group IDs (COCO, which already keeps every part in one annotation, and
 * Penn-Fudan) give every annotation one part.
 *
 * This is display grouping only. Each part keeps its own object ID, stable key and display number, because
 * findings and corrections refer to the part.
 */
export interface PersonParts {
  /** Object IDs of every part of this person, in file order. */
  memberIds: number[];
  /** This part's position among them, from 1. */
  partNumber: number;
  partCount: number;
  /** The first part; the person's colour and list entry follow it. */
  leaderId: number;
}

interface GroupedObject { object_id: number; label: string; group_id?: number | null }

export function personParts(objects: readonly GroupedObject[]): Map<number, PersonParts> {
  const groups = new Map<string, number[]>();
  const keyOf = new Map<number, string>();
  for (const obj of objects) {
    const key = obj.group_id == null ? `single:${obj.object_id}` : `group:${obj.group_id}:${obj.label}`;
    keyOf.set(obj.object_id, key);
    groups.set(key, [...(groups.get(key) ?? []), obj.object_id]);
  }
  const result = new Map<number, PersonParts>();
  for (const obj of objects) {
    const memberIds = groups.get(keyOf.get(obj.object_id)!)!;
    result.set(obj.object_id, {
      memberIds, partNumber: memberIds.indexOf(obj.object_id) + 1, partCount: memberIds.length, leaderId: memberIds[0]!,
    });
  }
  return result;
}
