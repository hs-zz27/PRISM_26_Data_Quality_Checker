/**
 * Canvas Store
 * ─────────────────────────────────────────────────────────────────────────
 * Manages canvas preferences and display states for the Review Workspace:
 *   - showMask (boolean)
 *   - showBoxes (boolean)
 *   - maskOpacity (number: 0.0 to 1.0)
 *   - selectedInstanceId (number | null)
 *   - zoom (number: e.g. 1.0)
 *   - pan ({ x: number, y: number })
 * ─────────────────────────────────────────────────────────────────────────
 */

import { create } from 'zustand';

interface CanvasState {
  showMask: boolean;
  showBoxes: boolean;
  showPolygons: boolean;
  showLabels: boolean;
  showLegend: boolean;
  /** Show the photo alone: every overlay layer is hidden until this is switched off again. */
  originalOnly: boolean;
  maskOpacity: number;
  selectedInstanceId: number | null;
  zoom: number;
  pan: { x: number; y: number };
  toggleMask: () => void;
  toggleBoxes: () => void;
  togglePolygons: () => void;
  toggleLabels: () => void;
  toggleLegend: () => void;
  toggleOriginalOnly: () => void;
  setMaskOpacity: (opacity: number) => void;
  setSelectedInstanceId: (id: number | null) => void;
  setZoom: (zoom: number) => void;
  setPan: (pan: { x: number; y: number }) => void;
  resetView: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  showMask: true,
  showBoxes: true,
  showPolygons: true,
  showLabels: true,
  showLegend: false,
  originalOnly: false,
  maskOpacity: 0.45,
  selectedInstanceId: null,
  zoom: 1.0,
  pan: { x: 0, y: 0 },
  toggleMask: () => set((s) => ({ showMask: !s.showMask })),
  toggleBoxes: () => set((s) => ({ showBoxes: !s.showBoxes })),
  togglePolygons: () => set((s) => ({ showPolygons: !s.showPolygons })),
  toggleLabels: () => set((s) => ({ showLabels: !s.showLabels })),
  toggleLegend: () => set((s) => ({ showLegend: !s.showLegend })),
  toggleOriginalOnly: () => set((s) => ({ originalOnly: !s.originalOnly })),
  setMaskOpacity: (maskOpacity) => set({ maskOpacity }),
  setSelectedInstanceId: (selectedInstanceId) => set({ selectedInstanceId }),
  setZoom: (zoom) => set({ zoom: Math.max(0.2, Math.min(zoom, 10.0)) }),
  setPan: (pan) => set({ pan }),
  resetView: () => set({ zoom: 1.0, pan: { x: 0, y: 0 }, selectedInstanceId: null }),
}));
