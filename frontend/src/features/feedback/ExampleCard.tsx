import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import * as Dialog from '@radix-ui/react-dialog';
import { Maximize2, X } from 'lucide-react';
import { apiGet } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { bboxToCss, type ImageSize } from '../../lib/coords';
import { formatTimestamp } from '../../lib/format';
import { outcomeLabel } from '../../lib/labels';
import type { FeedbackExample } from './exampleSelection';

type Box = import('../audits/ReviewCanvas').CanvasObject;
type Evidence = { imageSize: ImageSize; objects: Box[] };

/**
 * The exact sample revision an example was judged on. Never the latest revision
 * or recheck: a missing revision is an error, not a fallback.
 */
function useExactEvidence(example: FeedbackExample) {
  const { dataset_id: datasetId } = example;
  const { sample_id: sampleId, sample_revision: revision } = example.reference;
  return useQuery({
    queryKey: [...queryKeys.datasetSample(datasetId, sampleId, revision), 'example-evidence'],
    queryFn: async (): Promise<Evidence> => {
      const sample = await apiGet('/datasets/{dataset_id}/samples/{sample_id}', { path: { dataset_id: datasetId, sample_id: sampleId }, query: { revision } });
      if (sample.revision !== revision || sample.dataset_id !== datasetId) {
        throw new Error(`The server returned revision r${sample.revision} instead of r${revision}.`);
      }
      const [width, height] = sample.image_size ?? [0, 0];
      if (!(width > 0 && height > 0)) throw new Error(`Revision r${revision} has no recorded image size, so its boxes cannot be placed.`);
      return { imageSize: [width, height], objects: sample.objects };
    },
    staleTime: Infinity, // A stored revision never changes.
  });
}

