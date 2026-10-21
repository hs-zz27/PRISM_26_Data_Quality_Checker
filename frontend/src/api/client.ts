import type { paths } from './generated/openapi';
import { recordTransport } from '../store/transportStore';

export const API_BASE = '/api/v1';

type HttpMethod = 'get' | 'post' | 'put';
type PathWithMethod<M extends HttpMethod> = {
  [P in keyof paths]: M extends keyof paths[P]
    ? paths[P][M] extends never | undefined
      ? never
      : P
    : never;
}[keyof paths];
type PathFor<M extends HttpMethod> = Extract<PathWithMethod<M>, string>;
type Operation<P extends keyof paths, M extends HttpMethod> = NonNullable<paths[P][M]>;
type PathParameters<P extends keyof paths, M extends HttpMethod> = Operation<P, M> extends {
  parameters: { path: infer V };
}
  ? V
  : never;
type QueryParameters<P extends keyof paths, M extends HttpMethod> = Operation<P, M> extends {
  parameters: { query?: infer V };
}
  ? Exclude<V, undefined>
  : never;
type RequestContent<P extends keyof paths, M extends HttpMethod> = Operation<P, M> extends {
  requestBody: { content: infer V };
}
  ? V
  : never;
type JsonRequest<P extends keyof paths, M extends HttpMethod> = RequestContent<P, M> extends {
  'application/json': infer V;
}
  ? V
  : never;
type MultipartRequest<P extends keyof paths, M extends HttpMethod> = RequestContent<P, M> extends {
  'multipart/form-data': infer V;
}
  ? V
  : never;
type JsonResponse<T> = T extends { content: { 'application/json': infer V } } ? V : never;
type SuccessfulJson<T> = T extends { responses: infer R }
  ? {
      [S in keyof R]: S extends 200 | 201 | 202 | '200' | '201' | '202' ? JsonResponse<R[S]> : never;
    }[keyof R]
  : never;
type ResponseFor<P extends keyof paths, M extends HttpMethod> = P extends keyof paths
  ? SuccessfulJson<NonNullable<paths[P][M]>>
  : never;
type PathArgs<P extends keyof paths, M extends HttpMethod> = [PathParameters<P, M>] extends [never]
  ? Record<never, never>
  : PathParameters<P, M>;
type QueryArgs<P extends keyof paths, M extends HttpMethod> = [QueryParameters<P, M>] extends [never]
  ? Record<never, never>
  : QueryParameters<P, M>;

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    public readonly status: number,
    message: string,
    public readonly requestId?: string,
    public readonly details?: Array<{ field: string; message: string }>,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: Array<{ field: string; message: string }>;
    request_id?: string;
  };
}

function interpolatePath<P extends keyof paths, M extends HttpMethod>(
  path: P,
  values: PathArgs<P, M>,
): string {
  let result = String(path);
  for (const [key, value] of Object.entries(values as Record<string, string | number>)) {
    result = result.replace(`{${key}}`, encodeURIComponent(String(value)));
  }
  if (/[{}]/.test(result)) throw new Error(`Missing path parameter for ${String(path)}`);
  return result;
}

function makeUrl<P extends keyof paths, M extends HttpMethod>(
  path: P,
  pathValues: PathArgs<P, M>,
  queryValues?: QueryArgs<P, M>,
): string {
  const pathname = interpolatePath<P, M>(path, pathValues);
  const url = new URL(`${API_BASE}${pathname}`, window.location.origin);
  for (const [key, value] of Object.entries(queryValues ?? {})) {
    if (value !== null && value !== undefined) url.searchParams.set(key, String(value));
  }
  return `${url.pathname}${url.search}`;
}

