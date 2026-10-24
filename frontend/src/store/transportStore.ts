import { create } from 'zustand';

export type DataSource = 'live' | 'mock';

interface TransportState {
  sources: Record<string, DataSource>;
  record: (operationId: string, source: DataSource) => void;
}

export const useTransportStore = create<TransportState>((set) => ({
  sources: {},
  record: (operationId, source) =>
    set((state) => ({ sources: { ...state.sources, [operationId]: source } })),
}));

export function recordTransport(operationId: string, source: DataSource): void {
  useTransportStore.getState().record(operationId, source);
}
