export interface OperationMetadata {
  operation_id: string;
  method: string;
  path: string;
  implementation: 'implemented' | 'planned';
}

const operationIndex =
  typeof __PRISM_OPERATION_INDEX__ === 'undefined' ? [] : __PRISM_OPERATION_INDEX__;

export function matchOperation(
  method: string,
  pathname: string,
  operations: OperationMetadata[] = operationIndex,
): OperationMetadata | undefined {
  return operations.find((operation) => {
    if (operation.method.toUpperCase() !== method.toUpperCase()) return false;
    const pattern = operation.path
      .split('/')
      .map((part) => (part.startsWith('{') && part.endsWith('}') ? '[^/]+' : escapeRegex(part)))
      .join('/');
    return new RegExp(`^${pattern}/?$`).test(pathname.split('?')[0] ?? pathname);
  });
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export async function initTransport(): Promise<boolean> {
  if (typeof window === 'undefined' || !import.meta.env.DEV) return false;
  try {
    const { worker } = await import('../mocks/browser');
    await worker.start({
      onUnhandledRequest: 'bypass',
      serviceWorker: { url: '/mockServiceWorker.js' },
    });
    return true;
  } catch {
    return false;
  }
}
