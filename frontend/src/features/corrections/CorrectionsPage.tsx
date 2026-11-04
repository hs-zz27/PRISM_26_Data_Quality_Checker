import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { apiGet, apiGetAllPages, apiPost, apiPostForm, ApiError } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { useReviewerStore } from '../../store/reviewerStore';
import { Button } from '../../components/ui/Button';
import { useJobPolling } from '../../hooks/useJobPolling';
import { RECHECK_IN_PROGRESS_MESSAGE, useImageRecheckStatus } from '../../hooks/useImageRecheckStatus';
import { bboxToCss, defaultNewBbox, isBboxValid, resizeBboxOnCanvas, translateBboxOnCanvas, type BBox1Based, type ResizeHandle } from '../../lib/coords';
import { formatTimestamp } from '../../lib/format';
import { jobStatusLabel } from '../../lib/labels';
import { parseJsonGeometry, type JsonObject } from './jsonGeometry';
import { PolygonEditor } from './PolygonEditor';
import { validateCorrectionObjects, type EditableObject } from './correctionValidation';
import type { components } from '../../api/generated/openapi';
import { useDisplayNames } from '../../hooks/useDisplayNames';
import { auditHeading, auditLabel, rulebookLabel } from '../../lib/names';

type DragState = { id: number; mode: 'move' | ResizeHandle; x: number; y: number; bbox: BBox1Based };
const HANDLES: ResizeHandle[] = ['nw', 'ne', 'sw', 'se'];

function describeCorrectionError(err: Error): string {
  if (!(err instanceof ApiError)) return err.message;
  if (err.code === 'MASK_UPLOAD_UNAVAILABLE') return 'The uploaded mask expired or no longer matches this revision. Choose the mask file again.';
  if (err.code === 'SOURCE_AUDIT_MISMATCH') return 'The selected audit checked a different revision of this sample. Clear the audit or pick a matching one.';
  if (err.code === 'JSON_SOURCE_NOT_CORRECTABLE') return 'The correction could not be saved. Check for crossing polygon edges, out-of-image points or changes to source identities. Your edits are still here.';
  if (err.status === 409) return 'Someone saved a newer revision. Your edits are kept here — reload the current revision, then reapply them.';
  return err.message;
}