async function request<T>(
  operationPath: string,
  method: HttpMethod,
  url: string,
  body?: BodyInit,
): Promise<T> {
  const headers = new Headers();
  if (body && !(body instanceof FormData)) headers.set('Content-Type', 'application/json');

  let response: Response;
  try {
    response = await fetch(url, { method: method.toUpperCase(), headers, body });
  } catch (err) {
    throw new ApiError(
      'NETWORK_ERROR',
      0,
      `Network request failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  const operation = getOperationForPath(method, operationPath);
  if (operation) {
    recordTransport(operation.operation_id, response.headers.get('x-prism-transport') === 'mock' ? 'mock' : 'live');
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  let envelope: ErrorEnvelope | undefined;
  try {
    envelope = (await response.json()) as ErrorEnvelope;
  } catch {
    throw new ApiError('UNKNOWN_ERROR', response.status, `HTTP ${response.status}: ${response.statusText}`);
  }
  const error = envelope?.error;
  throw new ApiError(
    error?.code ?? 'UNKNOWN_ERROR',
    response.status,
    error?.message ?? `HTTP ${response.status}`,
    error?.request_id,
    error?.details,
  );
}

export function apiGet<const P extends PathFor<'get'>>(
  path: P,
  args: { path?: PathArgs<P, 'get'>; query?: QueryArgs<P, 'get'> } = {},
): Promise<ResponseFor<P, 'get'>> {
  const pathValues = (args.path ?? {}) as PathArgs<P, 'get'>;
  return request<ResponseFor<P, 'get'>>(
    String(path),
    'get',
    makeUrl(path, pathValues, (args.query ?? {}) as QueryArgs<P, 'get'>),
  );
}

type PagePath = PathFor<'get'>;

export async function apiGetAllPages<const P extends PagePath>(
  path: P,
  args: { path?: PathArgs<P, 'get'>; query?: QueryArgs<P, 'get'> } = {},
): Promise<Extract<ResponseFor<P, 'get'>, { items: unknown[]; next_offset: number | null }>> {
  const pageQuery = (offset: number) => ({ ...(args.query as Record<string, unknown>), limit: 100, offset }) as QueryArgs<P, 'get'>;
  const first = await apiGet(path, { ...args, query: pageQuery(0) });
  const page = first as unknown as { items: Array<unknown>; next_offset: number | null; offset: number; total: number; [key: string]: unknown };
  const items = [...page.items];
  let nextOffset = page.next_offset;
  while (nextOffset !== null) {
    const next = await apiGet(path, {
      ...args,
      query: pageQuery(nextOffset),
    });
    const nextPage = next as unknown as { items: Array<unknown>; next_offset: number | null; offset: number; total: number; [key: string]: unknown };
    items.push(...nextPage.items);
    nextOffset = nextPage.next_offset;
  }
  return { ...page, items, offset: 0, next_offset: null, total: page.total } as unknown as Extract<ResponseFor<P, 'get'>, { items: unknown[]; next_offset: number | null }>;
}

export function apiPost<const P extends PathFor<'post'>>(
  path: P,
  args: {
    path?: PathArgs<P, 'post'>;
    query?: QueryArgs<P, 'post'>;
    body?: JsonRequest<P, 'post'>;
  } = {},
): Promise<ResponseFor<P, 'post'>> {
  const pathValues = (args.path ?? {}) as PathArgs<P, 'post'>;
  const body = args.body === undefined ? undefined : JSON.stringify(args.body);
  return request<ResponseFor<P, 'post'>>(
    String(path),
    'post',
    makeUrl(path, pathValues, (args.query ?? {}) as QueryArgs<P, 'post'>),
    body,
  );
}

export function apiPut<const P extends PathFor<'put'>>(
  path: P,
  args: { path?: PathArgs<P, 'put'>; query?: QueryArgs<P, 'put'>; body: JsonRequest<P, 'put'> },
): Promise<ResponseFor<P, 'put'>> {
  const pathValues = (args.path ?? {}) as PathArgs<P, 'put'>;
  return request<ResponseFor<P, 'put'>>(
    String(path),
    'put',
    makeUrl(path, pathValues, (args.query ?? {}) as QueryArgs<P, 'put'>),
    JSON.stringify(args.body),
  );
}

type MultipartFiles<P extends PathFor<'post'>> = MultipartRequest<P, 'post'>;
type ImportForm = Omit<MultipartFiles<'/datasets'>, 'archive'> & { archive: File };
type MaskForm = Omit<MultipartFiles<'/datasets/{dataset_id}/samples/{sample_id}/mask-uploads'>, 'mask'> & { mask: File };

export function apiPostForm<P extends '/datasets' | '/datasets/{dataset_id}/samples/{sample_id}/mask-uploads'>(
  path: P,
  args: P extends '/datasets'
    ? { path?: PathArgs<P, 'post'>; body: ImportForm; onProgress?: (loaded: number, total: number) => void }
    : { path: PathArgs<P, 'post'>; body: MaskForm; onProgress?: (loaded: number, total: number) => void },
): Promise<ResponseFor<P, 'post'>> {
  const form = new FormData();
  for (const [key, value] of Object.entries(args.body)) {
    if (value instanceof Blob) form.append(key, value);
    else form.append(key, String(value));
  }
  const pathValues = (args.path ?? {}) as PathArgs<P, 'post'>;
  const url = makeUrl(path, pathValues);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (event) => args.onProgress?.(event.loaded, event.total);
    xhr.onload = () => {
      const operation = getOperationForPath('post', String(path));
      if (operation) recordTransport(operation.operation_id, xhr.getResponseHeader('x-prism-transport') === 'mock' ? 'mock' : 'live');
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as ResponseFor<P, 'post'>); } catch (error) { reject(error); }
      } else {
        try {
          const payload = JSON.parse(xhr.responseText) as ErrorEnvelope;
          reject(new ApiError(payload.error.code, xhr.status, payload.error.message, payload.error.request_id, payload.error.details));
        } catch { reject(new ApiError('UNKNOWN_ERROR', xhr.status, `HTTP ${xhr.status}`)); }
      }
    };
    xhr.onerror = () => reject(new ApiError('NETWORK_ERROR', 0, 'Network request failed'));
    xhr.send(form);
  });
}

export function buildApiUrl<P extends keyof paths>(path: P, params: Record<string, string | number>): string {
  return `${API_BASE}${interpolatePath(path, params as never)}`;
}

function getOperationForPath(method: HttpMethod, path: string) {
  return OPERATION_INDEX.find((operation) => operation.method.toLowerCase() === method && operation.path === `/api/v1${path}`);
}

export function isVersionConflict(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 409 && err.code === 'VERSION_CONFLICT';
}

export function isNotFound(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 404;
}

const OPERATION_INDEX = typeof __PRISM_OPERATION_INDEX__ === 'undefined' ? [] : __PRISM_OPERATION_INDEX__;
