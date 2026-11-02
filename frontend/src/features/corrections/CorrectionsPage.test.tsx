import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { CorrectionsPage } from './CorrectionsPage';
import { polygonObjects } from './polygonFixtures';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { makeSampleAudit } from '../../test/fixtures';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const page = (items: unknown[]) => ({ items, limit: 100, offset: 0, total: items.length, next_offset: null });
const error = (code: string, status: number) => json({ error: { code, message: code, details: [], request_id: 'request-test' } }, status);
const sample = (revision = 1, objects = polygonObjects()) => ({
  dataset_id: 'dataset-1', sample_id: 'sample-1', revision, annotation_format: 'labelme',
  image_size: [100, 80], objects, integrity_status: 'valid', issues: [],
  assets: { image: { url: `/image?revision=${revision}`, revision, content_type: 'image/png' }, mask: null, mask_preview: null, annotation: null },
});
const completedJob = { job_id: 'job-recheck', kind: 'recheck', status: 'completed',
  progress: { phase: 'completed', processed: 1, total: 1, fraction: 1 },
  result: { kind: 'audit', id: 'audit-child', version: null, href: '/api/v1/audits/audit-child' }, error: null,
  created_at: '2026-11-02T10:00:00Z', updated_at: '2026-11-02T10:00:01Z' };

