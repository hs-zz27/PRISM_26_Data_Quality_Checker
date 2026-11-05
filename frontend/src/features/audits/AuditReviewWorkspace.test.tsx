import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuditReviewWorkspace } from './AuditReviewWorkspace';
import { useReviewerStore } from '../../store/reviewerStore';
import { useCanvasStore } from '../../store/canvasStore';
import { makeAuditName, makeFinding, makeRule, makeSampleAudit, makeSummary } from '../../test/fixtures';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const finding = makeFinding();
const passFinding = makeFinding({ finding_id: 'finding-2', rule_id: 'BOX-1', description: 'Boxes stay in the image.', check_name: 'box_validity', outcome: 'pass', reason: null, parameters: {}, source_sentence: null, origin: 'reviewer' });
const sample = makeSampleAudit([finding, passFinding]);

function routeFetch(url: string, init?: RequestInit): Response {
  const { pathname } = new URL(url, 'http://localhost');
  const method = init?.method ?? 'GET';
  if (method === 'PUT' && pathname === '/api/v1/findings/finding-1/review') return json({ ...finding.review, revision: 1 });
  if (pathname === '/api/v1/audits/audit-1') return json({ audit_id: 'audit-1', job_id: 'job-1', dataset_id: 'dataset-1', rulebook_id: 'book-1', rulebook_version: 2, source_mode: 'snapshot', status: 'completed', sample_count: 1, processed_count: 1, failed_count: 0, incomplete_count: 0, created_at: '2026-10-20T10:00:00Z', updated_at: '2026-10-20T10:00:00Z' });
  if (pathname === '/api/v1/rulebooks/book-1') return json({ rulebook_id: 'book-1', version: 2, guideline_text: 'Allowed labels are person. Masks are one piece.', confirmation_state: 'confirmed', rules: [makeRule(), makeRule({ rule_id: 'MASK-1', description: 'Masks are one piece.', check_name: 'mask_components', source_sentence: 'Masks are one piece.' })] });
  if (pathname === '/api/v1/audits/audit-1/samples') return json({ items: [makeSummary()], limit: 100, offset: 0, total: 1, next_offset: null });
  if (pathname === '/api/v1/audits/audit-1/samples/sample-1') return json(sample);
  if (pathname === '/api/v1/datasets/dataset-1/samples/sample-1') return json({ sample_id: 'sample-1', dataset_id: 'dataset-1', revision: 1, image_size: [100, 80], integrity_status: 'valid', objects: [{ object_id: 1, label: 'vehicle', bbox: [10, 10, 40, 40] }], issues: [] });
  if (pathname.endsWith('/reviews')) return json({ items: [], limit: 100, offset: 0, total: 0, next_offset: null });
  return json({ error: { code: 'NOT_FOUND', message: `Unhandled ${method} ${pathname}` } }, 404);
}

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/audits/audit-1/review']}>
        <Routes><Route path="/audits/:auditId/review" element={<AuditReviewWorkspace />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AuditReviewWorkspace', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    useReviewerStore.setState({ reviewerId: 'rev_test' });
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => routeFetch(url, init));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows rule, result, evidence and the guideline citation for each finding', async () => {
    renderWorkspace();
    const card = await screen.findByRole('article', { name: 'Rule LBL-1: Fail' });
    expect(within(card).getByText('Label is not in the approved list')).toBeInTheDocument();
    expect(within(card).getByText('Label must be one of: person')).toBeInTheDocument();
    expect(within(card).getByText('vehicle')).toBeInTheDocument();
    const recorded = within(card).getByText('Complete recorded evidence').closest('details');
    expect(recorded?.querySelector('pre')?.textContent).toBe(JSON.stringify(finding.evidence?.measurements, null, 2));
    expect(within(card).getByText(/Allowed labels are person\./)).toBeInTheDocument();
    // Reviewer-authored rules say so instead of showing a citation.
    expect(await screen.findByText(/Reviewer-authored rule — no guideline sentence/)).toBeInTheDocument();
    // Confirmed rules without a finding are still listed for the reviewer.
    expect(screen.getByText(/Rules with no result for this sample/)).toBeInTheDocument();
    expect(screen.getByLabelText('Review progress')).toHaveTextContent('0 of 1 reviewed');
  });

  it('blocks an override until a reason is typed, then sends it with the decision', async () => {
    renderWorkspace();
    const card = await screen.findByRole('article', { name: 'Rule LBL-1: Fail' });
    fireEvent.click(within(card).getByRole('button', { name: 'Override' }));

    expect(await screen.findByText(/Type a reason in the comment box before you override the tool/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(false);

    fireEvent.change(screen.getByLabelText('Feedback for this image'), { target: { value: 'Vehicle label is correct for this crop' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Override' }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === 'PUT')!;
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({ resolution: 'rejected', reviewer_id: 'rev_test', comment: 'Vehicle label is correct for this crop', expected_review_version: 0 });
  });

  it('lets the reviewer agree with a finding without a reason', async () => {
    renderWorkspace();
    const card = await screen.findByRole('article', { name: 'Rule LBL-1: Fail' });
    fireEvent.click(within(card).getByRole('button', { name: 'Agree' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === 'PUT')!;
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({ resolution: 'accepted', comment: '' });
  });

  it('requires a reason before rejecting the sample', async () => {
    renderWorkspace();
    await screen.findByRole('article', { name: 'Rule LBL-1: Fail' });
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    expect(await screen.findByText(/before you reject this sample/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')).toBe(false);
  });
});

const attempts = [
  { audit_id: 'child-1', job_id: 'child-job', sample_revision: 1, processing_status: 'completed' as const, created_at: '2026-10-25T10:00:00Z', reviewer_id: 'rev_test' },
  { audit_id: 'audit-1', job_id: 'job-1', sample_revision: 1, processing_status: 'completed' as const, created_at: '2026-10-24T10:00:00Z', reviewer_id: null },
];
function childDetail() {
  return makeSampleAudit([makeFinding({ audit_id: 'child-1', finding_id: 'child-finding' })], {
    audit_id: 'child-1', workspace_audit_id: 'audit-1', attempts,
    summary: makeSummary({ result_audit_id: 'child-1' }),
    review: { ...sample.review, audit_id: 'child-1' },
  });
}

describe('rechecks inside the original workspace', () => {
  beforeEach(() => useReviewerStore.setState({ reviewerId: 'rev_test' }));
  afterEach(() => vi.unstubAllGlobals());

  it('submits feedback for the selected image without saving a correction', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST' && url.includes('/rechecks')) {
        return json({ audit: { audit_id: 'child-1' }, job: { job_id: 'child-job' }, parent_audit_id: 'audit-1' }, 202);
      }
      if (url.includes('/jobs/child-job')) return json({ job_id: 'child-job', kind: 'audit', status: 'queued', progress: { phase: 'queued', processed: 0, total: 1, fraction: 0 } });
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    await screen.findByRole('article', { name: 'Rule LBL-1: Fail' });
    // Canvas pan shortcuts must not swallow spaces typed in reviewer feedback.
    expect(fireEvent.keyDown(screen.getByLabelText('Feedback for this image'), { key: ' ', code: 'Space' })).toBe(true);
    fireEvent.change(screen.getByLabelText('Feedback for this image'), { target: { value: 'The box misses the feet' } });
    const suggest = screen.getByRole('button', { name: 'Suggest a correction' });
    await waitFor(() => expect(suggest).toBeEnabled());
    fireEvent.click(suggest);
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0]![0]).toContain('/audits/audit-1/rechecks');
    expect(JSON.parse(String(posts[0]![1]?.body))).toEqual({ sample_ids: ['sample-1'], sample_revisions: { 'sample-1': 1 },
      reviewer_id: 'rev_test', feedback: 'The box misses the feet', propose_correction: true });
  });

  it.each(['review-correction-v1', 'review-correction-v2'])('requires preview and explicit apply before saving a draft then rechecks the saved revision (%s)', async (protocol) => {
    const objects = [{ object_id: 1, label: 'vehicle', bbox: [10, 10, 40, 50] }];
    const draft = { ...sample, measurements: { ...sample.measurements, correction_proposal: {
      protocol, annotation_format: 'penn_fudan', dataset_id: 'dataset-1', sample_id: 'sample-1',
      sample_revision: 1, status: 'proposed', reason: 'Extend the lower edge.', feedback: { comment: 'Check feet' }, objects,
    } } };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (init?.method === 'POST' && path.endsWith('/revisions')) return json({ dataset_id: 'dataset-1', sample_id: 'sample-1', revision: 2 });
      if (init?.method === 'POST' && path.endsWith('/rechecks')) return json({ audit: { audit_id: 'child-1' }, job: { job_id: 'child-job' }, parent_audit_id: 'audit-1' }, 202);
      if (path === '/api/v1/jobs/child-job') return json({ job_id: 'child-job', kind: 'audit', status: 'queued', progress: { phase: 'queued', processed: 0, total: 1, fraction: 0 } });
      if (path === '/api/v1/audits/audit-1/samples/sample-1') return json(draft);
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    const apply = await screen.findByRole('button', { name: 'Apply correction and recheck' });
    expect(apply).toBeDisabled();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Preview correction' }));
    expect(screen.getByText(/Nothing has been saved/)).toBeInTheDocument();
    await waitFor(() => expect(apply).toBeEnabled());
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    fireEvent.click(apply);
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2));
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts[0]![0]).toContain('/revisions');
    expect(JSON.parse(String(posts[0]![1]?.body))).toMatchObject({ expected_revision: 1, objects, reason: 'Check feet', source_audit_id: 'audit-1' });
    expect(posts[1]![0]).toContain('/rechecks');
    expect(JSON.parse(String(posts[1]![1]?.body))).toMatchObject({ sample_ids: ['sample-1'], sample_revisions: { 'sample-1': 2 }, feedback: null, propose_correction: false });
  });

  it('submits a recheck without navigating away and displays its pending state', async () => {
    let submitted = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (init?.method === 'POST' && path === '/api/v1/audits/audit-1/rechecks') {
        submitted = true;
        return json({ audit: { audit_id: 'child-1' }, job: { job_id: 'child-job' }, parent_audit_id: 'audit-1' }, 202);
      }
      if (path === '/api/v1/jobs/child-job') return json({ job_id: 'child-job', kind: 'audit', status: 'queued', progress: { phase: 'queued', processed: 0, total: 1, fraction: 0 } });
      if (submitted && path === '/api/v1/audits/audit-1/samples/sample-1') {
        const child = childDetail();
        return json({ ...child, findings: [], summary: { ...child.summary, processing_status: 'queued', tool_status: 'pending' } });
      }
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    const recheck = await screen.findByRole('button', { name: 'Re-check this image' });
    await waitFor(() => expect(recheck).toBeEnabled());
    fireEvent.click(recheck);
    expect(await screen.findByText(/Checking this image\. Its new results will appear here/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-check this image' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Accept$/ })).toBeDisabled();
    expect(screen.getByText('audit-1')).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ sample_ids: ['sample-1'], sample_revisions: { 'sample-1': 1 }, reviewer_id: 'rev_test', feedback: null, propose_correction: false });
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/samples/sample-1?include_rechecks=true'))).toBe(true);
  });

  it('saves decisions to the displayed attempt, preserving original audit evidence', async () => {
    const child = childDetail();
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (path === '/api/v1/audits/audit-1/samples/sample-1') return json(child);
      if (init?.method === 'PUT') return json({ ...child.review, decision: 'accepted', revision: 1 });
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    const accept = await screen.findByRole('button', { name: /^Accept$/ });
    await waitFor(() => expect(accept).toBeEnabled());
    fireEvent.click(accept);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => init?.method === 'PUT' && url.includes('/audits/child-1/samples/sample-1/review'))).toBe(true));
    expect(fetchMock.mock.calls.some(([url, init]) => init?.method === 'PUT' && url.includes('/audits/audit-1/'))).toBe(false);
  });

  it('shows previous evidence read-only and blocks both buttons and review shortcuts', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const parsed = new URL(url, 'http://localhost');
      if (parsed.pathname === '/api/v1/audits/audit-1/samples/sample-1' && parsed.searchParams.get('include_rechecks') === 'true') return json(childDetail());
      if (parsed.pathname === '/api/v1/display-names') {
        // Attempts are labelled by one batched lookup of their internal IDs.
        expect(parsed.searchParams.getAll('audit_id')).toEqual(['audit-1', 'child-1']);
        return json({ audits: [makeAuditName(), makeAuditName({ audit_id: 'child-1', recheck_number: 1, label: 'Audit 014 R01', title: 'Feedback recheck' })], rulebooks: [] });
      }
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();
    const history = await screen.findByLabelText('Attempt history');
    expect(await within(history).findByRole('option', { name: /^Audit 014 · / })).toHaveValue('audit-1');
    expect(within(history).getByRole('option', { name: 'Latest attempt · Audit 014 R01' })).toHaveValue('');
    fireEvent.change(history, { target: { value: 'audit-1' } });
    expect(await screen.findByText(/Previous attempt \(Audit 014\) — read only/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Accept$/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Re-check this image' })).toBeDisabled();
    fireEvent.keyDown(window, { key: 'A', shiftKey: true });
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);
    fireEvent.change(screen.getByLabelText('Attempt history'), { target: { value: '' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /^Accept$/ })).toBeEnabled());
  });

  it('distinguishes referenced mask file missing from optional mask never supplied in diagnostics', async () => {
    const pennFudanDetail = {
      ...sample,
      annotation_format: 'penn_fudan' as const,
      assets: { image: { url: '/image.png' }, mask: null, mask_preview: null },
      failures: [{ failure_id: 'fail-1', stage: 'extraction', reason: 'raw_mask_missing', object_id: null, check_name: 'mask_exists', detail: 'Missing mask file' }],
    };
    const pennFudanRaw = {
      sample_id: 'sample-1', dataset_id: 'dataset-1', revision: 1, image_size: [100, 80], integrity_status: 'valid',
      objects: [
        { object_id: 1, label: 'vehicle', bbox: [10, 10, 40, 40] },
        { object_id: 2, label: 'ghost', bbox: null, polygons: [] },
      ],
      issues: ['mask_file_missing: PedMasks/sample-1.png'],
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (path === '/api/v1/audits/audit-1/samples/sample-1') return json(pennFudanDetail);
      if (path === '/api/v1/datasets/dataset-1/samples/sample-1') return json(pennFudanRaw);
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();

    expect(await screen.findByText(/Referenced mask file is missing/i)).toBeInTheDocument();
    expect(screen.getByText(/ghost: No bounding box or polygon coordinates supplied/i)).toBeInTheDocument();
    expect(screen.getAllByText('#2', { selector: '.object-list-swatch' }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/1-based inclusive/i).length).toBeGreaterThanOrEqual(1);
  });

  it('reports optional mask never supplied for datasets without masks', async () => {
    const cocoDetail = {
      ...sample,
      annotation_format: 'coco' as const,
      assets: { image: { url: '/image.png' }, mask: null, mask_preview: null },
      failures: [],
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (path === '/api/v1/audits/audit-1/samples/sample-1') return json(cocoDetail);
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();

    expect(await screen.findByText(/Optional mask never supplied/i)).toBeInTheDocument();
  });

  it('isolates proposal state and resets preview when viewing historical attempts', async () => {
    const objects = [{ object_id: 1, label: 'vehicle', bbox: [10, 10, 40, 50] }];
    const child = childDetail();
    const draft = {
      ...child,
      measurements: {
        ...child.measurements,
        correction_proposal: {
          protocol: 'review-correction-v2', annotation_format: 'penn_fudan', dataset_id: 'dataset-1', sample_id: 'sample-1',
          sample_revision: 1, status: 'proposed', reason: 'Extend edge', feedback: { comment: 'Check feet' }, objects,
        },
      },
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      if (path === '/api/v1/audits/audit-1/samples/sample-1') return json(draft);
      return routeFetch(url, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWorkspace();

    const previewBtn = await screen.findByRole('button', { name: 'Preview correction' });
    fireEvent.click(previewBtn);
    expect(screen.getByText(/Proposed correction \(preview, not saved\)/i)).toBeInTheDocument();

    // Switch to older attempt
    fireEvent.change(screen.getByLabelText('Attempt history'), { target: { value: 'audit-1' } });

    // Preview state should reset and proposal should not be shown on the older attempt
    await waitFor(() => {
      expect(screen.queryByText(/Proposed correction \(preview, not saved\)/i)).not.toBeInTheDocument();
    });
  });

  it('suppresses review and canvas keyboard shortcuts while typing in the feedback textarea', async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => routeFetch(url, init));
    vi.stubGlobal('fetch', fetchMock);
    useCanvasStore.setState({ showBoxes: true, showPolygons: true, showLabels: true, showMask: true });
    renderWorkspace();
    await screen.findByRole('article', { name: 'Rule LBL-1: Fail' });

    const feedbackInput = screen.getByLabelText('Feedback for this image');
    feedbackInput.focus();

    // Trigger shortcut keys while focused on feedback box
    fireEvent.keyDown(feedbackInput, { key: 'b' });
    fireEvent.keyDown(feedbackInput, { key: 'p' });
    fireEvent.keyDown(feedbackInput, { key: 'l' });
    fireEvent.keyDown(feedbackInput, { key: 'm' });
    fireEvent.keyDown(feedbackInput, { key: 'a' });
    fireEvent.keyDown(feedbackInput, { key: 'r' });

    // Canvas visibility toggles must remain unchanged
    expect(useCanvasStore.getState().showBoxes).toBe(true);
    expect(useCanvasStore.getState().showPolygons).toBe(true);
    expect(useCanvasStore.getState().showLabels).toBe(true);
    expect(useCanvasStore.getState().showMask).toBe(true);
  });
});

