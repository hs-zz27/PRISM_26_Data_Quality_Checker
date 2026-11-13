import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Crosshair, Eye, EyeOff, HelpCircle, Maximize2, Minus, Plus, Tag } from 'lucide-react';
import { useCanvasStore } from '../../store/canvasStore';
import { bboxToCss, cssToImageCoord, formatBbox, formatCursorPos, type BBox1Based } from '../../lib/coords';
import type { components } from '../../api/generated/openapi';
import { isTypingTarget } from '../../lib/keyboard';
import { colorFor, getObjectBounds, textColorFor } from './reviewWorkspace';
import { badgeWidth, boundarySegments, drawsBox, isBoxCheck, placeBadges, type CssBox, type OverlayShape } from './overlayLayout';

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
    showMask, showBoxes, showPolygons, showLabels, showLegend, originalOnly,
    maskOpacity, selectedInstanceId, zoom, pan,
    toggleMask, toggleBoxes, togglePolygons, toggleLabels, toggleLegend, toggleOriginalOnly,
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
      if ((e.key === 'o' || e.key === 'O') && !e.repeat && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        toggleOriginalOnly();
      }
    };
    const onKeyUp = (e: KeyboardEvent) => { if (e.code === 'Space') panModeRef.current = false; };
    window.addEventListener('keydown', onKeyDown); window.addEventListener('keyup', onKeyUp);
    return () => { window.removeEventListener('keydown', onKeyDown); window.removeEventListener('keyup', onKeyUp); };
  }, [selectedInstanceId, zoomToSelected, toggleOriginalOnly]);

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

  // What is drawn for each object. An object with a polygon is drawn as that polygon only; its box appears
  // only when a saved box-check finding is about it, so older audits still show their evidence.
  const polygonData = annotationFormat === 'coco' || annotationFormat === 'labelme';
  const boxFindingIds = useMemo(
    () => new Set(findings.filter((f) => f.object_id != null && isBoxCheck(f.check_name)).map((f) => f.object_id as number)),
    [findings],
  );
  const shapes = useMemo(() => {
    const map = new Map<number, OverlayShape>();
    for (const obj of objects) {
      const box: CssBox | null = obj.bbox && drawsBox(obj, boxFindingIds.has(obj.object_id))
        ? (obj.coordinate_system === 'zero_based_continuous'
          ? { left: obj.bbox[0], top: obj.bbox[1], width: obj.bbox[2] - obj.bbox[0], height: obj.bbox[3] - obj.bbox[1] }
          : bboxToCss(obj.bbox))
        : null;
      map.set(obj.object_id, { polygons: obj.polygons ?? [], box });
    }
    return map;
  }, [objects, boxFindingIds]);
  const anyBoxDrawn = useMemo(() => [...shapes.values()].some((shape) => shape.box != null), [shapes]);

  // Labels: the display number, "!" when a finding needs a look, then the annotation's stable key.
  const badgeParts = useMemo(() => {
    const map = new Map<number, { number: string; key: string | null }>();
    for (const obj of visibleObjects) {
      const dn = displayNumberMap.get(obj.object_id) ?? 0;
      if (dn === 0) continue;
      const flagged = findings.some((f) => f.object_id === obj.object_id && (f.outcome === 'fail' || f.outcome === 'unsure'));
      map.set(dn, { number: `#${dn}${flagged ? '!' : ''}`, key: obj.source_key?.trim() ? obj.source_key : null });
    }
    return map;
  }, [visibleObjects, displayNumberMap, findings]);

  // Labels are placed off every drawn outline and off each other.
  const badgePlacements = useMemo(() => {
    const inputs = [];
    const drawn: OverlayShape[] = [];
    for (const obj of visibleObjects) {
      const dn = displayNumberMap.get(obj.object_id) ?? 0;
      const parts = badgeParts.get(dn);
      const b = getObjectBounds(obj);
      const shape = shapes.get(obj.object_id);
      if (!parts || !b || !shape) continue;
      inputs.push({ displayNumber: dn, text: parts.key ? `${parts.number} ${parts.key}` : parts.number, bounds: b });
      drawn.push({ polygons: showPolygons ? shape.polygons : [], box: showBoxes ? shape.box : null });
    }
    return placeBadges(inputs, width, height, boundarySegments(drawn));
  }, [visibleObjects, displayNumberMap, badgeParts, shapes, showPolygons, showBoxes, width, height]);

  // The selected object is drawn last, so no other outline can hide it.
  const drawOrder = useMemo(
    () => [...visibleObjects].sort((a, b) => Number(a.object_id === selectedInstanceId) - Number(b.object_id === selectedInstanceId)),
    [visibleObjects, selectedInstanceId],
  );
  const selectedObject = objects.find((obj) => obj.object_id === selectedInstanceId) ?? null;
  const someSelected = selectedObject != null && visibleObjects.includes(selectedObject);

  // Objects with no box and no polygon have no location, so they are never
  // drawn on the image; they are listed in a fixed strip instead.
  const undrawableObjects = useMemo(() =>
    objects.filter((obj) => !getObjectBounds(obj)),
    [objects],
  );

  return <section ref={containerRef} className="review-canvas" aria-label="Image review canvas" onWheel={(e) => { e.preventDefault(); updateZoom(e.deltaY < 0 ? 1.12 : 0.89); }} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
    <div className="canvas-toolbar">
      <button title="Zoom out" aria-label="Zoom out" onClick={() => updateZoom(1 / 1.2)}><Minus size={14}/></button>
      <span className="mono">{Math.round(zoom * 100)}%</span>
      <button title="Zoom in" aria-label="Zoom in" onClick={() => updateZoom(1.2)}><Plus size={14}/></button>
      <button title="Fit image (F)" aria-label="Fit image" onClick={handleFit}><Maximize2 size={14}/></button>
      <button className={selectedInstanceId != null ? 'is-on' : ''} disabled={selectedInstanceId == null} title="Zoom to selected object (Z)" aria-label="Zoom to selected object" onClick={zoomToSelected}><Crosshair size={14}/></button>
      <i/>
      <button className={originalOnly ? 'is-on' : ''} aria-pressed={originalOnly} title="Show the original photo only, with nothing drawn on it (O)" aria-label="Show the original photo only" onClick={toggleOriginalOnly}>Original</button>
      <i/>
      {anyBoxDrawn && <button className={showBoxes ? 'is-on' : ''} title="Toggle boxes (B)" aria-label="Toggle boxes" onClick={toggleBoxes}>B</button>}
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
      <div className="canvas-legend-row"><span className="canvas-legend-swatch" style={{ borderTopWidth: 1 }}/> Thin line: an annotation, drawn exactly as the file has it</div>
      <div className="canvas-legend-row"><span className="canvas-legend-swatch is-selected-swatch"/> Thick line with a tinted fill: the selected annotation. The others fade.</div>
      {anyBoxDrawn && <div className="canvas-legend-row"><span className="canvas-legend-swatch is-box-swatch"/> Rectangle: a box from the annotation file</div>}
      <div className="canvas-legend-row"><span className="mono">#N</span>: the number in the object list, followed by the annotation’s key in the file</div>
      <div className="canvas-legend-row"><span className="mono">!</span>: a finding for this annotation needs a look</div>
      <div className="canvas-legend-row"><span className="mono">Original</span>: hides everything drawn over the photo. The photo itself is never changed.</div>
      <div className="canvas-legend-row">Each annotation keeps one colour on the image and in the list.</div>
      {isCorrection && <div className="canvas-legend-row"><em>Showing proposed correction (preview, not saved).</em></div>}
    </div>}

    <div className="canvas-stage" ref={stageRef} style={{ width, height, transform: `translate3d(${pan.x}px,${pan.y}px,0) scale(${zoom})` }}>
      {assets?.image?.url && !imageError ? <img src={assets.image.url} alt={sampleId ?? 'Sample'} draggable={false} onError={() => setImageError(true)}/> : <div className="canvas-empty" role="status">{imageError ? 'Image could not be loaded' : 'No image asset for this revision'}</div>}
      {!originalOnly && showMask && (assets?.mask_preview?.url ?? assets?.mask?.url) && <img className="canvas-mask" src={assets.mask_preview?.url ?? assets.mask?.url} alt="Mask preview" style={{ opacity: maskOpacity }} draggable={false} onError={() => setImageError(true)}/>}
      {!originalOnly && (showBoxes || showPolygons) && <svg className="canvas-overlay" viewBox={`0 0 ${width} ${height}`} aria-label="Object annotations" onClick={handleStageClick}>{drawOrder.map((obj) => {
        const shape = shapes.get(obj.object_id);
        const b = shape?.box ?? null;
        const state = outcome(obj.object_id);
        const selected = selectedInstanceId === obj.object_id;
        const dn = displayNumberMap.get(obj.object_id) ?? 0;
        const objColor = colorFor(dn);
        const parts = obj.polygons ?? [];
        if (!b && !parts.length) return null;
        const points = parts.reduce((total, part) => total + part.length, 0);
        const geometry = parts.length ? `polygon with ${points} points` : (obj.bbox ? formatBbox(obj.bbox) : 'no geometry');
        const key = obj.source_key?.trim() ? `${obj.source_key}, ` : '';
        return <g key={obj.object_id} role="button" tabIndex={0} aria-pressed={selected} aria-label={`Object ${dn}, ${obj.label}, ${key}${geometry}`} onClick={(e) => { e.stopPropagation(); handleStageClick(e); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); const id = selected ? null : obj.object_id; setSelectedInstanceId(id); onSelectObject?.(id); } }} className={`box-${state}${selected ? ' is-selected' : ''}${someSelected && !selected ? ' is-dimmed' : ''}`}>
          {/* Polygons, with the file's own points. Selected: dark halo, thick outline and a tinted fill. */}
          {showPolygons && parts.map((part, index) => {
            const drawnPoints = part.map((point) => point.join(',')).join(' ');
            return <React.Fragment key={index}>
              {selected && <polygon className="overlay-halo" points={drawnPoints} fill="none" stroke="rgba(10,10,10,0.85)" strokeWidth={7} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
              <polygon className="overlay-outline" points={drawnPoints} fill={selected ? objColor : 'none'} fillOpacity={selected ? 0.22 : undefined} stroke={objColor} strokeWidth={selected ? 4 : 1.5} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            </React.Fragment>;
          })}
          {/* A box is drawn only for an object with no polygon, or for a saved box-check finding. */}
          {showBoxes && b && <rect x={b.left} y={b.top} width={b.width} height={b.height} stroke={objColor} style={{ stroke: objColor, ...(selected ? { fill: objColor, fillOpacity: 0.18, strokeWidth: 4 } : {}) }} strokeDasharray="none" vectorEffect="non-scaling-stroke" />}
        </g>;
      })}
      {/* Labels: number and stable key, placed off every outline */}
      {showLabels && badgePlacements.map((badge) => {
        const objColor = colorFor(badge.displayNumber);
        const txtColor = textColorFor(badge.displayNumber);
        const obj = objects.find((o) => displayNumberMap.get(o.object_id) === badge.displayNumber);
        const selected = obj ? selectedInstanceId === obj.object_id : false;
        const parts = badgeParts.get(badge.displayNumber) ?? { number: badge.text, key: null };
        const numberWidth = badgeWidth(parts.number);
        return <g key={`badge-${badge.displayNumber}`} className={`canvas-badge${selected ? ' is-selected' : ''}${someSelected && !selected ? ' is-dimmed' : ''}`} onClick={(e) => { e.stopPropagation(); if (!obj) return; const id = selected ? null : obj.object_id; setSelectedInstanceId(id); onSelectObject?.(id); }} style={{ cursor: 'pointer' }}>
          {/* Leader line if the label sits away from its object */}
          {badge.leaderTarget && <>
            <line x1={badge.x + badge.width / 2} y1={badge.y + badge.height / 2} x2={badge.leaderTarget.x} y2={badge.leaderTarget.y} stroke="rgba(20,20,20,0.6)" strokeWidth={3} vectorEffect="non-scaling-stroke" />
            <line x1={badge.x + badge.width / 2} y1={badge.y + badge.height / 2} x2={badge.leaderTarget.x} y2={badge.leaderTarget.y} stroke={objColor} strokeWidth={1} vectorEffect="non-scaling-stroke" />
          </>}
          <rect x={badge.x} y={badge.y} width={badge.width} height={badge.height} rx={3} fill={objColor} style={{ fill: objColor }} stroke="rgba(20,20,20,0.7)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          <text x={badge.x + (parts.key ? numberWidth / 2 : badge.width / 2)} y={badge.y + badge.height / 2 + 1} textAnchor="middle" dominantBaseline="central" fill={txtColor} style={{ fill: txtColor, stroke: 'none' }} className="canvas-badge-text">{parts.number}</text>
          {parts.key && <text x={badge.x + numberWidth - 2} y={badge.y + badge.height / 2 + 1} textAnchor="start" dominantBaseline="central" fill={txtColor} style={{ fill: txtColor, stroke: 'none' }} className="canvas-badge-text canvas-badge-key">{parts.key}</text>}
        </g>;
      })}
      </svg>}
    </div>

    {undrawableObjects.length > 0 && <div className="canvas-undisplayable" role="status" aria-label="Objects not drawn on the image">
      {undrawableObjects.map((obj) => {
        const dn = displayNumberMap.get(obj.object_id) ?? 0;
        return <span key={obj.object_id} className="canvas-undisplayable-item">
          <span className="canvas-undisplayable-marker" style={{ background: colorFor(dn), color: textColorFor(dn) }}>#{dn}</span>
          {obj.label}: not drawn, the annotation has no {polygonData ? 'polygon' : 'box or polygon'}
        </span>;
      })}
    </div>}

    <div className="canvas-readout">
      <span ref={cursorRef} className="mono">[X: —, Y: —]</span>
      <span className="mono">{width} × {height} px</span>
      <span className="mono">{continuous ? '0-based continuous' : '1-based inclusive'}</span>
      {someSelected && selectedObject && <span className="mono" role="status" aria-label="Selected annotation">
        Selected: #{displayNumberMap.get(selectedObject.object_id)} {selectedObject.label}{selectedObject.source_key ? ` ${selectedObject.source_key}` : ''}
      </span>}
      {originalOnly && <span className="canvas-mode-tag mono is-original" role="status">Original photo, nothing drawn on it</span>}
      <span className={`canvas-mode-tag mono ${isCorrection ? 'is-correction' : 'is-original'}`} role="status">
        {isCorrection ? 'Proposed correction (preview, not saved)' : 'Original'}
      </span>
    </div>
  </section>;
};