describe('manual polygon correction and recheck workflow', () => {
  let client: QueryClient;
  let current: ReturnType<typeof sample>;
  let posts: { path: string; body: Record<string, unknown> }[];
  let saveStatus: number;
  let failReload: boolean;
  let holdReload: (() => Promise<Response>) | null;

  beforeEach(() => {
    current = sample(); posts = []; saveStatus = 201; failReload = false; holdReload = null;
    useReviewerStore.setState({ reviewerId: 'Harkamal' });
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        posts.push({ path, body });
        if (path.endsWith('/revisions')) {
          if (saveStatus !== 201) return error('CORRECTION_CONFLICT', saveStatus);
          current = sample(2, body.objects);
          return json({ ...current, parent_revision: 1, reviewer_id: 'Harkamal', reason: body.reason,
            source_audit_id: body.source_audit_id, mask_changed: false, created_at: '2026-11-02T10:00:00Z' }, 201);
        }
        if (path.endsWith('/rechecks')) return json({ job: completedJob, audit: { audit_id: 'audit-child' } }, 202);
        return error('UNEXPECTED_MUTATION', 500);
      }
      if (path === '/api/v1/datasets') return json(page([{ dataset_id: 'dataset-1', name: 'Synthetic polygons' }]));
      if (path === '/api/v1/datasets/dataset-1/samples') return json(page([current, { ...sample(), sample_id: 'sample-2' }]));
      if (path === '/api/v1/datasets/dataset-1/samples/sample-1') {
        if (current.revision > 1 && holdReload) return holdReload();
        return current.revision > 1 && failReload ? error('DEPENDENCY_UNAVAILABLE', 503) : json(current);
      }
      if (path === '/api/v1/datasets/dataset-1/samples/sample-2') return json({ ...sample(), sample_id: 'sample-2' });
      if (path.endsWith('/revisions')) return json(page([]));
      if (path === '/api/v1/audits') return json(page([{ audit_id: 'audit-1', dataset_id: 'dataset-1', rulebook_id: 'book-1', rulebook_version: 3 }]));
      if (path.startsWith('/api/v1/audits/audit-1/samples/')) return json(makeSampleAudit([], {
        workspace_audit_id: 'audit-root', attempts: [],
      }));
      if (path === '/api/v1/jobs/job-recheck') return json(completedJob);
      return error('UNEXPECTED_READ', 404);
    }));
  });
  afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  function mount() {
    render(<QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/corrections?dataset=dataset-1&sample=sample-1&audit=audit-1']}><CorrectionsPage /></MemoryRouter>
    </QueryClientProvider>);
  }
  async function edit() {
    const selector = await screen.findByLabelText('Polygon object');
    fireEvent.change(selector, { target: { value: '/shapes/0' } });
    fireEvent.change(screen.getByLabelText('Selected point X'), { target: { value: '5.125' } });
    fireEvent.change(screen.getByLabelText('Reason (required)'), { target: { value: 'Included the visible boundary.' } });
  }
  function draft() { return JSON.parse((screen.getByLabelText('JSON annotation geometry') as HTMLTextAreaElement).value); }

  it('saves only the edited polygon then rechecks the saved revision and links back to the original audit', async () => {
    mount(); await edit();
    expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await screen.findByText('Saved as revision r2. You can now re-check it against the audit.');
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toMatchObject({ expected_revision: 1, reviewer_id: 'Harkamal', reason: 'Included the visible boundary.', source_audit_id: 'audit-1', mask_upload_id: null });
    const saved = posts[0]!.body.objects as ReturnType<typeof polygonObjects>;
    expect(saved[0]?.polygons?.[0]?.[0]).toEqual([5.125, 10]);
    expect(saved[0]?.bbox).toEqual([5.125, 10, 40, 60]);
    expect(saved[1]).toEqual(polygonObjects()[1]);
    expect(screen.getByRole('img', { name: 'sample-1' })).toHaveAttribute('src', '/image?revision=2');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Re-check this sample' }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toEqual({ sample_ids: ['sample-1'], sample_revisions: { 'sample-1': 2 },
      reviewer_id: 'Harkamal', feedback: null, propose_correction: false });
    expect(await screen.findByRole('link', { name: 'continue in the original review →' })).toHaveAttribute('href', '/audits/audit-root/review?sample=sample-1');
    // No review-acceptance, policy-activation or other writes occur.
    expect(posts.map((post) => post.path)).toEqual(['/api/v1/datasets/dataset-1/samples/sample-1/revisions', '/api/v1/audits/audit-1/rechecks']);
  });

  it('keeps the original revision and draft during a background update and save conflict', async () => {
    saveStatus = 409;
    mount(); await edit();
    act(() => { client.setQueryData(queryKeys.datasetSample('dataset-1', 'sample-1'), sample(2)); });
    // Query observers notify React asynchronously after the cache update.
    expect(await screen.findByText(/unsaved draft is still based on r1/)).toBeInTheDocument();
    expect(draft()[0].polygons[0][0]).toEqual([5.125, 10]);
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await screen.findByText(/Someone saved a newer revision/);
    expect(posts[0]?.body.expected_revision).toBe(1);
    expect(draft()[0].polygons[0][0]).toEqual([5.125, 10]);
    expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeDisabled();
  });

  it('does not repeat a successful save or recheck the old revision when its reload fails', async () => {
    failReload = true;
    mount(); await edit();
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await screen.findByText(/saved as r2, but it could not be loaded/);
    expect(screen.getByRole('button', { name: 'Save as new revision' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeDisabled();
    expect(screen.getByLabelText('Polygon object')).toBeDisabled();
    failReload = false;
    fireEvent.click(screen.getByRole('button', { name: 'Reload current revision' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeEnabled());
    expect(posts).toHaveLength(1);
    expect(screen.getByRole('img', { name: 'sample-1' })).toHaveAttribute('src', '/image?revision=2');
  });

  it('keeps editing and rechecking locked until the saved revision has loaded', async () => {
    let resolveReload!: (value: Response) => void;
    holdReload = () => new Promise<Response>((resolve) => { resolveReload = resolve; });
    mount(); await edit();
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await screen.findByText(/Loading the saved annotation/);
    expect(screen.getByLabelText('Polygon object')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeDisabled();
    await act(async () => { resolveReload(json(current)); });
    await screen.findByText('Saved as revision r2. You can now re-check it against the audit.');
  });

  it('requires a reason and preserves the draft when discard is declined', async () => {
    mount(); await edit();
    fireEvent.change(screen.getByLabelText('Reason (required)'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save as new revision' }));
    await screen.findByText(/Explain what you corrected/);
    expect(posts).toEqual([]);
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(screen.getByRole('button', { name: 'Reload current revision' }));
    expect(draft()[0].polygons[0][0]).toEqual([5.125, 10]);
    fireEvent.change(screen.getByLabelText('Sample'), { target: { value: 'sample-2' } });
    expect(screen.getByRole('img', { name: 'sample-1' })).toBeInTheDocument();
  });

  it('does not require a redundant save after undo restores the saved polygon', async () => {
    mount(); await edit();
    expect(screen.getByRole('button', { name: 'Save as new revision' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(draft()).toEqual(polygonObjects());
    expect(screen.getByRole('button', { name: 'Save as new revision' })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Re-check this sample' })).toBeEnabled());
    expect(posts).toEqual([]);
  });

  it('starts an isolated draft when the reviewer switches samples after confirming discard', async () => {
    mount(); await edit();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.change(screen.getByLabelText('Sample'), { target: { value: 'sample-2' } });
    await screen.findByRole('img', { name: 'sample-2' });
    expect(draft()).toEqual(polygonObjects());
    expect(screen.getByLabelText('Reason (required)')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Save as new revision' })).toBeDisabled();
  });
});
