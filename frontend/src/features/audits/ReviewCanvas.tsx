import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Eye, EyeOff, Maximize2, Minus, Plus } from 'lucide-react';
import { useCanvasStore } from '../../store/canvasStore';
import { bboxToCss, cssToImageCoord, formatBbox, formatCursorPos, type BBox1Based } from '../../lib/coords';
import type { components } from '../../api/generated/openapi';
import { isTypingTarget } from '../../lib/keyboard';

type Finding = components['schemas']['Finding'];
export interface CanvasObject { object_id: number; label: string; bbox: BBox1Based | null; coordinate_system?: 'zero_based_continuous'; polygons?: number[][][]; bbox_origin?: string | null }
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
}

export const ReviewCanvas: React.FC<ReviewCanvasProps> = ({ sampleId, imageSize, assets, objects = [], findings, onSelectObject, hiddenObjectIds = [], annotationFormat }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const cursorRef = useRef<HTMLSpanElement>(null);
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const panModeRef = useRef(false);
  const [imageError, setImageError] = useState(false);
  const { showMask, showBoxes, maskOpacity, selectedInstanceId, zoom, pan, toggleMask, toggleBoxes, setMaskOpacity, setSelectedInstanceId, setZoom, setPan } = useCanvasStore();
  const continuous = annotationFormat === 'coco' || annotationFormat === 'labelme' || objects.some((obj) => obj.coordinate_system === 'zero_based_continuous');
  const width = imageSize?.[0] ?? 640;
  const height = imageSize?.[1] ?? 480;
  const handleFit = useCallback(() => {
    if (!containerRef.current || !imageSize) return;
    const r = containerRef.current.getBoundingClientRect();
    const scale = Math.min((r.width - 40) / width, (r.height - 80) / height, 1);
    setZoom(Math.max(0.05, scale)); setPan({ x: 0, y: 0 });
  }, [imageSize, setPan, setZoom, width, height]);
  useEffect(() => { setImageError(false); handleFit(); }, [sampleId, handleFit]);
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => { if (!isTypingTarget(e) && e.code === 'Space' && !e.repeat) { panModeRef.current = true; e.preventDefault(); } };
    const onKeyUp = (e: KeyboardEvent) => { if (e.code === 'Space') panModeRef.current = false; };
    window.addEventListener('keydown', onKeyDown); window.addEventListener('keyup', onKeyUp);
    return () => { window.removeEventListener('keydown', onKeyDown); window.removeEventListener('keyup', onKeyUp); };
  }, []);
  const updateZoom = (factor: number) => setZoom(Math.min(8, Math.max(0.05, zoom * factor)));
  const outcome = (id: number) => findings.find((f) => f.object_id === id)?.outcome ?? 'unavailable';
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
  return <section ref={containerRef} className="review-canvas" aria-label="Image review canvas" onWheel={(e) => { e.preventDefault(); updateZoom(e.deltaY < 0 ? 1.12 : 0.89); }} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
    <div className="canvas-toolbar">
      <button title="Zoom out" aria-label="Zoom out" onClick={() => updateZoom(1 / 1.2)}><Minus size={14}/></button><span className="mono">{Math.round(zoom * 100)}%</span><button title="Zoom in" aria-label="Zoom in" onClick={() => updateZoom(1.2)}><Plus size={14}/></button>
      <button title="Fit image (F)" aria-label="Fit image" onClick={handleFit}><Maximize2 size={14}/></button><i/>
      <button className={showBoxes ? 'is-on' : ''} title="Toggle boxes (B)" aria-label="Toggle boxes" onClick={toggleBoxes}>B</button>
      <button className={showMask ? 'is-on' : ''} title="Toggle mask (M)" aria-label="Toggle mask" onClick={toggleMask}>{showMask ? <Eye size={14}/> : <EyeOff size={14}/>}</button>
      {showMask && <label className="mask-opacity">Mask <input aria-label="Mask opacity" type="range" min="0" max="1" step="0.05" value={maskOpacity} onChange={(e) => setMaskOpacity(Number(e.target.value))}/></label>}
      <span className="canvas-shortcut">Space + drag to pan</span>
    </div>
    <div className="canvas-stage" ref={stageRef} style={{ width, height, transform: `translate3d(${pan.x}px,${pan.y}px,0) scale(${zoom})` }}>
      {assets?.image?.url && !imageError ? <img src={assets.image.url} alt={sampleId ?? 'Sample'} draggable={false} onError={() => setImageError(true)}/> : <div className="canvas-empty" role="status">{imageError ? 'Image could not be loaded' : 'No image asset for this revision'}</div>}
      {showMask && (assets?.mask_preview?.url ?? assets?.mask?.url) && <img className="canvas-mask" src={assets.mask_preview?.url ?? assets.mask?.url} alt="Mask preview" style={{ opacity: maskOpacity }} draggable={false} onError={() => setImageError(true)}/>}
      {showBoxes && <svg className="canvas-overlay" viewBox={`0 0 ${width} ${height}`} aria-label="Object boxes">{objects.filter((object) => !hiddenObjectIds.includes(object.object_id)).map((obj) => {
        const b = obj.bbox ? (obj.coordinate_system === 'zero_based_continuous' ? { left: obj.bbox[0], top: obj.bbox[1], width: obj.bbox[2] - obj.bbox[0], height: obj.bbox[3] - obj.bbox[1] } : bboxToCss(obj.bbox)) : null; const state = outcome(obj.object_id); const selected = selectedInstanceId === obj.object_id;
        return <g key={obj.object_id} role="button" tabIndex={0} aria-label={`Object ${obj.object_id}, ${obj.label}, ${obj.bbox ? formatBbox(obj.bbox) : "no box"}`} onClick={(e) => { e.stopPropagation(); const id = selected ? null : obj.object_id; setSelectedInstanceId(id); onSelectObject?.(id); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); const id = selected ? null : obj.object_id; setSelectedInstanceId(id); onSelectObject?.(id); } }} className={`box-${state}${selected ? ' is-selected' : ''}`}>
          {obj.polygons?.map((part, index) => <polygon key={index} points={part.map((point) => point.join(',')).join(' ')} fill="none" stroke="currentColor" strokeWidth={selected ? 2.5 : 1.5} />)}
          {b && <>{obj.bbox_origin !== 'derived_from_polygon' && <rect x={b.left} y={b.top} width={b.width} height={b.height}/>}<text x={b.left + 2} y={Math.max(12,b.top - 3)}>#{obj.object_id} {obj.label}</text></>}
        </g>;
      })}</svg>}
    </div>
    <div className="canvas-readout"><span ref={cursorRef} className="mono">[X: —, Y: —]</span><span className="mono">{width} × {height} px</span><span className="mono">{continuous ? '0-based continuous' : '1-based inclusive'}</span></div>
  </section>;
};
