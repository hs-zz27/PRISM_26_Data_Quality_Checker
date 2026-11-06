import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { TitleEditor } from './TitleEditor';
import { AuditNameText, RulebookNameText, TechnicalId } from '../../components/ui/RecordName';
import { useDisplayNames } from '../../hooks/useDisplayNames';
import { AuditsPage } from '../audits/AuditsPage';
import { CommandPalette } from '../../components/layout/CommandPalette';
import { Navbar } from '../../components/layout/Navbar';
import { useReviewerStore } from '../../store/reviewerStore';
import { makeAuditName, makeRulebookName } from '../../test/fixtures';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const page = (items: unknown[]) => ({ items, limit: 100, offset: 0, total: items.length, next_offset: null });

function wrap(children: ReactNode, path = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{children}</MemoryRouter></QueryClientProvider>);
}

function CurrentRoute() {
  return <output data-testid="route">{useLocation().pathname}</output>;
}

const audit = {
  audit_id: 'audit_0e941d0e56f3403391108d10216d5f57', job_id: 'job-1', dataset_id: 'dataset-1',
  rulebook_id: 'rulebook_7385861ed52d4aeda8e8f7147b4e7b40', rulebook_version: 3, source_mode: 'snapshot',
  status: 'completed', sample_count: 1, processed_count: 1, failed_count: 0, incomplete_count: 0,
  created_at: '2026-11-02T10:30:00Z', updated_at: '2026-11-02T10:30:00Z',
  name: makeAuditName({ audit_id: 'audit_0e941d0e56f3403391108d10216d5f57', root_audit_id: 'audit_0e941d0e56f3403391108d10216d5f57' }),
  rulebook_name: makeRulebookName({ rulebook_id: 'rulebook_7385861ed52d4aeda8e8f7147b4e7b40' }),
};
const rulebook = {
  rulebook_id: audit.rulebook_id, version: 3, guideline_text: 'Person boxes must be tight.', confirmation_state: 'confirmed',
  rules: [], name: makeRulebookName({ rulebook_id: audit.rulebook_id }),
};
const dataset = { dataset_id: 'dataset-1', name: 'LabelMe demo set', format: 'labelme', status: 'ready', sample_count: 1, object_count: 1, import_job_id: 'j', created_at: '2026-11-01T00:00:00Z', issues: [] };

afterEach(() => vi.unstubAllGlobals());
beforeEach(() => useReviewerStore.setState({ reviewerId: 'rev_test' }));

