import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiGet, apiGetAllPages, apiPut } from './client';
afterEach(() => vi.unstubAllGlobals());
describe('typed API client', () => {
  it('follows next_offset and combines page items', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ rulebook_id: 'a' }], limit: 1, offset: 0, total: 2, next_offset: 1 }))).mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ rulebook_id: 'b' }], limit: 1, offset: 1, total: 2, next_offset: null })));
    vi.stubGlobal('fetch', fetchMock);
    const page = await apiGetAllPages('/rulebooks', { query: { limit: 1, offset: 0 } });
    expect(page.items).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('follows export history next_offset pages', async () => {
    const page = (id: string, offset: number, next: number | null) => ({ items: [{ export_id: id, job_id: `job-${id}`, audit_id: 'audit-1', format: 'penn_fudan', selection: 'accepted_only', include_images: false, status: 'completed', included_count: 1, excluded_count: 0, download_url: null, manifest_url: null, created_at: '2026-10-21T00:00:00Z' }], limit: 1, offset, total: 2, next_offset: next });
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(page('exp-1', 0, 1)))).mockResolvedValueOnce(new Response(JSON.stringify(page('exp-2', 1, null))));
    vi.stubGlobal('fetch', fetchMock);
    const result = await apiGetAllPages('/exports', { query: { limit: 1, offset: 0 } });
    expect(result.items.map((item) => item.export_id)).toEqual(['exp-1', 'exp-2']);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('repeats array query parameters and omits empty ones', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ audits: [], rulebooks: [] })));
    vi.stubGlobal('fetch', fetchMock);
    await apiGet('/display-names', { query: { audit_id: ['audit_a', 'audit b'], rulebook_id: [] } });
    const url = new URL(String(fetchMock.mock.calls[0]?.[0]), 'http://localhost');
    expect(url.pathname).toBe('/api/v1/display-names');
    expect(url.searchParams.getAll('audit_id')).toEqual(['audit_a', 'audit b']);
    expect(url.searchParams.has('rulebook_id')).toBe(false);
  });
  it('preserves required blank comments in review payloads and exposes 409', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'VERSION_CONFLICT', message: 'stale' } }), { status: 409 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiPut('/findings/{finding_id}/review', { path: { finding_id: 'f1' }, body: { expected_review_version: 0, resolution: 'unsure', reviewer_id: 'r1', comment: '' } })).rejects.toMatchObject({ status: 409, code: 'VERSION_CONFLICT' });
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body)).comment).toBe('');
  });
});
