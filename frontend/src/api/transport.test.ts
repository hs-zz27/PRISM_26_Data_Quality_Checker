import { describe, expect, it } from 'vitest';
import { matchOperation } from './transport';
const operations = [
  { operation_id: 'getHealth', method: 'GET', path: '/api/v1/health', implementation: 'planned' as const },
  { operation_id: 'getDataset', method: 'GET', path: '/api/v1/datasets/{dataset_id}', implementation: 'implemented' as const },
];
describe('transport operation routing', () => {
  it('matches parameterized operations', () => {
    expect(matchOperation('GET', '/api/v1/datasets/ds1', operations)?.operation_id).toBe('getDataset');
  });
});