describe('name rendering', () => {
  it('renders user titles as text, keeps long titles usable and offers the technical ID separately', () => {
    const long = 'A very long descriptive title that keeps going well beyond the width of a narrow column on a small screen';
    wrap(<>
      <AuditNameText name={makeAuditName({ title: '<img src=x onerror=alert(1)> & co' })} />
      <RulebookNameText name={makeRulebookName({ title: long })} version={3} />
      <TechnicalId label="Audit ID" value="audit_0e941d0e" />
    </>);
    expect(screen.getByText('<img src=x onerror=alert(1)> & co')).toBeInTheDocument();
    expect(document.querySelector('img')).toBeNull();
    const title = screen.getByText(long);
    expect(title).toHaveClass('truncate');
    expect(title.parentElement).toHaveAttribute('title', `Rules 003 v3 | ${long}`);
    expect(screen.getByText('Rules 003 v3')).toBeInTheDocument();
    expect(screen.getByText('audit_0e941d0e')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy Audit ID' })).toBeInTheDocument();
  });

  it('shows one fallback for records without a name', () => {
    wrap(<><AuditNameText name={null} /><RulebookNameText name={undefined} version={2} /></>);
    expect(screen.getByText('Unnumbered audit')).toBeInTheDocument();
    expect(screen.getByText('Unnumbered rules v2')).toBeInTheDocument();
  });
});

describe('TitleEditor', () => {
  it('renames by internal ID at the last-read revision and never sends the label', async () => {
    const fetchMock = vi.fn(async () => json({ ...audit, name: { ...audit.name, title: 'Person checks', title_revision: 1 } }));
    vi.stubGlobal('fetch', fetchMock);
    wrap(<TitleEditor kind="audit" id={audit.audit_id} name={makeAuditName({ title_revision: 4 })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rename Audit 014' }));
    const input = screen.getByLabelText('Title for Audit 014');
    expect(input).toHaveValue('LabelMe demo');
    fireEvent.change(input, { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.change(input, { target: { value: '  Person checks  ' } });
    expect(screen.getByText('Audit 014 stays the same; only the title changes.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/v1/audits/${audit.audit_id}/title`);
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ title: 'Person checks', expected_title_revision: 4, reviewer_id: 'rev_test' });
    await waitFor(() => expect(screen.queryByLabelText('Title for Audit 014')).not.toBeInTheDocument());
  });

  it('reports a concurrent rename instead of overwriting it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: { code: 'VERSION_CONFLICT', message: 'stale', details: [], request_id: 'r' } }, 409)));
    wrap(<TitleEditor kind="rulebook" id={rulebook.rulebook_id} name={makeRulebookName()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Rename Rules 003' }));
    fireEvent.change(screen.getByLabelText('Title for Rules 003'), { target: { value: 'Mine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Someone else renamed this');
    expect(screen.getByLabelText('Title for Rules 003')).toHaveValue('Mine');
  });

  it('cannot rename a record without a name', () => {
    wrap(<TitleEditor kind="audit" id="audit-1" name={null} />);
    expect(screen.queryByRole('button', { name: /Rename/ })).not.toBeInTheDocument();
  });
});

function LookupProbe({ ids }: { ids: string[] }) {
  const names = useDisplayNames({ auditIds: ids });
  return <ul>{ids.slice(0, 2).map((id) => <li key={id}>{names.isLoading ? 'loading' : names.audit(id)?.label ?? 'none'}</li>)}</ul>;
}

describe('useDisplayNames', () => {
  it('deduplicates, batches at most 100 IDs per request and leaves unknown IDs unnamed', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const ids = new URL(url, 'http://localhost').searchParams.getAll('audit_id');
      return json({ audits: ids.filter((id) => id !== 'a-000').map((id) => makeAuditName({ audit_id: id, label: `Label ${id}` })), rulebooks: [] });
    });
    vi.stubGlobal('fetch', fetchMock);
    const ids = Array.from({ length: 150 }, (_, index) => `a-${String(index).padStart(3, '0')}`);
    wrap(<LookupProbe ids={[...ids, ...ids, '', '  ']} />);
    expect(await screen.findByText('Label a-001')).toBeInTheDocument();
    expect(screen.getByText('none')).toBeInTheDocument();
    const batches = fetchMock.mock.calls.map(([url]) => new URL(String(url), 'http://localhost').searchParams.getAll('audit_id'));
    expect(batches.map((batch) => batch.length)).toEqual([100, 50]);
    expect(new Set(batches.flat()).size).toBe(150);
  });
});

describe('screens use labels while requests keep internal IDs', () => {
  function routes(extra: (url: URL, init?: RequestInit) => Response | undefined = () => undefined) {
    return vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost');
      const custom = extra(url, init);
      if (custom) return custom;
      if (url.pathname === '/api/v1/audits') return json(page([audit]));
      if (url.pathname === '/api/v1/rulebooks') return json(page([rulebook]));
      if (url.pathname === '/api/v1/datasets') return json(page([dataset]));
      if (url.pathname === '/api/v1/feedback/policies') return json(page([]));
      if (url.pathname === '/api/v1/display-names') return json({ audits: [audit.name], rulebooks: [rulebook.name] });
      return json({ error: { code: 'NOT_FOUND', message: url.pathname, details: [], request_id: 'r' } }, 404);
    });
  }

  it('lists audits by label and title and starts audits with internal IDs', async () => {
    const fetchMock = routes((url, init) => (init?.method === 'POST' && url.pathname === '/api/v1/audits'
      ? json({ audit: { ...audit, status: 'queued' }, job: { job_id: 'job-new', kind: 'audit', status: 'queued', progress: { phase: 'queued', processed: 0, total: 1, fraction: 0 }, result: null, error: null, created_at: audit.created_at, updated_at: audit.created_at }, parent_audit_id: null, feedback_policy_id: null }, 202)
      : undefined));
    vi.stubGlobal('fetch', fetchMock);
    wrap(<AuditsPage />, '/audits');
    const row = (await screen.findByText('Audit 014')).closest('tr')!;
    expect(within(row).getByText('LabelMe demo')).toBeInTheDocument();
    expect(within(row).getByText('Rules 003 v3')).toBeInTheDocument();
    expect(row).not.toHaveTextContent(audit.audit_id);
    fireEvent.click(screen.getByRole('button', { name: 'Launch New Audit' }));
    expect(await screen.findByRole('option', { name: /Rules 003 v3 \| Person annotation checks/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Launch Audit' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(String(init!.body))).toMatchObject({ dataset_id: 'dataset-1', rulebook_id: audit.rulebook_id, rulebook_version: 3 });
  });

  it('finds records in the command palette by label, title or internal ID and opens them by ID', async () => {
    vi.stubGlobal('fetch', routes());
    wrap(<><CurrentRoute /><CommandPalette /></>);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    const search = await screen.findByPlaceholderText('Go to a screen or search records');
    fireEvent.change(search, { target: { value: 'labelme demo' } });
    expect(await screen.findByRole('option', { name: /Audit 014 \| LabelMe demo/ })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: 'rules 003' } });
    expect(await screen.findByRole('option', { name: /Rules 003 v3 \| Person annotation checks/ })).toBeInTheDocument();
    fireEvent.change(search, { target: { value: audit.audit_id.slice(0, 14) } });
    fireEvent.click(await screen.findByRole('option', { name: /Audit 014/ }));
    await waitFor(() => expect(screen.getByTestId('route')).toHaveTextContent(`/audits/${audit.audit_id}/review`));
  });

  it('shows the label instead of the internal ID in breadcrumbs', async () => {
    const fetchMock = routes((url) => (url.pathname.startsWith('/api/v1/health') ? json({ status: 'ok', api_version: 'v1' })
      : url.pathname.startsWith('/api/v1/system/status') ? json({ ollama: { reachable: false }, person_model: {}, execution_device: 'cpu' }) : undefined));
    vi.stubGlobal('fetch', fetchMock);
    wrap(<Routes><Route path="*" element={<Navbar />} /></Routes>, `/audits/${audit.audit_id}/review`);
    const breadcrumb = screen.getByLabelText('Breadcrumb');
    await waitFor(() => expect(breadcrumb).toHaveTextContent('Audits›Audit 014›Review'.replace(/›/g, '')));
    expect(breadcrumb).not.toHaveTextContent(audit.audit_id);
    const lookup = fetchMock.mock.calls.map(([url]) => new URL(String(url), 'http://localhost')).find((url) => url.pathname === '/api/v1/display-names');
    expect(lookup?.searchParams.getAll('audit_id')).toEqual([audit.audit_id]);
  });
});
