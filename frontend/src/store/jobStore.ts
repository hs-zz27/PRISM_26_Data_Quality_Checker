import { create } from 'zustand';
interface JobsState { ids: string[]; add: (id: string) => void; remove: (id: string) => void }
export const useJobStore = create<JobsState>((set) => ({ ids: [], add: (id) => set((s) => ({ ids: s.ids.includes(id) ? s.ids : [...s.ids, id] })), remove: (id) => set((s) => ({ ids: s.ids.filter((item) => item !== id) })) }));
