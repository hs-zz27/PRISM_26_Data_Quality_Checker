import { http, HttpResponse } from 'msw';

interface MockFixture { operation_id: string; method: string; path: string; request: unknown; response: { status: number; content_type: string; body: unknown } }

export function createMockHandlers(fixtures: MockFixture[]) {
  return fixtures.map((fixture) => {
    if (fixture.method.toUpperCase() !== 'GET') {
      throw new Error(`Planned mock operation is not supported: ${fixture.operation_id}`);
    }
    const route = fixture.path.replace(/\{([^}]+)\}/g, ':$1');
    return http.get(route, () =>
      HttpResponse.json(fixture.response.body as never, {
        status: fixture.response.status,
        headers: { 'x-prism-transport': 'mock' },
      }),
    );
  });
}

export const handlers = createMockHandlers(
  typeof __PRISM_MOCK_FIXTURES__ === 'undefined' ? [] : __PRISM_MOCK_FIXTURES__,
);
