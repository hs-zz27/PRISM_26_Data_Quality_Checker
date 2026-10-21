import { describe, expect, it } from 'vitest';
import { isMockOperation, matchOperation } from './transport';
const operations = [
  { operation_id: 'getHealth', method: 'GET', path: '/api/v1/health', implementation: 'planned' as const },
  { operation_id: 'getDataset', method: 'GET', path: '/api/v1/datasets/{dataset_id}', implementation: 'implemented' as const },
];
describe('transport operation routing', () => {
  it('matches parameterized operations and mocks only planned routes', () => {
    expect(matchOperation('GET', '/api/v1/datasets/ds1', operations)?.operation_id).toBe('getDataset');
    expect(isMockOperation('GET', '/api/v1/health', operations)).toBe(true);
    expect(isMockOperation('GET', '/api/v1/datasets/ds1', operations)).toBe(false);
  });
});
