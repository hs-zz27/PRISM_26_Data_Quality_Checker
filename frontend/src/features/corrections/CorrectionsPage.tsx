import React, { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiGet, apiGetAllPages, apiPost, apiPostForm, ApiError } from '../../api/client';
import { useReviewerStore } from '../../store/reviewerStore';
import { Button } from '../../components/ui/Button';
import { useJobPolling } from '../../hooks/useJobPolling';
import { bboxToCss, translateBboxOnCanvas } from '../../lib/coords';

export const CorrectionsPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { reviewerId, openPrompt } = useReviewerStore();
  const [datasetId, setDatasetId] = useState('');
  const [sampleId, setSampleId] = useState('');
  const [reason, setReason] = useState('');
  const [objects, setObjects] = useState<Array<{ object_id: number; label: string; bbox: [number, number, number, number] }>>([]);
  const [mask, setMask] = useState<File | null>(null);
  const [error, setError] = useState('');
  const [jobId, setJobId] = useState<string | null>(null);
  const [auditId, setAuditId] = useState('');
  const imageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ id: number; x: number; y: number; bbox: [number, number, number, number] } | null>(null);
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }) });
  const samplesQuery = useQuery({ queryKey: ['dataset-samples', datasetId], queryFn: () => apiGetAllPages('/datasets/{dataset_id}/samples', { path: { dataset_id: datasetId }, query: { limit: 100, offset: 0 } }), enabled: !!datasetId });
  const sampleQuery = useQuery({ queryKey: ['dataset-sample', datasetId, sampleId], queryFn: () => apiGet('/datasets/{dataset_id}/samples/{sample_id}', { path: { dataset_id: datasetId, sample_id: sampleId } }), enabled: !!datasetId && !!sampleId });
  const revisionsQuery = useQuery({ queryKey: ['revisions', datasetId, sampleId], queryFn: () => apiGetAllPages('/datasets/{dataset_id}/samples/{sample_id}/revisions', { path: { dataset_id: datasetId, sample_id: sampleId }, query: { limit: 100, offset: 0 } }), enabled: !!datasetId && !!sampleId });
  useEffect(() => { if (sampleQuery.data) setObjects(sampleQuery.data.objects.map((item) => ({ ...item, bbox: [...item.bbox] as [number, number, number, number] }))); }, [sampleQuery.data]);
  const correction = useMutation({
    mutationFn: async () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Reviewer ID required.'); }
      if (!sampleQuery.data) throw new Error('Select a sample before saving.');
      if (!reason.trim()) throw new Error('Enter a revision reason.');
      let mask_upload_id: string | undefined;
      if (mask) {
        const uploaded = await apiPostForm('/datasets/{dataset_id}/samples/{sample_id}/mask-uploads', { path: { dataset_id: datasetId, sample_id: sampleId }, body: { mask, expected_revision: sampleQuery.data.revision } });
        mask_upload_id = uploaded.mask_upload_id;
      }
      return apiPost('/datasets/{dataset_id}/samples/{sample_id}/revisions', { path: { dataset_id: datasetId, sample_id: sampleId }, body: { expected_revision: sampleQuery.data.revision, reviewer_id: reviewerId.trim(), reason: reason.trim(), objects, mask_upload_id: mask_upload_id ?? null, source_audit_id: null } });
    },
    onSuccess: () => { setError('Correction saved.'); setMask(null); void sampleQuery.refetch(); void revisionsQuery.refetch(); void queryClient.invalidateQueries({ queryKey: ['dataset-samples', datasetId] }); },
    onError: (err) => setError(err instanceof ApiError && err.status === 409 ? 'Revision conflict (409). Your edits are preserved. Reload the current sample, then reapply the changes.' : err.message),
  });
  const recheck = useMutation({ mutationFn: () => {
    if (!reviewerId.trim()) { openPrompt(); throw new Error('Reviewer ID required.'); }
    if (!auditId.trim()) throw new Error('Select an audit ID.');
    if (!sampleQuery.data) throw new Error('Select a sample.');
    return apiPost('/audits/{audit_id}/rechecks', { path: { audit_id: auditId.trim() }, body: { sample_ids: [sampleId], sample_revisions: { [sampleId]: sampleQuery.data.revision }, reviewer_id: reviewerId.trim() } });
  }, onSuccess: (result) => setJobId(result.job.job_id), onError: (err) => setError(err.message) });
  const job = useJobPolling(jobId);
  const updateObject = (index: number, field: 'label' | 'object_id' | 0 | 1 | 2 | 3, value: string) => setObjects((old) => old.map((obj, i) => i === index ? field === 'label' ? { ...obj, label: value } : field === 'object_id' ? { ...obj, object_id: Number(value) } : { ...obj, bbox: obj.bbox.map((n, j) => j === field ? Number(value) : n) as [number, number, number, number] } : obj));
  return <div className="workstation-page corrections-page p-6 max-w-5xl mx-auto space-y-4"><h1 className="text-xl font-semibold">Annotation Corrections & Rechecks</h1>
    <div className="flex gap-3"><select value={datasetId} onChange={(e) => { setDatasetId(e.target.value); setSampleId(''); }}><option value="">Select dataset</option>{datasetsQuery.data?.items.map((d) => <option key={d.dataset_id} value={d.dataset_id}>{d.name}</option>)}</select><select value={sampleId} onChange={(e) => setSampleId(e.target.value)}><option value="">Select sample</option>{samplesQuery.data?.items.map((s) => <option key={s.sample_id} value={s.sample_id}>{s.sample_id} · r{s.revision}</option>)}</select></div>
    {(datasetsQuery.error || samplesQuery.error || sampleQuery.error || revisionsQuery.error) && <p role="alert">{String(datasetsQuery.error ?? samplesQuery.error ?? sampleQuery.error ?? revisionsQuery.error)}</p>}
    {!datasetId && <div className="correction-empty">Select a dataset, then choose a sample revision to inspect its image and full annotation set.</div>}
    {sampleQuery.data?.assets?.image?.url && <div ref={imageRef} className="correction-image" style={{ aspectRatio: sampleQuery.data.image_size?.join('/') ?? '4 / 3' }}><img src={sampleQuery.data.assets.image.url} alt={sampleId} /><svg viewBox={`0 0 ${sampleQuery.data.image_size?.[0] ?? 640} ${sampleQuery.data.image_size?.[1] ?? 480}`} aria-label="Current annotation boxes" onPointerMove={(event) => { const drag = dragRef.current; const bounds = imageRef.current?.getBoundingClientRect(); if (!drag || !bounds || !sampleQuery.data?.image_size) return; const bbox = translateBboxOnCanvas(drag.bbox, event.clientX - drag.x, event.clientY - drag.y, [bounds.width, bounds.height], sampleQuery.data.image_size); setObjects((old) => old.map((obj) => obj.object_id === drag.id ? { ...obj, bbox } : obj)); }} onPointerUp={() => { dragRef.current = null; }} onPointerCancel={() => { dragRef.current = null; }}>{objects.map((obj) => { const box = bboxToCss(obj.bbox); return <g key={obj.object_id}><rect x={box.left} y={box.top} width={box.width} height={box.height} onPointerDown={(event) => { const bounds = imageRef.current?.getBoundingClientRect(); if (!bounds) return; dragRef.current = { id: obj.object_id, x: event.clientX, y: event.clientY, bbox: obj.bbox }; event.currentTarget.setPointerCapture(event.pointerId); }} /><text x={box.left + 2} y={Math.max(12, box.top - 3)}>#{obj.object_id} {obj.label}</text></g>; })}</svg>{sampleQuery.data.assets.mask_preview?.url && <img className="correction-mask" src={sampleQuery.data.assets.mask_preview.url} alt="Revision-pinned mask preview" />}</div>}
    {sampleQuery.data && <div className="text-2xs text-text-muted font-mono">Revision-pinned image and mask preview · drag a box to reposition · coordinates remain 1-based inclusive</div>}
    {sampleQuery.isLoading && <p>Loading selected sample…</p>}
    {sampleQuery.data && <section className="space-y-3"><h2>Complete object list · source revision r{sampleQuery.data.revision}</h2>{objects.map((obj, i) => <div key={`${obj.object_id}-${i}`} className="flex flex-wrap gap-2"><input aria-label={`Object ${i + 1} label`} value={obj.label} onChange={(e) => updateObject(i, 'label', e.target.value)} /><input aria-label={`Object ${i + 1} ID`} type="number" value={obj.object_id} onChange={(e) => updateObject(i, 'object_id', e.target.value)} />{obj.bbox.map((n, j) => <input key={j} aria-label={`Object ${i + 1} coordinate ${j + 1}`} type="number" value={n} onChange={(e) => updateObject(i, j as 0 | 1 | 2 | 3, e.target.value)} />)}<Button variant="ghost" onClick={() => setObjects((old) => old.filter((_, index) => index !== i))}>Remove</Button></div>)}<Button variant="secondary" onClick={() => setObjects((old) => [...old, { object_id: old.reduce((max, obj) => Math.max(max, obj.object_id), 0) + 1, label: '', bbox: [1, 1, 1, 1] }])}>Add object</Button>
    <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Revision reason" /><input type="file" accept="image/*" onChange={(e) => setMask(e.target.files?.[0] ?? null)} /><Button disabled={correction.isPending} onClick={() => correction.mutate()}>Save complete replacement</Button><Button variant="secondary" disabled={!sampleId} onClick={() => void sampleQuery.refetch()}>Reload current revision</Button></section>}
    <section className="space-y-2"><h2>Revision history</h2>{revisionsQuery.data?.items.map((revision) => <div key={revision.revision}>r{revision.revision} · {revision.reason ?? 'No reason'} · {revision.reviewer_id ?? 'Unknown reviewer'}</div>)}</section>
    <div className="flex gap-2"><input value={auditId} onChange={(e) => setAuditId(e.target.value)} placeholder="Audit ID for recheck" /><Button disabled={recheck.isPending} onClick={() => recheck.mutate()}>Recheck this sample</Button></div>{job.job && <p>Recheck job {job.job.job_id}: {job.job.status}</p>}{error && <p role="status">{error}</p>}
  </div>;
};