const CorrectionWorkspace: React.FC = () => {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { reviewerId, openPrompt } = useReviewerStore();
  const datasetId = searchParams.get('dataset') ?? '';
  const sampleId = searchParams.get('sample') ?? '';
  const auditId = searchParams.get('audit') ?? '';
  const setParam = (updates: Record<string, string>) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(updates)) { if (value) next.set(key, value); else next.delete(key); }
    setSearchParams(next, { replace: true });
  };

  const [reason, setReason] = useState('');
  const [objects, setObjects] = useState<EditableObject[]>([]);
  const [jsonText, setJsonText] = useState('[]');
  const jsonEdit = useMemo(() => { try { return { objects: parseJsonGeometry(jsonText), error: null }; } catch (error) { return { objects: [], error: String(error) }; } }, [jsonText]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [mask, setMask] = useState<File | null>(null);
  const [message, setMessage] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);
  const [recheckTarget, setRecheckTarget] = useState<{ jobId: string; workspaceId: string; sampleId: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loadedSample, setLoadedSample] = useState<components['schemas']['Sample'] | null>(null);
  const [awaitingRevision, setAwaitingRevision] = useState<number | null>(null);
  const [editorEpoch, setEditorEpoch] = useState(0);
  const imageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);

  const datasetsQuery = useQuery({ queryKey: queryKeys.datasets(), queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }) });
  const samplesQuery = useQuery({ queryKey: queryKeys.datasetSamples(datasetId, { all: true }), queryFn: () => apiGetAllPages('/datasets/{dataset_id}/samples', { path: { dataset_id: datasetId }, query: { limit: 100, offset: 0 } }), enabled: !!datasetId });
  const sampleQuery = useQuery({ queryKey: queryKeys.datasetSample(datasetId, sampleId), queryFn: () => apiGet('/datasets/{dataset_id}/samples/{sample_id}', { path: { dataset_id: datasetId, sample_id: sampleId } }), enabled: !!datasetId && !!sampleId });
  const revisionsQuery = useQuery({ queryKey: ['revisions', datasetId, sampleId], queryFn: () => apiGetAllPages('/datasets/{dataset_id}/samples/{sample_id}/revisions', { path: { dataset_id: datasetId, sample_id: sampleId }, query: { limit: 100, offset: 0 } }), enabled: !!datasetId && !!sampleId });
  const auditsQuery = useQuery({ queryKey: queryKeys.audits(), queryFn: () => apiGetAllPages('/audits', { query: { limit: 100, offset: 0 } }) });
  const datasetAudits = useMemo(() => (auditsQuery.data?.items ?? []).filter((audit) => audit.dataset_id === datasetId), [auditsQuery.data, datasetId]);
  const sourceAuditNames = useDisplayNames({ auditIds: revisionsQuery.data?.items.map((revision) => revision.source_audit_id) ?? [] });
  // The audit's pinned revision decides whether it can be recorded as the correction's source.
  const auditSampleQuery = useQuery({
    queryKey: queryKeys.auditSample(auditId, sampleId),
    queryFn: () => apiGet('/audits/{audit_id}/samples/{sample_id}', { path: { audit_id: auditId, sample_id: sampleId } }),
    enabled: !!auditId && !!sampleId,
    retry: false,
  });

  // Survives refresh: blocked while any attempt of this image has an unfinished job.
  const recheckStatus = useImageRecheckStatus(auditId || null, sampleId || null);

  const sample = loadedSample;
  const imageSize = sample?.image_size ?? null;
  const isJson = sample?.annotation_format === 'coco' || sample?.annotation_format === 'labelme';
  const savedJsonObjects = useMemo(() => (sample?.objects ?? []).filter((object): object is JsonObject => 'coordinate_system' in object), [sample]);
  const originalIds = useMemo(() => (sample?.objects ?? []).map((obj) => obj.object_id).sort((a, b) => a - b).join(','), [sample]);
  const currentIds = useMemo(() => objects.map((obj) => obj.object_id).sort((a, b) => a - b).join(','), [objects]);
  const idsChanged = Boolean(sample) && originalIds !== currentIds;
  const problems = useMemo(() => validateCorrectionObjects(objects, imageSize), [objects, imageSize]);
  const sourceAuditMatches = auditSampleQuery.data?.summary.sample_revision === sample?.revision;

  const loadDraft = useCallback((value: components['schemas']['Sample']) => {
    setLoadedSample(value);
    setObjects(value.objects.flatMap((item) => 'coordinate_system' in item ? [] : [{ object_id: item.object_id, label: item.label, bbox: [...item.bbox] as BBox1Based }]));
    setJsonText(JSON.stringify(value.objects, null, 2));
    setDirty(false);
    setSelectedId(null);
    setReason(''); setMask(null); setAwaitingRevision(null);
    setEditorEpoch((epoch) => epoch + 1);
  }, []);

  useEffect(() => {
    const incoming = sampleQuery.data;
    if (!incoming || dirty || mask || awaitingRevision !== null) return;
    if (!sample || incoming.revision > sample.revision) loadDraft(incoming);
  }, [sampleQuery.data, sample, dirty, mask, awaitingRevision, loadDraft]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const editObjects = (next: (old: EditableObject[]) => EditableObject[]) => { setObjects(next); setDirty(true); setMessage(null); };

  const correction = useMutation({
    mutationFn: async () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Set your reviewer name first (top right).'); }
      if (!sample) throw new Error('Select a sample before saving.');
      if (awaitingRevision !== null) throw new Error('Load the saved revision before making another correction.');
      if (!reason.trim()) throw new Error('Explain what you corrected — the reason is stored with the revision.');
      if (isJson && jsonEdit.error) throw new Error(jsonEdit.error);
      if (!isJson && problems.length) throw new Error('Fix the highlighted object problems first.');
      let mask_upload_id: string | null = null;
      if (mask) {
        const uploaded = await apiPostForm('/datasets/{dataset_id}/samples/{sample_id}/mask-uploads', { path: { dataset_id: datasetId, sample_id: sampleId }, body: { mask, expected_revision: sample.revision } });
        mask_upload_id = uploaded.mask_upload_id;
      }
      return apiPost('/datasets/{dataset_id}/samples/{sample_id}/revisions', {
        path: { dataset_id: datasetId, sample_id: sampleId },
        body: {
          expected_revision: sample.revision,
          reviewer_id: reviewerId.trim(),
          reason: reason.trim(),
          objects: isJson ? jsonEdit.objects : objects,
          mask_upload_id,
          source_audit_id: auditId && sourceAuditMatches ? auditId : null,
        },
      });
    },
    onSuccess: async (saved) => {
      setAwaitingRevision(saved.revision);
      setMessage({ tone: 'ok', text: `Saved as revision r${saved.revision}. Loading the saved annotation…` });
      setMask(null);
      setReason('');
      // Keep recheck locked until its geometry, assets and revision agree. A saved
      // POST must not be repeated just because the following GET failed.
      const refreshed = await sampleQuery.refetch();
      if (refreshed.data && !refreshed.error && refreshed.data.revision >= saved.revision) {
        loadDraft(refreshed.data);
        setMessage({ tone: 'ok', text: refreshed.data.revision === saved.revision
          ? `Saved as revision r${saved.revision}.${auditId ? ' You can now re-check it against the audit.' : ''}`
          : `Your correction was saved as r${saved.revision}. A newer revision r${refreshed.data.revision} is now loaded; inspect it before rechecking.` });
      } else {
        setMessage({ tone: 'error', text: `Your correction was saved as r${saved.revision}, but it could not be loaded. Use Reload current revision before editing or rechecking.` });
      }
      void revisionsQuery.refetch();
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasetSamples(datasetId, { all: true }) });
    },
    onError: (err) => {
      setMessage({ tone: 'error', text: describeCorrectionError(err) });
    },
  });

  const recheck = useMutation({
    mutationFn: async () => {
      if (!reviewerId.trim()) { openPrompt(); throw new Error('Set your reviewer name first (top right).'); }
      if (!auditId) throw new Error('Choose the audit to re-check against.');
      if (!sample) throw new Error('Select a sample.');
      if (dirty || awaitingRevision !== null) throw new Error('Save your edits and load the saved revision before rechecking.');
      if (!recheckStatus.canRecheck) throw new Error(RECHECK_IN_PROGRESS_MESSAGE);
      const workspaceId = auditSampleQuery.data?.workspace_audit_id ?? auditId;
      const result = await apiPost('/audits/{audit_id}/rechecks', { path: { audit_id: auditId }, body: { sample_ids: [sampleId], sample_revisions: { [sampleId]: sample.revision }, reviewer_id: reviewerId.trim(), feedback: null, propose_correction: false } });
      return { jobId: result.job.job_id, workspaceId, sampleId, label: auditLabel(result.audit.name) };
    },
    onSuccess: (target) => {
      setRecheckTarget(target);
      setMessage({ tone: 'ok', text: `${target.label} started for ${target.sampleId}.` });
      recheckStatus.track(target.jobId);
      void queryClient.invalidateQueries({ queryKey: queryKeys.audits() });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === 'RECHECK_ALREADY_RUNNING') {
        setMessage({ tone: 'error', text: RECHECK_IN_PROGRESS_MESSAGE });
        recheckStatus.refresh();
        return;
      }
      setMessage({ tone: 'error', text: err.message });
    },
  });
  const job = useJobPolling(recheckTarget?.jobId);
  const editingLocked = correction.isPending || recheck.isPending || awaitingRevision !== null;
  const newerRevision = sampleQuery.data && sample && sampleQuery.data.revision > sample.revision ? sampleQuery.data.revision : null;
  const reload = async () => {
    if (dirty && awaitingRevision === null && !window.confirm('Discard unsaved edits and load the current revision?')) return;
    const refreshed = await sampleQuery.refetch();
    if (refreshed.data && !refreshed.error && (awaitingRevision === null || refreshed.data.revision >= awaitingRevision)) {
      loadDraft(refreshed.data); setMessage(null);
    } else setMessage({ tone: 'error', text: 'The current revision could not be loaded. Your draft is still here.' });
  };

  const updateObject = (index: number, field: 'label' | 'object_id' | 0 | 1 | 2 | 3, value: string) => editObjects((old) => old.map((obj, i) => {
    if (i !== index) return obj;
    if (field === 'label') return { ...obj, label: value };
    if (field === 'object_id') return { ...obj, object_id: Number(value) };
    return { ...obj, bbox: obj.bbox.map((n, j) => (j === field ? Number(value) : n)) as BBox1Based };
  }));

  const startDrag = (event: React.PointerEvent, obj: EditableObject, mode: DragState['mode']) => {
    if (editingLocked) return;
    event.stopPropagation();
    setSelectedId(obj.object_id);
    dragRef.current = { id: obj.object_id, mode, x: event.clientX, y: event.clientY, bbox: obj.bbox };
    (event.currentTarget as Element).setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    const bounds = imageRef.current?.getBoundingClientRect();
    if (!drag || !bounds || !imageSize || editingLocked) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    const displayed: [number, number] = [bounds.width, bounds.height];
    const bbox = drag.mode === 'move'
      ? translateBboxOnCanvas(drag.bbox, dx, dy, displayed, imageSize)
      : resizeBboxOnCanvas(drag.bbox, drag.mode, dx, dy, displayed, imageSize);
    editObjects((old) => old.map((obj) => (obj.object_id === drag.id ? { ...obj, bbox } : obj)));
  };
  const endDrag = () => { dragRef.current = null; };

  const loadError = datasetsQuery.error ?? samplesQuery.error ?? sampleQuery.error ?? revisionsQuery.error;

  return (
    <div className="workstation-page corrections-page space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1>Annotation corrections</h1>
          <p className="text-xs text-text-muted mt-1">Adjust outlines, boxes and labels, then save a new revision with your reason.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {auditId && <Link to={`/audits/${encodeURIComponent(auditSampleQuery.data?.workspace_audit_id ?? auditId)}/review?sample=${encodeURIComponent(sampleId)}`}
            onClick={(event) => { if ((dirty || editingLocked) && !window.confirm('Leave the correction editor? Unsaved edits will be discarded.')) event.preventDefault(); }}>Back to audit review</Link>}
          <select aria-label="Dataset" value={datasetId} disabled={editingLocked} onChange={(e) => {
            if (dirty && !window.confirm('Discard unsaved edits?')) return;
            setParam({ dataset: e.target.value, sample: '', audit: '' });
          }}>
            <option value="">Select dataset</option>
            {datasetsQuery.data?.items.map((d) => <option key={d.dataset_id} value={d.dataset_id}>{d.name}</option>)}
          </select>
          <select aria-label="Sample" value={sampleId} onChange={(e) => { if (dirty && !window.confirm('Discard unsaved edits?')) return; setParam({ sample: e.target.value }); }} disabled={!datasetId || editingLocked}>
            <option value="">Select sample</option>
            {samplesQuery.data?.items.map((s) => <option key={s.sample_id} value={s.sample_id}>{s.sample_id} · r{s.revision}</option>)}
          </select>
          <select aria-label="Audit for re-check" value={auditId} onChange={(e) => setParam({ audit: e.target.value })} disabled={!datasetId || editingLocked}>
            <option value="">No audit (just correct)</option>
            {datasetAudits.map((a) => <option key={a.audit_id} value={a.audit_id}>{auditHeading(a.name)} · {rulebookLabel(a.rulebook_name, a.rulebook_version)}</option>)}
          </select>
        </div>
      </header>

      {loadError && <p role="alert" className="text-xs text-status-fail">{String(loadError)}</p>}
      {!datasetId && <div className="correction-empty">Select a dataset and sample — or use “Correct” from the review screen to jump straight here.</div>}
      {sampleQuery.isLoading && <p className="text-xs text-text-muted">Loading sample…</p>}
      {newerRevision && dirty && awaitingRevision === null && <p role="status" className="correction-warning">Revision r{newerRevision} is now available. Your unsaved draft is still based on r{sample?.revision}. Copy any edits you need before reloading.</p>}

      {sample && (
        <div className="correction-layout">
          <div className="space-y-2">
            {isJson ? (sample.assets?.image?.url && imageSize ? <PolygonEditor key={`${sample.revision}:${editorEpoch}`}
              sampleId={sampleId} imageSize={imageSize} imageUrl={sample.assets.image.url}
              objects={jsonEdit.objects} savedObjects={savedJsonObjects}
              disabled={editingLocked || !!jsonEdit.error} onChange={(value) => {
                setJsonText(JSON.stringify(value, null, 2));
                setDirty(JSON.stringify(value) !== JSON.stringify(savedJsonObjects));
                setMessage(null);
              }} />
              : <div className="correction-empty">This revision needs an image and its dimensions for visual editing.</div>) : sample.assets?.image?.url ? (
              <div
                ref={imageRef}
                className="correction-image"
                style={{ aspectRatio: imageSize ? `${imageSize[0]} / ${imageSize[1]}` : '4 / 3' }}
              >
                <img src={sample.assets.image.url} alt={sampleId} />
                {sample.assets.mask_preview?.url && <img className="correction-mask" src={sample.assets.mask_preview.url} alt="Mask preview" />}
                <svg
                  viewBox={`0 0 ${imageSize?.[0] ?? 640} ${imageSize?.[1] ?? 480}`}
                  aria-label="Annotation boxes"
                  onPointerMove={onPointerMove}
                  onPointerUp={endDrag}
                  onPointerCancel={endDrag}
                  onPointerDown={() => setSelectedId(null)}
                >
                  {objects.map((obj) => {
                    const box = bboxToCss(obj.bbox);
                    const selected = selectedId === obj.object_id;
                    const handleSize = Math.max(6, Math.round((imageSize?.[0] ?? 640) / 90));
                    return (
                      <g key={obj.object_id} className={`${selected ? 'is-selected' : ''}${problems.some((p) => p.objectId === obj.object_id) ? ' is-invalid' : ''}`}>
                        <rect x={box.left} y={box.top} width={box.width} height={box.height} onPointerDown={(e) => startDrag(e, obj, 'move')} />
                        <text x={box.left + 2} y={Math.max(12, box.top - 3)}>#{obj.object_id} {obj.label}</text>
                        {selected && HANDLES.map((handle) => {
                          const hx = handle.endsWith('w') ? box.left : box.left + box.width;
                          const hy = handle.startsWith('n') ? box.top : box.top + box.height;
                          return <rect key={handle} className={`resize-handle is-${handle}`} x={hx - handleSize / 2} y={hy - handleSize / 2} width={handleSize} height={handleSize} onPointerDown={(e) => startDrag(e, obj, handle)} />;
                        })}
                      </g>
                    );
                  })}
                </svg>
              </div>
            ) : <div className="correction-empty">This revision has no image asset.</div>}
            <p className="text-2xs text-text-muted font-mono">{isJson ? 'Coordinates are 0-based continuous. Save your correction before rechecking.' : 'Click a box to select it · drag to move · drag a corner to resize · coordinates are 1-based inclusive pixels'}</p>
          </div>

          <aside className="correction-panel">
            {isJson ? <section>
              <h2>{sample.annotation_format} geometry · revision r{sample.revision}</h2>
              <p className="text-xs text-text-muted">Edit labels, supplied boxes and polygon points. Keep object order, source IDs, source keys and shape types unchanged. COCO labels must name an existing category. Polygon-derived bounds are recalculated before saving.</p>
              <details><summary className="text-xs">Advanced annotation fields</summary>
                <label className="correction-field">Annotation geometry
                  <textarea aria-label="JSON annotation geometry" rows={18} disabled={editingLocked} className="font-mono text-xs" value={jsonText} onChange={(e) => { setJsonText(e.target.value); setDirty(true); setMessage(null); setEditorEpoch((epoch) => epoch + 1); }} />
                </label>
              </details>
              {jsonEdit.error && <p role="alert" className="text-xs text-status-fail">{jsonEdit.error}</p>}
            </section> : <section>
              <h2>Objects · revision r{sample.revision}</h2>
              <table className="correction-table">
                <thead><tr><th>ID</th><th>Label</th><th>xmin</th><th>ymin</th><th>xmax</th><th>ymax</th><th /></tr></thead>
                <tbody>
                  {objects.map((obj, i) => {
                    const invalid = imageSize ? !isBboxValid(obj.bbox, imageSize) : false;
                    return (
                      <tr key={`${obj.object_id}-${i}`} className={`${selectedId === obj.object_id ? 'is-selected' : ''}${invalid ? ' is-invalid' : ''}`} onClick={() => setSelectedId(obj.object_id)}>
                        <td><input disabled={editingLocked} aria-label={`Object ${i + 1} ID`} type="number" min={1} value={obj.object_id} onChange={(e) => updateObject(i, 'object_id', e.target.value)} /></td>
                        <td><input disabled={editingLocked} aria-label={`Object ${i + 1} label`} value={obj.label} onChange={(e) => updateObject(i, 'label', e.target.value)} /></td>
                        {obj.bbox.map((n, j) => <td key={j}><input disabled={editingLocked} aria-label={`Object ${i + 1} coordinate ${j + 1}`} type="number" value={n} onChange={(e) => updateObject(i, j as 0 | 1 | 2 | 3, e.target.value)} /></td>)}
                        <td><button disabled={editingLocked} type="button" className="rule-action is-reject" aria-label={`Remove object ${obj.object_id}`} onClick={(e) => { e.stopPropagation(); editObjects((old) => old.filter((_, index) => index !== i)); }}><Trash2 size={12} /></button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <Button disabled={editingLocked} size="sm" variant="secondary" leftIcon={<Plus size={13} />} onClick={() => {
                const id = objects.reduce((max, obj) => Math.max(max, obj.object_id), 0) + 1;
                editObjects((old) => [...old, { object_id: id, label: old[0]?.label ?? 'person', bbox: defaultNewBbox(imageSize) }]);
                setSelectedId(id);
              }}>Add object</Button>
              {problems.length > 0 && <ul role="alert" className="rule-form-errors">{problems.map((p) => <li key={p.message}>{p.message}</li>)}</ul>}
              {idsChanged && (
                <p className="correction-warning"><AlertTriangle size={13} /> Object IDs changed. The mask's region IDs must match the object IDs, so adding or removing objects usually needs a replacement mask below.</p>
              )}
            </section>}

            <section>
              <h2>Save correction</h2>
              <label className="correction-field">Reason (required)
                <textarea rows={2} disabled={editingLocked} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Included the visible foot in polygon #2" />
              </label>
              {!isJson && <label className="correction-field">Replacement mask (optional, PNG with instance IDs)
                <input disabled={editingLocked} type="file" accept="image/png" onChange={(e) => { setMask(e.target.files?.[0] ?? null); setDirty(true); }} />
              </label>}
              {auditId && auditSampleQuery.data && !sourceAuditMatches && (
                <p className="text-2xs text-text-faint">The selected audit checked r{auditSampleQuery.data.summary.sample_revision}, not r{sample.revision}, so it will not be recorded as the source of this correction.</p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" size="sm" leftIcon={<Save size={13} />} isLoading={correction.isPending} disabled={editingLocked || (!dirty && !mask) || (isJson && !!jsonEdit.error)} onClick={() => correction.mutate()}>Save as new revision</Button>
                <Button variant="ghost" size="sm" disabled={correction.isPending || recheck.isPending} onClick={() => { void reload(); }}>Reload current revision</Button>
              </div>
            </section>

            {auditId && (
              <section>
                <h2>Re-check against audit</h2>
                <p className="text-2xs text-text-muted">Runs the audit's rules again on revision r{sample.revision} so the fix can be reviewed.</p>
                <Button size="sm" variant="secondary" leftIcon={<RefreshCw size={13} />} isLoading={recheck.isPending} disabled={dirty || editingLocked || !recheckStatus.canRecheck} title={dirty ? 'Save your edits first' : recheckStatus.state === 'active' ? RECHECK_IN_PROGRESS_MESSAGE : undefined} onClick={() => recheck.mutate()}>Re-check this sample</Button>
                {recheckStatus.state === 'loading' && <p className="text-2xs text-text-muted">Loading this image's recheck status…</p>}
                {recheckStatus.state === 'error' && (
                  <p role="alert" className="text-xs text-status-fail">
                    Could not load this image's recheck status, so re-checking is paused.{' '}
                    <button type="button" className="underline" onClick={recheckStatus.refresh}>Retry</button>
                  </p>
                )}
                {recheckStatus.activeJob && recheckStatus.activeJob.job_id !== recheckTarget?.jobId && (
                  <p className="text-xs">A recheck of this image is {jobStatusLabel(recheckStatus.activeJob.status)}. You can re-check again once it finishes.</p>
                )}
                {job.job && (
                  <p className="text-xs">
                    Re-check {jobStatusLabel(job.job.status)}
                    {recheckTarget && <> — <Link to={`/audits/${recheckTarget.workspaceId}/review?sample=${encodeURIComponent(recheckTarget.sampleId)}`}>continue in the original review →</Link></>}
                    {job.isFailed && job.job.error && <> — {job.job.error.message}</>}
                  </p>
                )}
              </section>
            )}

            {message && <p role={message.tone === 'error' ? 'alert' : 'status'} className={message.tone === 'error' ? 'text-xs text-status-fail' : 'text-xs text-status-pass'}>{message.text}</p>}

            <section>
              <h2>Revision history</h2>
              <ol className="finding-history">
                {revisionsQuery.data?.items.map((revision) => (
                  <li key={revision.revision}>
                    <b>r{revision.revision}</b> · {formatTimestamp(revision.created_at)} · {revision.reviewer_id ?? 'original import'}
                    {revision.reason && <span> — “{revision.reason}”</span>}
                    {revision.mask_changed && <span> · mask replaced</span>}
                    {revision.source_audit_id && <span title={revision.source_audit_id}> · from {auditLabel(sourceAuditNames.audit(revision.source_audit_id))}</span>}
                  </li>
                ))}
              </ol>
            </section>
          </aside>
        </div>
      )}
    </div>
  );
};

/** A different sample gets an isolated draft, selection, mask and job state. */
export const CorrectionsPage: React.FC = () => {
  const [params] = useSearchParams();
  return <CorrectionWorkspace key={JSON.stringify([params.get('dataset'), params.get('sample')])} />;
};
