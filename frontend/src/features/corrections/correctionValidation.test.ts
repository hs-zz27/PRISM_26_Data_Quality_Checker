import { describe, expect, it } from 'vitest';
import { validateCorrectionObjects } from './correctionValidation';

describe('correction object validation', () => {
  it('accepts valid objects', () => {
    expect(validateCorrectionObjects([{ object_id: 1, label: 'person', bbox: [1, 1, 10, 10] }], [100, 100])).toEqual([]);
  });

  it('reports duplicate IDs, blank or unsafe labels and boxes outside the image', () => {
    const problems = validateCorrectionObjects([
      { object_id: 1, label: 'person', bbox: [1, 1, 10, 10] },
      { object_id: 1, label: ' ', bbox: [1, 1, 10, 10] },
      { object_id: 3, label: 'say "hi"', bbox: [5, 5, 200, 10] },
      { object_id: 0, label: 'person', bbox: [1, 1, 2, 2] },
    ], [100, 100]);
    expect(problems.map((p) => p.message)).toEqual([
      'Object ID 1 is used more than once.',
      'Object #1 needs a label.',
      'Object #3 label has quotes, control characters or is too long.',
      'Object #3 box must be whole pixels with min ≤ max, inside the 100×100 image.',
      'Object ID 0 must be a whole number from 1 to 65535.',
    ]);
  });
});
