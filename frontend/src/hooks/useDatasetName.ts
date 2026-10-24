import { useQuery } from '@tanstack/react-query';
import { apiGetAllPages } from '../api/client';
import { queryKeys } from '../api/queryClient';

/** Resolve dataset IDs to their names using the shared dataset list; unknown IDs are shown as-is. */
export function useDatasetName(): (datasetId: string | null | undefined) => string {
  const { data } = useQuery({
    queryKey: queryKeys.datasets(),
    queryFn: () => apiGetAllPages('/datasets', { query: { limit: 100, offset: 0 } }),
  });
  return (datasetId) => {
    if (!datasetId) return '…';
    return data?.items.find((dataset) => dataset.dataset_id === datasetId)?.name ?? datasetId;
  };
}
