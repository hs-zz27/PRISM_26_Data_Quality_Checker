import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuditReviewWorkspace } from './AuditReviewWorkspace';
import { useReviewerStore } from '../../store/reviewerStore';
import { makeFinding, makeRule, makeSampleAudit, makeSummary } from '../../test/fixtures';

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

    fireEvent.change(screen.getByLabelText('Reason / comment'), { target: { value: 'Vehicle label is correct for this crop' } });
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
