import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Crosshair, Eye, EyeOff, HelpCircle, Maximize2, Minus, Plus, Tag } from 'lucide-react';
import { useCanvasStore } from '../../store/canvasStore';
import { bboxToCss, cssToImageCoord, formatBbox, formatCursorPos, type BBox1Based } from '../../lib/coords';
import type { components } from '../../api/generated/openapi';
import { isTypingTarget } from '../../lib/keyboard';
import { colorFor, getObjectBounds, textColorFor } from './reviewWorkspace';

type Finding = components['schemas']['Finding'];
export interface CanvasObject {
  object_id: number;
  label: string;
  bbox: BBox1Based | null;
  coordinate_system?: 'zero_based_continuous';
  polygons?: number[][][];
  bbox_origin?: string | null;
  source_key?: string;
  source_id?: number | string | null;
}
export interface CanvasAssets {
  image?: { url: string; content_type?: string; revision?: number } | null;
  mask?: { url: string; content_type?: string; revision?: number } | null;
  mask_preview?: { url: string; content_type?: string; revision?: number } | null;
}
interface ReviewCanvasProps {
  sampleId?: string; imageSize?: [number, number] | null; assets?: CanvasAssets | null;
  objects?: CanvasObject[]; findings: Finding[]; onSelectObject?: (objectId: number | null) => void;
  hiddenObjectIds?: number[];
  annotationFormat?: 'penn_fudan' | 'coco' | 'labelme';
  isCorrection?: boolean;
}

interface BadgePlacement {
  displayNumber: number;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  leaderTarget: { x: number; y: number } | null;
}

const BADGE_W = 26;
const BADGE_H = 18;



