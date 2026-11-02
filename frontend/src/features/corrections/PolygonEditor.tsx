import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { Button } from '../../components/ui/Button';
import type { JsonObject } from './jsonGeometry';
import { imagePoint, insertVertex, moveVertex, removeVertex, vertices, type Point, type Vertex } from './polygonEditing';

interface Props {
  objects: JsonObject[];
  savedObjects: JsonObject[];
  imageUrl: string;
  imageSize: Point;
  sampleId: string;
  disabled?: boolean;
  onChange: (objects: JsonObject[]) => void;
}

export function PolygonEditor({ objects, savedObjects, imageUrl, imageSize, sampleId, disabled = false, onChange }: Props) {
  const [selected, setSelected] = useState<Vertex | null>(null);
  const [zoom, setZoom] = useState(1);
  const [showSaved, setShowSaved] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [past, setPast] = useState<JsonObject[][]>([]);
  const [future, setFuture] = useState<JsonObject[][]>([]);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ vertex: Vertex; before: JsonObject[]; latest: JsonObject[]; pointerId: number } | null>(null);
  const polygonObjects = objects.filter((object) => object.polygons?.length);
  const current = objects.find((object) => object.source_key === selected?.key);
  const point = selected && current?.polygons?.[selected.part]?.[selected.point];
  const locked = disabled || imageError;

  const remember = (before: JsonObject[]) => { setPast((items) => [...items.slice(-49), before]); setFuture([]); };
  const commit = (change: () => JsonObject[], nextSelection = selected) => {
    if (locked || drag.current) return;
    try {
      const next = change();
      if (JSON.stringify(next) !== JSON.stringify(objects)) { remember(objects); onChange(next); }
      setSelected(nextSelection); setError(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The point could not be changed.'); }
  };

  const start = (event: PointerEvent<SVGCircleElement>, vertex: Vertex) => {
    if (locked || event.button !== 0 || drag.current) return;
    event.preventDefault(); event.stopPropagation();
    setSelected(vertex); setError(null);
    drag.current = { vertex, before: objects, latest: objects, pointerId: event.pointerId };
    svg.current?.setPointerCapture(event.pointerId);
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId || locked || !svg.current) return;
    const position = imagePoint([event.clientX, event.clientY], svg.current.getBoundingClientRect(), imageSize);
    if (!position) return;
    try {
      const next = moveVertex(active.before, active.vertex, position, imageSize);
      active.latest = next;
      onChange(next); setError(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The point could not be moved.'); }
  };
  const finish = (cancel: boolean) => {
    const active = drag.current;
    if (!active) return;
    drag.current = null;
    if (cancel) onChange(active.before);
    else if (JSON.stringify(active.before) !== JSON.stringify(active.latest)) remember(active.before);
    if (svg.current?.hasPointerCapture(active.pointerId)) svg.current.releasePointerCapture(active.pointerId);
  };
  const keyDown = (event: KeyboardEvent<SVGCircleElement>, vertex: Vertex) => {
    const offsets: Record<string, Point> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const delta = offsets[event.key];
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(vertex); }
    if (!delta || locked) return;
    event.preventDefault(); event.stopPropagation();
    const value = objects.find((object) => object.source_key === vertex.key)?.polygons?.[vertex.part]?.[vertex.point];
    if (!value) return;
    const step = event.shiftKey ? 10 : 1;
    commit(() => moveVertex(objects, vertex, [value[0]! + delta[0] * step, value[1]! + delta[1] * step], imageSize), vertex);
  };

  return <section className="polygon-editor space-y-2" aria-label="Visual polygon editor">
    <div className="flex flex-wrap items-center gap-2">
      <label className="text-xs">Outline <select aria-label="Polygon object" value={selected?.key ?? ''} disabled={locked}
        onChange={(event) => { setSelected(event.target.value ? { key: event.target.value, part: 0, point: 0 } : null); setError(null); }}>
        <option value="">Select an outline</option>
        {polygonObjects.map((object) => <option key={object.source_key} value={object.source_key}>#{object.object_id} {object.label}</option>)}
      </select></label>
      <Button size="sm" onClick={() => setZoom((value) => Math.max(1, value / 1.5))} aria-label="Zoom out polygon editor">−</Button>
      <span className="text-xs">{Math.round(zoom * 100)}%</span>
      <Button size="sm" onClick={() => setZoom((value) => Math.min(8, value * 1.5))} aria-label="Zoom in polygon editor">+</Button>
      <Button size="sm" onClick={() => setZoom(1)}>Fit width</Button>
      <Button size="sm" disabled={locked || !past.length} onClick={() => {
        const previous = past[past.length - 1]; if (!previous) return;
        setFuture((items) => [...items, objects]); setPast((items) => items.slice(0, -1)); onChange(previous); setSelected(null); setError(null);
      }}>Undo</Button>
      <Button size="sm" disabled={locked || !future.length} onClick={() => {
        const next = future[future.length - 1]; if (!next) return;
        setPast((items) => [...items, objects]); setFuture((items) => items.slice(0, -1)); onChange(next); setSelected(null); setError(null);
      }}>Redo</Button>
      <label className="text-xs"><input type="checkbox" checked={showSaved} onChange={(event) => setShowSaved(event.target.checked)} /> Show saved outline</label>
    </div>
    <div className="polygon-viewport">
      <div className="polygon-stage" style={{ width: `${zoom * 100}%`, aspectRatio: `${imageSize[0]} / ${imageSize[1]}` }}>
        <img src={imageUrl} alt={sampleId} draggable={false} onError={() => setImageError(true)} />
        <svg ref={svg} viewBox={`0 0 ${imageSize[0]} ${imageSize[1]}`} preserveAspectRatio="none"
          aria-label="Editable polygon outlines" onPointerMove={move}
          onPointerUp={(event) => { if (drag.current?.pointerId === event.pointerId) finish(false); }}
          onPointerCancel={(event) => { if (drag.current?.pointerId === event.pointerId) finish(true); }}
          onLostPointerCapture={(event) => { if (drag.current?.pointerId === event.pointerId) finish(true); }}>
          {showSaved && savedObjects.flatMap((object) => (object.polygons ?? []).map((part, index) =>
            <polygon key={`saved-${object.source_key}-${index}`} className="polygon-saved" points={part.map((p) => p.join(',')).join(' ')} />))}
          {objects.map((object) => <g key={object.source_key}>
            {object.bbox_origin === 'supplied' && object.bbox && <rect className="polygon-supplied-box"
              x={object.bbox[0]} y={object.bbox[1]} width={object.bbox[2] - object.bbox[0]} height={object.bbox[3] - object.bbox[1]} />}
            {(object.polygons ?? []).map((part, partIndex) => <g key={partIndex}>
              <polygon className={`polygon-outline${selected?.key === object.source_key ? ' is-selected' : ''}`}
                points={part.map((p) => p.join(',')).join(' ')} role="button" tabIndex={locked ? -1 : 0}
                aria-label={`Select object ${object.object_id} polygon ${partIndex + 1}`} aria-disabled={locked}
                onClick={() => { if (!locked) setSelected({ key: object.source_key, part: partIndex, point: 0 }); }}
                onKeyDown={(event) => { if (!locked && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); setSelected({ key: object.source_key, part: partIndex, point: 0 }); } }} />
              {selected?.key === object.source_key && vertices(part).map((vertex, index) => <circle key={index}
                className={`polygon-vertex${selected.part === partIndex && selected.point === index ? ' is-selected' : ''}`}
                cx={vertex[0]} cy={vertex[1]} r={Math.max(2, imageSize[0] / (140 * zoom))} role="button" tabIndex={locked ? -1 : 0}
                aria-label={`Object ${object.object_id} polygon ${partIndex + 1} point ${index + 1}`} aria-disabled={locked}
                onPointerDown={(event) => start(event, { key: object.source_key, part: partIndex, point: index })}
                onKeyDown={(event) => keyDown(event, { key: object.source_key, part: partIndex, point: index })}
                onFocus={() => setSelected({ key: object.source_key, part: partIndex, point: index })} />)}
            </g>)}
          </g>)}
        </svg>
      </div>
    </div>
    {imageError && <p role="alert">The image could not be loaded. Visual editing is disabled.</p>}
    {point && selected && <div className="flex flex-wrap items-center gap-2 text-xs">
      <span>Polygon {selected.part + 1}, point {selected.point + 1}</span>
      {(['X', 'Y'] as const).map((axis, index) => <label key={axis}>{axis} <input aria-label={`Selected point ${axis}`} type="number" step="any"
        min={0} max={imageSize[index]} value={point[index]} disabled={locked} className="w-24"
        onChange={(event) => {
          if (!event.target.value || !Number.isFinite(event.target.valueAsNumber)) return;
          const next: Point = [point[0]!, point[1]!]; next[index] = event.target.valueAsNumber;
          commit(() => moveVertex(objects, selected, next, imageSize));
        }} /></label>)}
      <Button size="sm" disabled={locked} onClick={() => commit(() => insertVertex(objects, selected), { ...selected, point: selected.point + 1 })}>Add point after selected</Button>
      <Button size="sm" disabled={locked || vertices(current!.polygons![selected.part]!).length <= 3}
        onClick={() => commit(() => removeVertex(objects, selected), { ...selected, point: 0 })}>Remove selected point</Button>
    </div>}
    {error && <p role="alert" className="text-xs text-status-fail">{error}</p>}
    {!polygonObjects.length && <p className="text-xs">This sample has no editable polygons. Use the annotation fields for its supplied boxes.</p>}
    <p className="text-2xs text-text-muted">Select an outline, then drag its points. Arrow keys move a focused point by 1 pixel; Shift moves it by 10. Zoom and scroll to inspect details. Cyan is the edited outline; dashed white is the saved outline; yellow is a supplied box.</p>
  </section>;
}
