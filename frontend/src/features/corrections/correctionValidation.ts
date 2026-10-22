import { isBboxValid, type BBox1Based, type ImageSize } from '../../lib/coords';

export interface EditableObject { object_id: number; label: string; bbox: BBox1Based }
export interface ObjectProblem { objectId: number | null; message: string }

/** Labels may not contain double quotes, control characters or Unicode line/paragraph separators. */
function hasForbiddenLabelCharacter(label: string): boolean {
  for (const char of label) {
    const code = char.codePointAt(0) ?? 0;
    if (char === '"' || code < 0x20 || code === 0x7f || code === 0x2028 || code === 0x2029) return true;
  }
  return false;
}

/** Client-side checks that mirror the correction contract, so problems show before saving. */
export function validateCorrectionObjects(objects: EditableObject[], imageSize: ImageSize | null): ObjectProblem[] {
  const problems: ObjectProblem[] = [];
  const seen = new Set<number>();
  for (const obj of objects) {
    if (!Number.isInteger(obj.object_id) || obj.object_id < 1 || obj.object_id > 65535) {
      problems.push({ objectId: obj.object_id, message: `Object ID ${obj.object_id} must be a whole number from 1 to 65535.` });
    } else if (seen.has(obj.object_id)) {
      problems.push({ objectId: obj.object_id, message: `Object ID ${obj.object_id} is used more than once.` });
    }
    seen.add(obj.object_id);
    if (!obj.label.trim()) problems.push({ objectId: obj.object_id, message: `Object #${obj.object_id} needs a label.` });
    else if (obj.label.length > 255 || hasForbiddenLabelCharacter(obj.label)) {
      problems.push({ objectId: obj.object_id, message: `Object #${obj.object_id} label has quotes, control characters or is too long.` });
    }
    if (imageSize && !isBboxValid(obj.bbox, imageSize)) {
      problems.push({ objectId: obj.object_id, message: `Object #${obj.object_id} box must be whole pixels with min ≤ max, inside the ${imageSize[0]}×${imageSize[1]} image.` });
    }
  }
  return problems;
}
