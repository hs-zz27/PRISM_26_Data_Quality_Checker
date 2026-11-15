import React, { useEffect, useId, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { bboxToCss } from '../../lib/coords';
import { explainFinding } from '../../lib/labels';
import type { RegionKind } from '../../lib/findingRegions';
import type { components } from '../../api/generated/openapi';
import { edgeSentence, REGION_STYLES, type CloseUpRegion, type Side } from './closeUpRegions';
import type { CanvasObject } from './ReviewCanvas';

type Finding = components['schemas']['Finding'];

interface ErrorCloseUpProps {
  finding: Finding;
  /** From closeUpRegions(); the panel shows one at a time. */
  regions: CloseUpRegion[];
  /** The stored original photo. It is only displayed, through a window; it is never edited or saved. */
  imageUrl: string | null | undefined;
  imageSize: readonly [number, number];
  objects: CanvasObject[];
  annotationFormat?: 'penn_fudan' | 'coco' | 'labelme';
  onClose: () => void;
  id?: string;
}

const KINDS: RegionKind[] = ['missing', 'extra', 'mismatch', 'unannotated'];
const OWN_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape']);

/** Fill patterns, one per kind of problem. `unit` is the pattern tile in the SVG's own units. */
function Patterns({ prefix, unit }: { prefix: string; unit: number }) {
  const line = unit / 6;
  return <defs>
    {KINDS.map((kind) => {
      const { pattern, color } = REGION_STYLES[kind];
      const shared = { stroke: color, strokeWidth: line };
      return <pattern key={kind} id={`${prefix}-${pattern}`} patternUnits="userSpaceOnUse" width={unit} height={unit}>
        <rect width={unit} height={unit} fill={color} fillOpacity={0.16} />
        {pattern === 'diagonal' && <path d={`M0,${unit} L${unit},0`} {...shared} />}
        {pattern === 'dots' && <circle cx={unit / 2} cy={unit / 2} r={unit / 6} fill={color} />}
        {pattern === 'cross' && <path d={`M0,0 L${unit},${unit} M0,${unit} L${unit},0`} {...shared} />}
        {pattern === 'horizontal' && <path d={`M0,${unit / 2} L${unit},${unit / 2}`} {...shared} />}
      </pattern>;
    })}
  </defs>;
}

/**
 * The outline of one annotation in image pixels: its polygon, or its box when it has no polygon. A problem
 * measured on the box (older audits of data that has both) is shown against the box.
 */
function Outline({ obj, preferBox }: { obj: CanvasObject; preferBox: boolean }) {
  const lines = { fill: 'none', vectorEffect: 'non-scaling-stroke' as const, strokeLinejoin: 'round' as const };
  const pair = (render: (extra: Record<string, unknown>, key: string) => React.ReactNode, key: string) => <React.Fragment key={key}>
    {render({ ...lines, stroke: 'rgba(10,10,10,0.85)', strokeWidth: 4, className: 'close-up-outline-halo' }, 'halo')}
    {render({ ...lines, stroke: '#ffffff', strokeWidth: 1.5, className: 'close-up-outline' }, 'line')}
  </React.Fragment>;
  const useBox = preferBox && obj.bbox != null && obj.bbox_origin !== 'derived_from_polygon';
  if (obj.polygons?.length && !useBox) {
    return <>{obj.polygons.map((part, index) => {
      const points = part.map((point) => point.join(',')).join(' ');
      return pair((extra, key) => <polygon key={key} points={points} {...extra} />, String(index));
    })}</>;
  }
  if (!obj.bbox) return null;
  const box = obj.coordinate_system === 'zero_based_continuous'
    ? { left: obj.bbox[0], top: obj.bbox[1], width: obj.bbox[2] - obj.bbox[0], height: obj.bbox[3] - obj.bbox[1] }
    : bboxToCss(obj.bbox);
  return pair((extra, key) => <rect key={key} x={box.left} y={box.top} width={box.width} height={box.height} {...extra} />, 'box');
}

/**
 * A cropped close-up of one problem region, shown beside its finding.
 *
 * The crop is the original photo seen through a padded window around the region. The region, the annotation
 * outline and the image-edge markers are drawn over it here in the browser; the photo and the annotation
 * file are not changed.
 */
export const ErrorCloseUp: React.FC<ErrorCloseUpProps> = ({ finding, regions, imageUrl, imageSize, objects, onClose, id }) => {
  const [index, setIndex] = useState(0);
  const panelRef = useRef<HTMLElement>(null);
  const prefix = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const count = regions.length;
  const current = regions[Math.min(index, count - 1)];

  useEffect(() => { setIndex(0); }, [finding.finding_id]);
  useEffect(() => { panelRef.current?.focus({ preventScroll: true }); }, [finding.finding_id]);

  if (!current) return null;
  const { window: view, region, title, detail } = current;
  const style = REGION_STYLES[region.kind];
  const step = (to: number) => setIndex(Math.max(0, Math.min(count - 1, to)));
  const position = Math.min(index, count - 1);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!OWN_KEYS.has(event.key) || event.ctrlKey || event.metaKey || event.altKey) return;
    // These keys belong to the close-up while it has the focus; the page's own shortcuts must not also fire.
    event.preventDefault();
    event.stopPropagation();
    event.nativeEvent.stopPropagation();
    if (event.key === 'Escape') onClose();
    else if (event.key === 'ArrowRight') step(position + 1);
    else if (event.key === 'ArrowLeft') step(position - 1);
    else if (event.key === 'Home') step(0);
    else step(count - 1);
  };

  const unit = 1 / view.scale; // one screen pixel, in image pixels
  const width = view.x1 - view.x0;
  const height = view.y1 - view.y0;
  const [left, top, right, bottom] = view.region;
  // An unannotated person has no outline of their own: show every annotation, so its absence is visible.
  const outlined = finding.object_id == null ? objects : objects.filter((obj) => obj.object_id === finding.object_id);
  const wordAbove = top - view.y0 > 18 * unit;
  const edgeLine: Record<Side, [number, number, number, number]> = {
    left: [view.x0, view.y0, view.x0, view.y1], right: [view.x1, view.y0, view.x1, view.y1],
    top: [view.x0, view.y0, view.x1, view.y0], bottom: [view.x0, view.y1, view.x1, view.y1],
  };
  const edges = edgeSentence(view);
  const sentence = explainFinding(finding);

  return <section ref={panelRef} id={id} className="error-close-up" role="region" aria-label="Error close-up" tabIndex={-1}
    onKeyDown={onKeyDown} onClick={(event) => event.stopPropagation()}>
    <header className="error-close-up-head">
      <span className="finding-section-label">Error close-up</span>
      {count > 1 && <span className="error-close-up-steps">
        <button type="button" aria-label="Previous region" title="Previous region (Left arrow)" disabled={position === 0} onClick={() => step(position - 1)}><ChevronLeft size={13} aria-hidden="true" /></button>
        <span aria-live="polite">Region {position + 1} of {count}</span>
        <button type="button" aria-label="Next region" title="Next region (Right arrow)" disabled={position === count - 1} onClick={() => step(position + 1)}><ChevronRight size={13} aria-hidden="true" /></button>
      </span>}
      <button type="button" className="error-close-up-close" aria-label="Close the error close-up" title="Close (Esc)" onClick={onClose}><X size={13} aria-hidden="true" /></button>
    </header>

    <p className="error-close-up-sentence">{sentence}</p>

    {imageUrl
      ? <svg className="close-up-crop" role="img" viewBox={`${view.x0} ${view.y0} ${width} ${height}`} width={view.outputWidth} height={view.outputHeight}
          aria-label={`Close-up of the photo. ${title}${detail ? `. ${detail}` : ''}${edges ? `. ${edges}` : ''}`}>
          <Patterns prefix={prefix} unit={8 * unit} />
          <image href={imageUrl} x={0} y={0} width={imageSize[0]} height={imageSize[1]} preserveAspectRatio="none" />
          {outlined.map((obj) => <Outline key={obj.object_id} obj={obj} preferBox={region.geometry === 'box'} />)}
          <rect className="close-up-region" data-kind={region.kind} x={left} y={top} width={right - left} height={bottom - top}
            fill={`url(#${prefix}-${style.pattern})`} stroke={style.color} strokeWidth={2.5} strokeDasharray={style.dash} vectorEffect="non-scaling-stroke" />
          <text className="close-up-word" x={left} y={wordAbove ? top - 5 * unit : Math.min(bottom + 14 * unit, view.y1 - 4 * unit)}
            fontSize={12 * unit} fill={style.color} stroke="rgba(10,10,10,0.9)" strokeWidth={3 * unit} paintOrder="stroke">{style.word}</text>
          {view.imageEdges.map((side) => {
            const [x1, y1, x2, y2] = edgeLine[side];
            return <line key={side} className="close-up-edge" data-side={side} x1={x1} y1={y1} x2={x2} y2={y2}
              stroke="#ffffff" strokeWidth={6} strokeDasharray="2 6" vectorEffect="non-scaling-stroke"><title>{`${side} edge of the photo`}</title></line>;
          })}
        </svg>
      : <p className="error-close-up-missing" role="status">The photo is not available, so the close-up cannot be drawn.</p>}

    <p className="error-close-up-caption">
      <b>{title}</b>{detail && <span> · {detail}</span>}
      {edges && <span className="error-close-up-edge-note"> {edges}</span>}
    </p>

    <ul className="error-close-up-legend" aria-label="What the markings mean">
      {KINDS.map((kind) => <li key={kind} className={kind === region.kind ? 'is-current' : undefined}>
        <svg width={18} height={12} aria-hidden="true">
          <Patterns prefix={`${prefix}-legend`} unit={6} />
          <rect x={1} y={1} width={16} height={10} fill={`url(#${prefix}-legend-${REGION_STYLES[kind].pattern})`} stroke={REGION_STYLES[kind].color} strokeWidth={1.5} strokeDasharray={REGION_STYLES[kind].dash === 'none' ? undefined : '3 2'} />
        </svg>
        <b>{REGION_STYLES[kind].word}</b> {REGION_STYLES[kind].meaning}
      </li>)}
      <li><svg width={18} height={12} aria-hidden="true"><path d="M1,6 L17,6" stroke="rgba(10,10,10,0.85)" strokeWidth={4} /><path d="M1,6 L17,6" stroke="#ffffff" strokeWidth={1.5} /></svg>Annotation outline, as the file has it</li>
    </ul>
    <p className="error-close-up-note">Small differences are accepted. Only a noticeable one is marked.</p>
    <p className="error-close-up-note">Shown only here. The photo and the annotation file are not changed.</p>
  </section>;
};
