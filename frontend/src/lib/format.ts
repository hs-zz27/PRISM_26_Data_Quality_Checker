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

/** Truncate a long string with ellipsis, preserving the start. */
export function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return `${str.slice(0, maxLen - 1)}…`;
}

