import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  Image as ImageIcon,
  X,
  Loader2,
} from 'lucide-react';
import { apiGet, apiGetAllPages } from '../../api/client';
import { queryKeys } from '../../api/queryClient';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { formatBbox } from '../../lib/coords';
import { formatCount } from '../../lib/format';
import type { components } from '../../api/generated/openapi';

type Sample = components['schemas']['Sample'];

export const DatasetDetailPage: React.FC = () => {
  const { datasetId } = useParams<{ datasetId: string }>();
  const navigate = useNavigate();

  const [selectedSample, setSelectedSample] = useState<Sample | null>(null);

  // Fetch dataset info
  const { data: dataset, error: datasetError, isLoading: isDatasetLoading } = useQuery({
    queryKey: queryKeys.dataset(datasetId!),
    queryFn: () => apiGet('/datasets/{dataset_id}', { path: { dataset_id: datasetId! } }),
    enabled: Boolean(datasetId),
  });

  // Fetch samples page
  const { data: samplePage, isLoading: isSamplesLoading, error: samplesError } = useQuery({
    queryKey: queryKeys.datasetSamples(datasetId!),
    queryFn: () => apiGetAllPages('/datasets/{dataset_id}/samples', { path: { dataset_id: datasetId! }, query: { limit: 100, offset: 0 } }),
    enabled: Boolean(datasetId),
  });

  return (
    <div className="workstation-page sample-explorer flex flex-col h-full bg-base overflow-hidden">
      {/* Top Header */}
      <div className="h-14 border-b border-border bg-surface px-6 flex items-center justify-between shrink-0 select-none">
        <div className="flex items-center gap-4">
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<ArrowLeft className="w-4 h-4" />}
            onClick={() => navigate('/datasets')}
          >
            Datasets
          </Button>

          <div className="h-4 w-px bg-border" />

          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-text text-sm">{dataset?.name ?? datasetId}</span>
              <Badge
                variant={
                  dataset?.status === 'ready'
                    ? 'pass'
                    : dataset?.status === 'invalid' || dataset?.status === 'failed'
                    ? 'fail'
                    : 'pending'
                }
                size="sm"
              >
                {dataset?.status ?? 'loading'}
              </Badge>
            </div>
            <div className="text-2xs font-mono text-text-faint">{datasetId}</div>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs font-mono text-text-muted">
          <span>{formatCount(dataset?.sample_count ?? null)} samples</span>
          <span>·</span>
          <span>{formatCount(dataset?.object_count ?? null)} objects</span>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 flex min-h-0">
        {(datasetError || samplesError) && <div role="alert" className="p-3 text-status-fail">{String(datasetError ?? samplesError)}</div>}
        {isDatasetLoading && <div className="p-3 text-text-muted">Loading dataset…</div>}
        {/* Samples Grid */}
        <div className="flex-1 p-6 overflow-y-auto">
          <div className="text-xs font-mono uppercase tracking-wider text-text-muted mb-4">
            Catalogue Samples ({samplePage?.items?.length ?? 0})
          </div>

          {isSamplesLoading ? (
            <div className="flex items-center justify-center p-12 text-text-muted text-xs">
              <Loader2 className="w-4 h-4 animate-spin mr-2 text-accent" />
              Loading samples...
            </div>
          ) : samplePage?.items?.length === 0 ? (
            <div className="p-8 text-center text-text-muted text-xs border border-dashed border-border rounded">
              No samples catalogued for this dataset.
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
              {samplePage?.items?.map((sample) => {
                const isSelected = selectedSample?.sample_id === sample.sample_id;
                return (
                  <div
                    key={sample.sample_id}
                    onClick={() => setSelectedSample(sample)}
                    className={`rounded-lg border bg-surface overflow-hidden cursor-pointer transition-all ${
                      isSelected
                        ? 'border-accent shadow-md ring-1 ring-accent'
                        : 'border-border hover:border-border-muted hover:shadow-sm'
                    }`}
                  >
                    {/* Thumbnail / Image preview */}
                    <div className="aspect-[4/3] bg-base-pure relative flex items-center justify-center border-b border-border/60 overflow-hidden">
                      {sample.assets?.image?.url ? (
                        <img
                          src={sample.assets.image.url}
                          alt={sample.sample_id}
                          className="w-full h-full object-cover"
                          loading="lazy"
                        />
                      ) : (
                        <ImageIcon className="w-8 h-8 text-text-faint" />
                      )}
                      <span className="absolute bottom-1.5 right-1.5 px-1.5 py-0.2 rounded bg-base-pure/80 backdrop-blur-sm text-3xs font-mono text-text-muted border border-border/50">
                        {sample.objects?.length ?? 0} objs
                      </span>
                    </div>

                    <div className="p-2.5">
                      <div className="font-mono text-xs text-text font-medium truncate">
                        {sample.sample_id}
                      </div>
                      <div className="flex items-center justify-between text-2xs text-text-muted font-mono mt-1">
                        <span>
                          {sample.image_size ? `${sample.image_size[0]}×${sample.image_size[1]}` : '—'}
                        </span>
                        <span className="text-text-faint">r{sample.revision}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Selected Sample Detail Drawer */}
        {selectedSample && (
          <aside className="w-96 border-l border-border bg-surface flex flex-col min-h-0 select-none animate-in slide-in-from-right-4">
            <div className="p-4 border-b border-border flex items-center justify-between">
              <div>
                <div className="font-mono font-semibold text-sm text-text">
                  {selectedSample.sample_id}
                </div>
                <div className="text-2xs font-mono text-text-muted">
                  Revision {selectedSample.revision} · {selectedSample.image_size?.[0]}×
                  {selectedSample.image_size?.[1]} px
                </div>
              </div>
              <button
                onClick={() => setSelectedSample(null)}
                className="text-text-muted hover:text-text p-1 rounded"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 p-4 overflow-y-auto space-y-4">
              {/* Asset View */}
              <div>
                <div className="text-2xs font-mono uppercase text-text-muted mb-2">Image Asset</div>
                <div className="rounded border border-border bg-base-pure overflow-hidden">
                  {selectedSample.assets?.image?.url ? (
                    <img
                      src={selectedSample.assets.image.url}
                      alt={selectedSample.sample_id}
                      className="w-full h-auto object-contain"
                    />
                  ) : (
                    <div className="p-8 text-center text-xs text-text-faint">No image asset</div>
                  )}
                </div>
              </div>

              {/* Objects & Bounding Boxes */}
              <div>
                <div className="text-2xs font-mono uppercase text-text-muted mb-2">
                  Ground Truth Annotations ({selectedSample.objects?.length ?? 0})
                </div>
                <div className="space-y-2">
                  {selectedSample.objects?.map((obj) => (
                    <div
                      key={obj.object_id}
                      className="p-2.5 rounded bg-surface-high border border-border/70 text-xs font-mono"
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-semibold text-accent">
                          #{obj.object_id} {obj.label}
                        </span>
                      </div>
                      <div className="text-2xs text-text-muted">
                        BBox: {obj.bbox ? formatBbox(obj.bbox) : 'Unavailable'}
                        {'coordinate_system' in obj && <div>0-based continuous · {obj.polygons?.length ?? 0} polygon parts · source {obj.source_key}</div>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
};
