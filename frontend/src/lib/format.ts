/**
 * Formatting utilities — dates, counts, fractions, IDs.
 * Keep all display-layer formatting here, not in components.
 */

/** Format an ISO timestamp to a short human-readable string. */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** Format a timestamp as a relative string (e.g. "2 min ago"). */
export function formatRelative(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;

  const diff = Math.floor((now - then) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/** Format a fraction (0–1) as a percentage string. */
export function formatPercent(value: number | null, decimals = 0): string {
  if (value === null) return '—';
  return `${(value * 100).toFixed(decimals)}%`;
}

/** Format a numeric count with locale separators. */
export function formatCount(n: number | null): string {
  if (n === null) return '—';
  return n.toLocaleString('en-US');
}

/** Format a rulebook version for display. */
export function formatVersion(version: number): string {
  return `v${version}`;
}

/** Truncate a long string with ellipsis, preserving the start. */
export function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return `${str.slice(0, maxLen - 1)}…`;
}

/** Format a job progress fraction as e.g. "42 / 170". */
export function formatProgress(processed: number | null, total: number | null): string {
  if (processed === null || total === null) return '—';
  return `${formatCount(processed)} / ${formatCount(total)}`;
}

/** Format bytes as human-readable size. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}
