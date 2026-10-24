import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Activity, Bot, Layers, UserRound, ChevronRight, CircleHelp, BriefcaseBusiness, Search } from 'lucide-react';
import { apiGet } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { useJobStore } from '../../store/jobStore';
import { useTransportStore } from '../../store/transportStore';
import { ReviewerPromptModal } from '../ui/ReviewerPromptModal';
import { KeyboardHelpModal } from '../ui/KeyboardHelpModal';
import { MockBadge } from '../ui/MockBadge';
import { CommandPalette } from './CommandPalette';

function breadcrumbName(part: string): string {
  const labels: Record<string, string> = { audits: 'Audits', review: 'Review', report: 'Report', datasets: 'Datasets', rulebooks: 'Rulebooks', corrections: 'Corrections', exports: 'Exports', feedback: 'Feedback', dev: 'Developer', styleguide: 'Styleguide' };
  return labels[part] ?? part;
}

function Readiness({ icon: Icon, label, ready, mockOperation }: { icon: React.ComponentType<{ size?: number }>; label: string; ready: boolean; mockOperation?: string }) {
  const mocked = useTransportStore((state) => mockOperation ? state.sources[mockOperation] === 'mock' : false);
  return <span className={`readiness-chip${mocked ? ' is-mock' : ready ? ' is-ready' : ' is-unavailable'}`}>
    <Icon size={13} /> <span>{label} {mocked ? 'mock' : ready ? 'ready' : 'Unavailable'}</span>
    <i aria-hidden="true" className="readiness-marker" />
    {mockOperation && <MockBadge operationId={mockOperation} />}
  </span>;
}

export const Navbar: React.FC = () => {
  const { reviewerId, openPrompt } = useReviewerStore();
  const [helpOpen, setHelpOpen] = useState(false);
  const { pathname } = useLocation();
  const jobCount = useJobStore((state) => state.ids.length);
  const { data: health, isError: healthError } = useQuery({ queryKey: queryKeys.health(), queryFn: () => apiGet('/health'), refetchInterval: 30_000 });
  const { data: system, isError: systemError } = useQuery({ queryKey: queryKeys.systemStatus(), queryFn: () => apiGet('/system/status'), refetchInterval: 30_000 });
  const parts = pathname.split('/').filter(Boolean);
  const apiReady = !healthError && health?.status === 'ok';
  const ollamaReady = !systemError && Boolean(system?.ollama?.reachable);
  const detector = system?.detector;
  const detectorReady = !systemError && Boolean(detector?.integration_supported
    && detector.configuration === 'enabled' && detector.checkpoint_file === 'present'
    && detector.validation === 'validated');

  return <>
    <header className="topbar">
      <div className="breadcrumb" aria-label="Breadcrumb">
        {parts.map((part, index) => <React.Fragment key={`${part}-${index}`}>
          {index > 0 && <ChevronRight size={13} className="breadcrumb-separator" />}
          <span className={index === parts.length - 1 ? 'breadcrumb-current' : ''}>{breadcrumbName(part)}</span>
        </React.Fragment>)}
        {parts.length === 0 && <span className="breadcrumb-current">Overview</span>}
      </div>
      <div className="topbar-tools">
        <button className="palette-trigger" onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))} aria-label="Open command palette"><Search size={13} /><span>Go to…</span><kbd>⌘ / Ctrl K</kbd></button>
        <div className="readiness-group" aria-label="System readiness">
          <Readiness icon={Activity} label="API" ready={apiReady} mockOperation="getHealth" />
          <Readiness icon={Bot} label="Ollama" ready={ollamaReady} mockOperation="getSystemStatus" />
          <Readiness icon={Layers} label="Detector" ready={detectorReady} mockOperation="getSystemStatus" />
        </div>
        <span className="topbar-divider" />
        <span className="job-count" title={`${jobCount} tracked jobs`}><BriefcaseBusiness size={14} /> Jobs <b className="mono">{jobCount}</b></span>
        <button className="topbar-icon-button" onClick={() => setHelpOpen(true)} title="Keyboard shortcuts" aria-label="Keyboard shortcuts"><CircleHelp size={15} /><kbd>?</kbd></button>
        <button className="reviewer-control" onClick={openPrompt} title="Set reviewer attribution"><UserRound size={14} /><span>{reviewerId || 'Set reviewer'}</span></button>
      </div>
    </header>
    <ReviewerPromptModal />
    <KeyboardHelpModal open={helpOpen} onOpenChange={setHelpOpen} />
    <CommandPalette />
  </>;
};
