import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Database,
  UploadCloud,
  FileArchive,
  ArrowRight,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  X,
} from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import { apiGetAllPages, apiPostForm } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { useJobPolling } from '../../hooks/useJobPolling';
import { formatCount } from '../../lib/format';

export const DatasetsPage: React.FC = () => {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [datasetName, setDatasetName] = useState('');
  const [format, setFormat] = useState<'penn_fudan' | 'coco' | 'labelme'>('penn_fudan');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);

  // Fetch datasets
  const { data: datasetPage, isLoading, error } = useQuery({
    queryKey: queryKeys.datasets(),
    queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }),
  });
  const datasets = datasetPage?.items ?? [];

  // Track import job
  const jobState = useJobPolling(activeJobId, {
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
    },
  });

  // Upload mutation
  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (!selectedFile) throw new Error('Please select a ZIP file');
      if (!datasetName.trim()) throw new Error('Please enter a dataset name');

      return apiPostForm('/datasets', {
        body: { archive: selectedFile, name: datasetName.trim(), format },
        onProgress: (loaded, total) => setUploadProgress(total ? loaded / total : null),
      });
    },
    onSuccess: (res) => {
      setIsUploadModalOpen(false);
      setSelectedFile(null);
      setDatasetName('');
      setActiveJobId(res.job.job_id);
      setUploadProgress(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.datasets() });
    },
  });

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setSelectedFile(file);
      if (!datasetName) {
        // Strip extension for default name
        setDatasetName(file.name.replace(/\.[^/.]+$/, ''));
      }
    }
  };

  return (
    <div className="workstation-page dataset-page p-6 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-semibold text-text flex items-center gap-2">
            <Database className="w-5 h-5 text-accent" />
            Dataset Catalogue
          </h1>
          <p className="text-xs text-text-muted mt-1">
            Import images and annotations in Penn-Fudan, COCO JSON or LabelMe JSON format.
          </p>
        </div>
        <Button
          variant="primary"
          leftIcon={<UploadCloud className="w-4 h-4" />}
          onClick={() => setIsUploadModalOpen(true)}
        >
          Import Dataset ZIP
        </Button>
      </div>

      {/* Active Import Job Banner */}
      {jobState.isActive && (
        <div className="p-4 bg-surface border border-accent/40 rounded-lg flex items-center justify-between shadow-md">
          <div className="flex items-center gap-3">
            <Loader2 className="w-5 h-5 text-accent animate-spin" />
            <div>
              <div className="text-sm font-semibold text-text">Importing dataset archive...</div>
              <div className="text-xs text-text-muted font-mono mt-0.5">
                Phase: {jobState.progress?.phase ?? 'processing'} · Processed:{' '}
                {jobState.progress?.processed ?? 0}
                {jobState.progress?.total ? ` / ${jobState.progress.total}` : ''}
              </div>
            </div>
          </div>
          <Button size="sm" variant="ghost" onClick={() => jobState.cancel()}>
            Cancel Job
          </Button>
        </div>
      )}

      {/* Datasets Table */}
      <div>
        <div className="text-xs font-mono uppercase tracking-wider text-text-muted mb-3">
          Catalogued Datasets ({datasetPage?.total ?? 0})
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center p-12 text-text-muted text-xs">
            <Loader2 className="w-4 h-4 animate-spin mr-2 text-accent" />
            Loading dataset catalogue...
          </div>
        ) : error ? (
          <div className="p-4 rounded bg-status-fail/10 border border-status-fail/30 text-status-fail text-xs">
            Failed to load datasets: {(error as Error).message}
          </div>
        ) : datasets.length === 0 ? (
          <div className="p-12 text-center border border-dashed border-border rounded-lg bg-surface/50">
            <FileArchive className="w-8 h-8 text-text-muted mx-auto mb-2 opacity-60" />
            <div className="text-sm text-text font-medium">No datasets found</div>
            <p className="text-xs text-text-muted mt-1 max-w-sm mx-auto">
              Upload a dataset ZIP archive to begin auditing and reviewing samples.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => setIsUploadModalOpen(true)}
            >
              Upload Dataset ZIP
            </Button>
          </div>
        ) : (
          <div className="border border-border rounded-lg overflow-hidden bg-surface">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-high border-b border-border text-2xs font-mono uppercase text-text-muted">
                <tr>
                  <th className="py-2.5 px-4 font-medium">Dataset Name</th>
                  <th className="py-2.5 px-4 font-medium">Format</th>
                  <th className="py-2.5 px-4 font-medium">Status</th>
                  <th className="py-2.5 px-4 font-medium">Samples</th>
                  <th className="py-2.5 px-4 font-medium">Objects</th>
                  <th className="py-2.5 px-4 font-medium">Integrity Evidence</th>
                  <th className="py-2.5 px-4 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {datasets.map((ds) => {
                  const issueCount = ds.issues?.length ?? 0;
                  return (
                    <tr
                      key={ds.dataset_id}
                      className="hover:bg-surface-high/60 transition-colors cursor-pointer"
                      onClick={() => navigate(`/datasets/${ds.dataset_id}`)}
                    >
                      <td className="py-3 px-4">
                        <div className="font-semibold text-text">{ds.name}</div>
                        <div className="text-2xs font-mono text-text-faint">{ds.dataset_id}</div>
                      </td>
                      <td className="py-3 px-4 font-mono text-text-muted">
                        {ds.format}
                      </td>
                      <td className="py-3 px-4">
                        <Badge
                          variant={
                            ds.status === 'ready'
                              ? 'pass'
                              : ds.status === 'invalid' || ds.status === 'failed'
                              ? 'fail'
                              : 'pending'
                          }
                          size="sm"
                        >
                          {ds.status}
                        </Badge>
                      </td>
                      <td className="py-3 px-4 font-mono text-text">
                        {formatCount(ds.sample_count)}
                      </td>
                      <td className="py-3 px-4 font-mono text-text">
                        {formatCount(ds.object_count)}
                      </td>
                      <td className="py-3 px-4">
                        {issueCount > 0 ? (
                          <span
                            title="Catalogue integrity issues noted during ingestion"
                            className="inline-flex items-center gap-1 text-2xs font-mono text-status-unsure bg-status-unsure/10 px-2 py-0.5 rounded border border-status-unsure/30"
                          >
                            <AlertTriangle className="w-3 h-3" />
                            <span>{issueCount} issue{issueCount === 1 ? '' : 's'}</span>
                          </span>
                        ) : (
                          <span className="text-2xs font-mono text-status-pass flex items-center gap-1">
                            <CheckCircle2 className="w-3 h-3" />
                            <span>Coherent</span>
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/datasets/${ds.dataset_id}`);
                          }}
                        >
                          View Samples
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Upload Modal */}
      <Dialog.Root open={isUploadModalOpen} onOpenChange={setIsUploadModalOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-base-pure/80 backdrop-blur-sm z-50 animate-in fade-in" />
          <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-lg bg-surface border border-border p-6 rounded-lg shadow-xl z-50 animate-in zoom-in-95">
            <div className="flex items-center justify-between pb-3 border-b border-border mb-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-text">
                <UploadCloud className="w-4 h-4 text-accent" />
                <span>Import Dataset ZIP Archive</span>
              </div>
              <button
                onClick={() => setIsUploadModalOpen(false)}
                className="text-text-muted hover:text-text p-1 rounded"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                uploadMutation.mutate();
              }}
              className="space-y-4"
            >
              <div>
                <label className="block text-xs font-mono text-text-muted uppercase mb-1">
                  Dataset Name
                </label>
                <input
                  type="text"
                  value={datasetName}
                  onChange={(e) => setDatasetName(e.target.value)}
                  placeholder="e.g. Penn-Fudan Pedestrians Batch 1"
                  required
                  className="w-full bg-surface-high border border-border rounded px-3 py-2 text-xs font-sans text-text focus:outline-none focus:border-accent"
                />
              </div>

              <div>
                <label className="block text-xs font-mono text-text-muted uppercase mb-1">
                  Format
                </label>
                <select aria-label="Annotation format" value={format} onChange={(e) => setFormat(e.target.value as typeof format)} className="w-full bg-base border border-border rounded px-3 py-2 text-xs">
                  <option value="penn_fudan">Penn-Fudan boxes and pixel masks</option>
                  <option value="coco">COCO JSON boxes and polygons</option>
                  <option value="labelme">LabelMe JSON polygons and rectangles</option>
                </select>
                <span className="text-3xs text-text-faint mt-1 block">
                  {format === 'penn_fudan' ? 'Include PNGImages, Annotation and PedMasks folders.' : format === 'coco' ? 'Include JSON annotations and their images. An annotations/ folder with a sibling images/ folder is supported.' : 'Include one JSON per image. imagePath must point to an image within the ZIP.'}
                </span>
              </div>

              <div>
                <label className="block text-xs font-mono text-text-muted uppercase mb-1">
                  ZIP File
                </label>
                <div
                  className="border-2 border-dashed border-border hover:border-accent/50 rounded-lg p-6 text-center cursor-pointer transition-colors bg-base/50"
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.dataTransfer.dropEffect = 'copy';
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    const file = event.dataTransfer.files[0];
                    if (file && file.name.toLowerCase().endsWith('.zip')) {
                      setSelectedFile(file);
                      if (!datasetName) setDatasetName(file.name.replace(/\.[^/.]+$/, ''));
                    }
                  }}
                >
                  <input
                    type="file"
                    accept=".zip"
                    onChange={handleFileChange}
                    className="hidden"
                    id="dataset-zip-input"
                  />
                  <label htmlFor="dataset-zip-input" className="cursor-pointer">
                    <FileArchive className="w-8 h-8 text-text-muted mx-auto mb-2" />
                    {selectedFile ? (
                      <div>
                        <div className="text-xs font-mono text-accent font-medium">
                          {selectedFile.name}
                        </div>
                        <div className="text-2xs text-text-faint mt-0.5">
                          {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                        </div>
                      </div>
                    ) : (
                      <div>
                        <div className="text-xs text-text font-medium">
                          Click to select or drag and drop ZIP archive
                        </div>
                        <div className="text-2xs text-text-muted mt-1">
                          Images and matching annotation files (.zip)
                        </div>
                      </div>
                    )}
                  </label>
                </div>
              </div>

              {uploadMutation.isError && (
                <div className="p-2.5 rounded bg-status-fail/10 border border-status-fail/30 text-xs text-status-fail flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span>{(uploadMutation.error as Error).message}</span>
                </div>
              )}

              {uploadMutation.isPending && uploadProgress !== null && (
                <div className="space-y-1" aria-live="polite">
                  <div className="flex justify-between text-2xs text-text-muted">
                    <span>Uploading ZIP</span>
                    <span>{Math.round(uploadProgress * 100)}%</span>
                  </div>
                  <progress className="w-full h-1.5" max={1} value={uploadProgress} />
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t border-border">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setIsUploadModalOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={uploadMutation.isPending}
                  disabled={!selectedFile || !datasetName.trim()}
                >
                  Upload & Ingest
                </Button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
};