/** Full, uncropped photo with every annotation box of the revision outlined (1-based inclusive pixels). */
const AnnotatedPhoto: React.FC<{ src: string; alt: string; evidence: Evidence; onImageError: () => void; large?: boolean }> = ({ src, alt, evidence, onImageError, large }) => {
  const [width, height] = evidence.imageSize;
  return (
    <div className={`example-photo${large ? ' is-large' : ''}`} style={{ aspectRatio: `${width} / ${height}` }}>
      <img src={src} alt={alt} loading="lazy" onError={onImageError} />
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label={`${evidence.objects.length} annotation boxes`} role="img">
        {evidence.objects.map((object, index) => {
          const box = object.bbox ? (object.coordinate_system === 'zero_based_continuous' ? { left: object.bbox[0], top: object.bbox[1], width: object.bbox[2] - object.bbox[0], height: object.bbox[3] - object.bbox[1] } : bboxToCss(object.bbox)) : null;
          return (
            <g key={`${object.object_id}-${index}`}>
              {object.polygons?.map((part, i) => <polygon key={i} points={part.map((point) => point.join(',')).join(' ')} fill="none" stroke="currentColor" />)}
              {box && object.bbox_origin !== 'derived_from_polygon' && <rect x={box.left} y={box.top} width={box.width} height={box.height} />}
              <text x={(box?.left ?? 0) + 2} y={Math.max(11, (box?.top ?? 0) - 3)}>#{object.object_id} {object.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
};

interface ExampleCardProps {
  example: FeedbackExample & { example_id: string };
  datasetName: string;
  checked: boolean;
  /** Why the example cannot be selected now; null when it can. */
  blockReason: string | null;
  onToggle: (checked: boolean) => void;
}

export const ExampleCard: React.FC<ExampleCardProps> = ({ example, datasetName, checked, blockReason, onToggle }) => {
  const { reference, target } = example;
  const evidence = useExactEvidence(example);
  const [imageFailed, setImageFailed] = useState(false);
  const [enlarged, setEnlarged] = useState(false);
  const expected = outcomeLabel(example.expected);
  const where = `${reference.sample_id} r${reference.sample_revision}`;
  const ready = evidence.data && !imageFailed;
  const noteId = `example-note-${example.example_id}`;
  const boxCount = evidence.data?.objects.length ?? 0;

  return (
    <article className={`example-card${checked ? ' is-selected' : ''}${blockReason ? ' is-disabled' : ''}`} aria-label={`Example ${where}, rule ${reference.rule_id}`}>
      {evidence.isPending ? (
        <div className="example-evidence-state" role="status">Loading the exact evidence ({where})…</div>
      ) : evidence.error || imageFailed ? (
        <div className="example-evidence-state is-error" role="alert">
          Could not load the exact evidence for {where}{evidence.error ? `: ${evidence.error.message}` : ': the image did not load.'} The photo is not shown without its boxes.
        </div>
      ) : (
        <>
          <AnnotatedPhoto src={example.image_url} alt={`Sample ${where} with its annotation boxes`} evidence={evidence.data} onImageError={() => setImageFailed(true)} />
          <div className="example-photo-actions">
            <button type="button" onClick={() => setEnlarged(true)}><Maximize2 size={12} /> Enlarge</button>
            <Link to={`/audits/${encodeURIComponent(reference.audit_id)}/review?sample=${encodeURIComponent(reference.sample_id)}`}>Open source review →</Link>
          </div>
        </>
      )}

      <div className="example-card-body">
        <p className="example-verdict">Reviewer’s expected result: <b className={`is-${example.expected}`}>{expected}</b></p>
        <p className="example-scope">
          For rule <b className="font-mono">{reference.rule_id}</b>, judged on this image’s whole annotation
          {ready ? (boxCount === 1 ? ' — the 1 outlined box.' : ` — all ${boxCount} outlined boxes.`) : '.'}
          {ready && boxCount === 0 && ' This revision has no annotation boxes.'}
        </p>
        <p className="example-rule">{target.rule.description}</p>
        <p className="example-reason">Saved reason: “{example.reason}”</p>
        <dl className="example-meta">
          <dt>Expected result by</dt><dd>{example.reviewer_id} · {formatTimestamp(example.reviewed_at)}</dd>
          {example.correction_parent_revision !== null && (
            <><dt>Revision</dt><dd>r{reference.sample_revision} corrects r{example.correction_parent_revision}{example.correction_reviewer_id ? ` (by ${example.correction_reviewer_id})` : ''}</dd></>
          )}
          <dt>Dataset</dt><dd>{datasetName}</dd>
          <dt>Rulebook</dt><dd className="font-mono">{target.rulebook_id} v{target.rulebook_version}</dd>
          <dt>Sample</dt><dd className="font-mono">{where}</dd>
          <dt>Audit</dt><dd className="font-mono">{reference.audit_id}</dd>
        </dl>
        <label className="example-use">
          <input type="checkbox" checked={checked} disabled={Boolean(blockReason)} aria-describedby={blockReason ? noteId : undefined} onChange={(event) => onToggle(event.target.checked)} />
          Use as an example
        </label>
        {blockReason && <p id={noteId} className="example-block-reason">{blockReason}</p>}
      </div>

      {ready && (
        <Dialog.Root open={enlarged} onOpenChange={setEnlarged}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 bg-base-pure/80 backdrop-blur-sm z-50" />
            <Dialog.Content className="example-dialog">
              <div className="flex items-start justify-between gap-3">
                <Dialog.Title className="text-sm font-semibold">{where} · rule {reference.rule_id} · expected {expected}</Dialog.Title>
                <Dialog.Close aria-label="Close"><X size={16} /></Dialog.Close>
              </div>
              <Dialog.Description className="text-2xs text-text-muted">The expected result applies to this image’s whole annotation for the rule — every outlined box. Coordinates are 1-based inclusive pixels.</Dialog.Description>
              <AnnotatedPhoto large src={example.image_url} alt={`Sample ${where} with its annotation boxes, enlarged`} evidence={evidence.data} onImageError={() => setImageFailed(true)} />
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}
    </article>
  );
};
