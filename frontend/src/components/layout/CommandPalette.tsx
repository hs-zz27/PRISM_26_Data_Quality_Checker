import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, BookOpen, Database, Search, SearchCode } from 'lucide-react';
import { apiGetAllPages } from '../../api/client';
import { queryKeys } from '../../api/queryClient';

interface PaletteItem { label: string; detail: string; path: string; kind: 'screen' | 'audit' | 'dataset' | 'rulebook' }
const screens: PaletteItem[] = [
  { label: 'Audits', detail: 'All audit runs', path: '/audits', kind: 'screen' },
  { label: 'Rulebooks', detail: 'Guidelines and checks', path: '/rulebooks', kind: 'screen' },
  { label: 'Datasets', detail: 'Imported catalogues', path: '/datasets', kind: 'screen' },
  { label: 'Corrections', detail: 'Annotation revisions', path: '/corrections', kind: 'screen' },
  { label: 'Exports', detail: 'Export history', path: '/exports', kind: 'screen' },
  { label: 'Feedback', detail: 'Evaluation and activation', path: '/feedback', kind: 'screen' },
];
const itemIcon = { screen: Search, audit: SearchCode, dataset: Database, rulebook: BookOpen };

export const CommandPalette: React.FC = () => {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const navigate = useNavigate();
  const audits = useQuery({ queryKey: queryKeys.audits(), queryFn: () => apiGetAllPages('/audits', { query: { limit: 100, offset: 0 } }), enabled: open });
  const datasets = useQuery({ queryKey: queryKeys.datasets(), queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }), enabled: open });
  const rulebooks = useQuery({ queryKey: queryKeys.rulebooks(), queryFn: () => apiGetAllPages('/rulebooks', { query: { limit: 100, offset: 0 } }), enabled: open });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault(); setOpen((value) => !value); setSearch(''); setActiveIndex(0);
      } else if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  useEffect(() => { if (open) window.setTimeout(() => document.getElementById('command-palette-search')?.focus(), 0); }, [open]);

  const items = useMemo<PaletteItem[]>(() => [
    ...screens,
    ...(audits.data?.items ?? []).map((audit) => ({ label: audit.audit_id, detail: `Audit · ${audit.dataset_id} · ${audit.status}`, path: `/audits/${audit.audit_id}/review`, kind: 'audit' as const })),
    ...(datasets.data?.items ?? []).map((dataset) => ({ label: dataset.name, detail: `Dataset · ${dataset.dataset_id}`, path: `/datasets/${dataset.dataset_id}`, kind: 'dataset' as const })),
    ...(rulebooks.data?.items ?? []).map((book) => ({ label: book.rulebook_id, detail: `Rulebook · v${book.version} · ${book.confirmation_state}`, path: `/rulebooks/${book.rulebook_id}`, kind: 'rulebook' as const })),
  ], [audits.data, datasets.data, rulebooks.data]);
  const filtered = items.filter((item) => `${item.label} ${item.detail}`.toLowerCase().includes(search.trim().toLowerCase())).slice(0, 12);
  const loading = audits.isLoading || datasets.isLoading || rulebooks.isLoading;
  const failed = audits.isError || datasets.isError || rulebooks.isError;
  const choose = (item: PaletteItem) => { navigate(item.path); setOpen(false); };
  const moveSelection = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); setActiveIndex((index) => filtered.length ? (index + 1) % filtered.length : 0); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActiveIndex((index) => filtered.length ? (index - 1 + filtered.length) % filtered.length : 0); }
    if (event.key === 'Enter' && filtered[activeIndex]) { event.preventDefault(); choose(filtered[activeIndex]); }
  };

  if (!open) return null;
  return <div className="palette-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
    <section className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette">
      <label className="palette-search"><Search size={17} /><input id="command-palette-search" value={search} onChange={(event) => { setSearch(event.target.value); setActiveIndex(0); }} onKeyDown={moveSelection} placeholder="Go to a screen or search records" /><kbd>ESC</kbd></label>
      <div className="palette-results" role="listbox" aria-label="Navigation results">
        {filtered.map((item, index) => { const Icon = itemIcon[item.kind]; return <button key={`${item.kind}:${item.path}`} className={`palette-item${activeIndex === index ? ' is-active' : ''}`} role="option" aria-selected={activeIndex === index} onMouseEnter={() => setActiveIndex(index)} onClick={() => choose(item)}>
          <Icon size={15} /><span className="palette-item-copy"><b>{item.label}</b><small>{item.detail}</small></span><ArrowRight size={14} className="palette-arrow" />
        </button>; })}
        {loading && <p className="palette-message">Loading recent records…</p>}
        {!loading && failed && <p className="palette-message">Some records could not be loaded. Screen links remain available.</p>}
        {!loading && filtered.length === 0 && <p className="palette-message">No matching screens or records.</p>}
      </div>
      <footer className="palette-footer"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span><kbd>⌘ / Ctrl</kbd> + <kbd>K</kbd> close</span></footer>
    </section>
  </div>;
};
