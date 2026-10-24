import React from 'react';
import { useJobStore } from '../../store/jobStore';
import { useJobPolling } from '../../hooks/useJobPolling';
function JobItem({ id }: { id: string }) { const state = useJobPolling(id); return <div className="text-2xs font-mono px-2 py-1 border-l border-border">{id} · {state.status ?? (state.isLoading ? 'loading' : 'unavailable')}{state.isActive && <button className="ml-2" onClick={() => state.cancel()}>Cancel</button>}</div>; }
export const GlobalJobTray: React.FC = () => { const ids = useJobStore((state) => state.ids); return ids.length ? <div className="flex items-center overflow-x-auto border-t border-border bg-surface px-2">{ids.map((id) => <JobItem key={id} id={id} />)}</div> : null; };