/** Check if two rects overlap. */
function rectsOverlap(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }): boolean {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

/** Place numbered badges near objects, avoiding overlap (like the backend _place_badge). */
function placeBadges(
  objects: Array<{ displayNumber: number; text: string; bounds: { x0: number; y0: number; x1: number; y1: number } }>,
  panelW: number,
  panelH: number,
): BadgePlacement[] {
  const occupied: Array<{ x: number; y: number; w: number; h: number }> = [];
  const placements: BadgePlacement[] = [];

  // Sort by area ascending — smaller objects first so they get priority for nearby placement
  const ordered = [...objects].sort((a, b) => {
    const areaA = (a.bounds.x1 - a.bounds.x0) * (a.bounds.y1 - a.bounds.y0);
    const areaB = (b.bounds.x1 - b.bounds.x0) * (b.bounds.y1 - b.bounds.y0);
    return areaA - areaB;
  });

  for (const obj of ordered) {
    const { bounds, displayNumber, text } = obj;
    const w = BADGE_W + text.length * 3; // slightly wider for symbols
    const h = BADGE_H;
    const cx = (bounds.x0 + bounds.x1) / 2;
    const cy = (bounds.y0 + bounds.y1) / 2;

    // Candidate positions near the object
    const candidates: Array<[number, number]> = [
      [bounds.x0, bounds.y0 - h - 1],
      [bounds.x0 + 1, bounds.y0 + 1],
      [bounds.x1 - w, bounds.y0 - h - 1],
      [bounds.x1 - w - 1, bounds.y0 + 1],
      [bounds.x0, bounds.y1 + 1],
      [cx - w / 2, cy - h / 2],
    ];
    // Expand search outward
    const steps: Array<[number, number]> = [
      [-1, -1], [1, -1], [-1, 1], [1, 1], [0, -1], [0, 1], [-1, 0], [1, 0],
      [-2, -1], [2, -1], [-2, 1], [2, 1], [-1, -2], [1, -2], [-1, 2], [1, 2],
    ];
    for (let ring = 1; ring <= 10; ring++) {
      for (const [dx, dy] of steps) {
        candidates.push([bounds.x0 + dx * ring * (w * 0.55 + 2), bounds.y0 + dy * ring * (h + 3) * 0.75]);
      }
    }

    let bestCost = Infinity;
    let bestRect = { x: bounds.x0, y: Math.max(0, bounds.y0 - h - 1), w, h };

    for (const [left, top] of candidates) {
      const clampedLeft = Math.min(Math.max(left, 0), Math.max(panelW - w, 0));
      const clampedTop = Math.min(Math.max(top, 0), Math.max(panelH - h, 0));
      const rect = { x: clampedLeft, y: clampedTop, w, h };
      let cost = 0;
      for (const other of occupied) {
        if (rectsOverlap(rect, other)) {
          const overlapW = Math.min(rect.x + rect.w, other.x + other.w) - Math.max(rect.x, other.x);
          const overlapH = Math.min(rect.y + rect.h, other.y + other.h) - Math.max(rect.y, other.y);
          cost += overlapW * overlapH;
        }
      }
      if (cost === 0) {
        bestRect = rect;
        bestCost = 0;
        break;
      }
      if (cost < bestCost) {
        bestCost = cost;
        bestRect = rect;
      }
    }

    occupied.push(bestRect);

    // Check if badge is touching the object bounds
    const touching = (
      bestRect.x <= bounds.x1 + 2 && bestRect.x + bestRect.w >= bounds.x0 - 2 &&
      bestRect.y <= bounds.y1 + 2 && bestRect.y + bestRect.h >= bounds.y0 - 2
    );

    let leaderTarget: { x: number; y: number } | null = null;
    if (!touching) {
      leaderTarget = {
        x: Math.min(Math.max((bestRect.x + bestRect.w / 2), bounds.x0), bounds.x1),
        y: Math.min(Math.max((bestRect.y + bestRect.h / 2), bounds.y0), bounds.y1),
      };
    }

    placements.push({
      displayNumber,
      text,
      x: bestRect.x,
      y: bestRect.y,
      width: bestRect.w,
      height: bestRect.h,
      leaderTarget,
    });
  }

  return placements;
}

export const ReviewCanvas: React.FC<ReviewCanvasProps> = ({
  sampleId,
  imageSize,
  assets,
  objects = [],
  findings,
  onSelectObject,
  hiddenObjectIds = [],
  annotationFormat,
  isCorrection,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef<HTMLSpanElement>(null);
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const panModeRef = useRef(false);
  const [imageError, setImageError] = useState(false);
  const {
    showMask, showBoxes, showPolygons, showLabels, showLegend,
    maskOpacity, selectedInstanceId, zoom, pan,
    toggleMask, toggleBoxes, togglePolygons, toggleLabels, toggleLegend,
    setMaskOpacity, setSelectedInstanceId, setZoom, setPan,
  } = useCanvasStore();
  const continuous = annotationFormat === 'coco' || annotationFormat === 'labelme' || objects.some((obj) => obj.coordinate_system === 'zero_based_continuous');
  const width = imageSize?.[0] ?? 640;
  const height = imageSize?.[1] ?? 480;

  const handleFit = useCallback(() => {
    if (!containerRef.current || !imageSize) return;
    const r = containerRef.current.getBoundingClientRect();
    if (r.width <= 40 || r.height <= 80) {
      setZoom(1);
      setPan({ x: 0, y: 0 });
      return;
    }
    const scale = Math.min((r.width - 40) / width, (r.height - 80) / height, 1);
    setZoom(Math.max(0.05, scale)); setPan({ x: 0, y: 0 });
  }, [imageSize, setPan, setZoom, width, height]);

  useEffect(() => { setImageError(false); handleFit(); }, [sampleId, handleFit]);

  const updateZoom = (factor: number) => setZoom(Math.min(8, Math.max(0.05, zoom * factor)));
  const outcome = (id: number) => findings.find((f) => f.object_id === id)?.outcome ?? 'unavailable';

  // Build a stable display-number mapping: 1..N in objects array order, same as backend
  const displayNumberMap = useMemo(() => {
    const map = new Map<number, number>();
    objects.forEach((obj, idx) => { map.set(obj.object_id, idx + 1); });
    return map;
  }, [objects]);

  // Visible objects (not hidden by per-object toggle)
  const visibleObjects = useMemo(() =>
    objects.filter((obj) => !hiddenObjectIds.includes(obj.object_id)),
    [objects, hiddenObjectIds],
  );

  // Zoom to fit the currently selected object in view
  const zoomToSelected = useCallback(() => {
    if (selectedInstanceId == null || !containerRef.current) return;
    const selectedObj = objects.find((o) => o.object_id === selectedInstanceId);
    if (!selectedObj) return;
    const b = getObjectBounds(selectedObj);
    if (!b) return;

    const r = containerRef.current.getBoundingClientRect();
    const bw = Math.max(b.x1 - b.x0, 24);
    const bh = Math.max(b.y1 - b.y0, 24);
    const scale = Math.min((r.width - 60) / bw, (r.height - 100) / bh, 8);
    const targetZoom = Math.max(0.1, Math.min(8, scale));

    const cx = (b.x0 + b.x1) / 2;
    const cy = (b.y0 + b.y1) / 2;
    // The stage is centred in the canvas and scales around its own centre.
    const panX = -(cx - width / 2) * targetZoom;
    const panY = -(cy - height / 2) * targetZoom;

    setZoom(targetZoom);
    setPan({ x: panX, y: panY });
  }, [selectedInstanceId, objects, setZoom, setPan, width, height]);

  // Keyboard shortcut listener
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      if (e.code === 'Space' && !e.repeat) { panModeRef.current = true; e.preventDefault(); }
      if ((e.key === 'z' || e.key === 'Z') && !e.repeat && selectedInstanceId != null) {
        e.preventDefault();
        zoomToSelected();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => { if (e.code === 'Space') panModeRef.current = false; };
    window.addEventListener('keydown', onKeyDown); window.addEventListener('keyup', onKeyUp);
    return () => { window.removeEventListener('keydown', onKeyDown); window.removeEventListener('keyup', onKeyUp); };
  }, [selectedInstanceId, zoomToSelected]);

  // Hit-testing and overlap cycling when clicking geometry on the canvas
  const handleStageClick = useCallback((e: React.MouseEvent) => {
    if (!stageRef.current) return;
    const rect = stageRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return;

    const cx = x / zoom;
    const cy = y / zoom;

    // Find all visible objects whose bounds contain the click point
    const hits: Array<{ obj: CanvasObject; area: number }> = [];
    for (const obj of visibleObjects) {
      const b = getObjectBounds(obj);
      if (b && cx >= b.x0 && cx <= b.x1 && cy >= b.y0 && cy <= b.y1) {
        const area = (b.x1 - b.x0) * (b.y1 - b.y0);
        hits.push({ obj, area });
      }
    }

    if (hits.length === 0) {
      setSelectedInstanceId(null);
      onSelectObject?.(null);
      return;
    }

    // Sort smallest area first: smallest object wins priority
    hits.sort((a, b) => a.area - b.area);

    // If currently selected object is among hits, cycle through to next overlapping object
    const currIdx = hits.findIndex((h) => h.obj.object_id === selectedInstanceId);
    const nextIdx = currIdx >= 0 ? (currIdx + 1) % hits.length : 0;
    const targetObj = hits[nextIdx]?.obj;
    if (targetObj) {
      const nextId = targetObj.object_id;
      setSelectedInstanceId(nextId);
      onSelectObject?.(nextId);
    }
  }, [zoom, visibleObjects, selectedInstanceId, setSelectedInstanceId, onSelectObject]);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0 && e.button !== 1) return;
    if (!panModeRef.current && e.button !== 1) return;
    dragRef.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (dragRef.current && stageRef.current) {
      const d = dragRef.current;
      stageRef.current.style.transform = `translate3d(${d.panX + e.clientX - d.x}px,${d.panY + e.clientY - d.y}px,0) scale(${zoom})`;
      return;
    }
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect || !cursorRef.current) return;
    const x = e.clientX - rect.left; const y = e.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) { cursorRef.current.textContent = '[X: —, Y: —]'; return; }
    const [ix, iy] = continuous ? [x / zoom, y / zoom] : cssToImageCoord(x, y, zoom);
    cursorRef.current.textContent = formatCursorPos(Math.max(continuous ? 0 : 1, Math.min(ix, width)), Math.max(continuous ? 0 : 1, Math.min(iy, height)));
  };
  const onPointerUp = () => {
    if (dragRef.current) {
      const matrix = stageRef.current ? new DOMMatrixReadOnly(getComputedStyle(stageRef.current).transform) : null;
      if (matrix) setPan({ x: matrix.m41, y: matrix.m42 });
      dragRef.current = null;
    }
  };

  // Compute badge placements for numbered markers
  const badgePlacements = useMemo(() => {
    const badgeInputs: Array<{ displayNumber: number; text: string; bounds: { x0: number; y0: number; x1: number; y1: number } }> = [];
    for (const obj of visibleObjects) {
      const dn = displayNumberMap.get(obj.object_id) ?? 0;
      if (dn === 0) continue;
      const b = getObjectBounds(obj);
      if (!b) continue;
      const isDerived = obj.bbox_origin === 'derived_from_polygon' && obj.bbox != null;
      const hasDiag = findings.some((f) => f.object_id === obj.object_id && (f.outcome === 'fail' || f.outcome === 'unsure'));
      const suffix = (isDerived ? '*' : '') + (hasDiag ? '!' : '');
      badgeInputs.push({ displayNumber: dn, text: `#${dn}${suffix}`, bounds: b });
    }
    return placeBadges(badgeInputs, width, height);
  }, [visibleObjects, displayNumberMap, width, height, findings]);

  return <section ref={containerRef} className="review-canvas" aria-label="Image review canvas" onWheel={(e) => { e.preventDefault(); updateZoom(e.deltaY < 0 ? 1.12 : 0.89); }} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
    <div className="canvas-toolbar">
      <button title="Zoom out" aria-label="Zoom out" onClick={() => updateZoom(1 / 1.2)}><Minus size={14}/></button>
      <span className="mono">{Math.round(zoom * 100)}%</span>
      <button title="Zoom in" aria-label="Zoom in" onClick={() => updateZoom(1.2)}><Plus size={14}/></button>
      <button title="Fit image (F)" aria-label="Fit image" onClick={handleFit}><Maximize2 size={14}/></button>
      <button className={selectedInstanceId != null ? 'is-on' : ''} disabled={selectedInstanceId == null} title="Zoom to selected object (Z)" aria-label="Zoom to selected object" onClick={zoomToSelected}><Crosshair size={14}/></button>
      <i/>
      <button className={showBoxes ? 'is-on' : ''} title="Toggle boxes (B)" aria-label="Toggle boxes" onClick={toggleBoxes}>B</button>
      <button className={showPolygons ? 'is-on' : ''} title="Toggle polygons (P)" aria-label="Toggle polygons" onClick={togglePolygons}>P</button>
      <button className={showLabels ? 'is-on' : ''} title="Toggle labels (L)" aria-label="Toggle labels" onClick={toggleLabels}><Tag size={14}/></button>
      <button className={showMask ? 'is-on' : ''} title="Toggle mask (M)" aria-label="Toggle mask" onClick={toggleMask}>{showMask ? <Eye size={14}/> : <EyeOff size={14}/>}</button>
      {showMask && <label className="mask-opacity">Mask <input aria-label="Mask opacity" type="range" min="0" max="1" step="0.05" value={maskOpacity} onChange={(e) => setMaskOpacity(Number(e.target.value))}/></label>}
      <button className={showLegend ? 'is-on' : ''} title="Toggle legend" aria-label="Toggle legend" onClick={toggleLegend}><HelpCircle size={14}/></button>
      <span className="canvas-shortcut">Space + drag to pan</span>
    </div>

    {/* Legend overlay */}
    {showLegend && <div className="canvas-legend" role="note" aria-label="Overlay legend">
      <div className="canvas-legend-title">How to read this overlay</div>
      <div className="canvas-legend-row"><span className="canvas-legend-swatch" style={{ borderStyle: 'solid' }}/> Solid line: supplied geometry (from source file)</div>
      <div className="canvas-legend-row"><span className="canvas-legend-swatch" style={{ borderStyle: 'dashed' }}/> Dashed line: derived box (computed from polygon vertices, not in source file)</div>
      <div className="canvas-legend-row"><span className="mono">#N</span> Display number matching object list entry</div>
      <div className="canvas-legend-row"><span className="mono">*</span> Bounding box is derived from polygon vertices</div>
      <div className="canvas-legend-row"><span className="mono">!</span> Diagnostic finding flagged or unsure for this object</div>
      <div className="canvas-legend-row">Colors match between geometry, marker and list entry.</div>
      {isCorrection && <div className="canvas-legend-row"><em>Showing proposed correction (preview, not saved).</em></div>}
    </div>}

    <div className="canvas-stage" ref={stageRef} style={{ width, height, transform: `translate3d(${pan.x}px,${pan.y}px,0) scale(${zoom})` }}>
      {assets?.image?.url && !imageError ? <img src={assets.image.url} alt={sampleId ?? 'Sample'} draggable={false} onError={() => setImageError(true)}/> : <div className="canvas-empty" role="status">{imageError ? 'Image could not be loaded' : 'No image asset for this revision'}</div>}
      {showMask && (assets?.mask_preview?.url ?? assets?.mask?.url) && <img className="canvas-mask" src={assets.mask_preview?.url ?? assets.mask?.url} alt="Mask preview" style={{ opacity: maskOpacity }} draggable={false} onError={() => setImageError(true)}/>}
      {(showBoxes || showPolygons) && <svg className="canvas-overlay" viewBox={`0 0 ${width} ${height}`} aria-label="Object annotations" onClick={handleStageClick}>{visibleObjects.map((obj) => {
        const b = obj.bbox ? (obj.coordinate_system === 'zero_based_continuous' ? { left: obj.bbox[0], top: obj.bbox[1], width: obj.bbox[2] - obj.bbox[0], height: obj.bbox[3] - obj.bbox[1] } : bboxToCss(obj.bbox)) : null;
        const state = outcome(obj.object_id);
        const selected = selectedInstanceId === obj.object_id;
        const dn = displayNumberMap.get(obj.object_id) ?? 0;
        const objColor = colorFor(dn);
        const isDerived = obj.bbox_origin === 'derived_from_polygon';
        return <g key={obj.object_id} role="button" tabIndex={0} aria-label={`Object ${dn}, ${obj.label}, ${obj.bbox ? formatBbox(obj.bbox) : "no box"}`} onClick={(e) => { e.stopPropagation(); handleStageClick(e); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); const id = selected ? null : obj.object_id; setSelectedInstanceId(id); onSelectObject?.(id); } }} className={`box-${state}${selected ? ' is-selected' : ''}`}>
          {/* Polygons */}
          {showPolygons && obj.polygons?.map((part, index) => <polygon key={index} points={part.map((point) => point.join(',')).join(' ')} fill="none" stroke={objColor} strokeWidth={selected ? 2.5 : 1.5} vectorEffect="non-scaling-stroke" />)}
          {/* Boxes: solid for supplied, dashed for derived */}
          {showBoxes && b && <>
            <rect x={b.left} y={b.top} width={b.width} height={b.height} stroke={objColor} style={{ stroke: objColor }} strokeDasharray={isDerived ? '6 4' : 'none'} vectorEffect="non-scaling-stroke" />
          </>}
          {/* Visual indicator for objects without visible geometry */}
          {!b && !obj.polygons?.length && <circle cx={width / 2} cy={height / 2} r={8} fill={objColor} fillOpacity={0.3} stroke={objColor} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
        </g>;
      })}
      {/* Numbered badges */}
      {showLabels && badgePlacements.map((badge) => {
        const objColor = colorFor(badge.displayNumber);
        const txtColor = textColorFor(badge.displayNumber);
        const obj = objects.find((o) => displayNumberMap.get(o.object_id) === badge.displayNumber);
        const selected = obj ? selectedInstanceId === obj.object_id : false;
        return <g key={`badge-${badge.displayNumber}`} className={selected ? 'canvas-badge is-selected' : 'canvas-badge'} onClick={(e) => { e.stopPropagation(); if (!obj) return; const id = selected ? null : obj.object_id; setSelectedInstanceId(id); onSelectObject?.(id); }} style={{ cursor: 'pointer' }}>
          {/* Leader line if badge is displaced */}
          {badge.leaderTarget && <>
            <line x1={badge.x + badge.width / 2} y1={badge.y + badge.height / 2} x2={badge.leaderTarget.x} y2={badge.leaderTarget.y} stroke="rgba(20,20,20,0.6)" strokeWidth={3} vectorEffect="non-scaling-stroke" />
            <line x1={badge.x + badge.width / 2} y1={badge.y + badge.height / 2} x2={badge.leaderTarget.x} y2={badge.leaderTarget.y} stroke={objColor} strokeWidth={1} vectorEffect="non-scaling-stroke" />
          </>}
          <rect x={badge.x} y={badge.y} width={badge.width} height={badge.height} rx={3} fill={objColor} style={{ fill: objColor }} stroke="rgba(20,20,20,0.7)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          <text x={badge.x + badge.width / 2} y={badge.y + badge.height / 2 + 1} textAnchor="middle" dominantBaseline="central" fill={txtColor} style={{ fill: txtColor, stroke: 'none' }} className="canvas-badge-text">{badge.text}</text>
        </g>;
      })}
      </svg>}
    </div>

    <div className="canvas-readout">
      <span ref={cursorRef} className="mono">[X: —, Y: —]</span>
      <span className="mono">{width} × {height} px</span>
      <span className="mono">{continuous ? '0-based continuous' : '1-based inclusive'}</span>
      <span className={`canvas-mode-tag mono ${isCorrection ? 'is-correction' : 'is-original'}`} role="status">
        {isCorrection ? 'Proposed correction (preview, not saved)' : 'Original'}
      </span>
    </div>
  </section>;
};
