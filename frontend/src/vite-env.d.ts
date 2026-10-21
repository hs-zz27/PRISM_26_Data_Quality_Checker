/// <reference types="vite/client" />
interface PrismOperationMetadata {
  operation_id: string;
  method: string;
  path: string;
  implementation: 'implemented' | 'planned';
}

interface PrismMockFixture extends Omit<PrismOperationMetadata, 'implementation'> {
  request: {
    path_parameters?: Record<string, string | number>;
    query?: Record<string, string | number | null>;
    body?: unknown;
  };
  response: {
    status: number;
    content_type: string;
    body: unknown;
  };
}

declare const __PRISM_OPERATION_INDEX__: PrismOperationMetadata[];
declare const __PRISM_MOCK_FIXTURES__: PrismMockFixture[];
