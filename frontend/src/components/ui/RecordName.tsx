import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import {
  auditLabel, auditTitle, rulebookLabel, rulebookTitle,
  type AuditName, type RulebookName,
} from '../../lib/names';

/** Label first, then the title. Long titles truncate; the full text stays in the tooltip. */
function LabelAndTitle({ label, title, className = '' }: { label: string; title: string | null; className?: string }) {
  return <span className={`inline-flex min-w-0 max-w-full items-baseline gap-1.5 ${className}`} title={title ? `${label} | ${title}` : label}>
    <span className="shrink-0 font-semibold text-text whitespace-nowrap">{label}</span>
    {title && <span className="min-w-0 truncate text-text-muted">{title}</span>}
  </span>;
}

export function AuditNameText({ name, className, showTitle = true }: {
  name: AuditName | null | undefined; className?: string; showTitle?: boolean;
}) {
  return <LabelAndTitle label={auditLabel(name)} title={showTitle ? auditTitle(name) : null} className={className} />;
}

export function RulebookNameText({ name, version, className, showTitle = true }: {
  name: RulebookName | null | undefined; version?: number | null; className?: string; showTitle?: boolean;
}) {
  return <LabelAndTitle label={rulebookLabel(name, version)} title={showTitle ? rulebookTitle(name) : null} className={className} />;
}

/** A clearly labelled internal ID for troubleshooting, with a copy control. */
export function TechnicalId({ label, value }: { label: string; value: string | null | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return <span className="inline-flex min-w-0 max-w-full items-center gap-1 text-2xs text-text-muted">
    <span className="shrink-0">{label}</span>
    <code className="min-w-0 truncate font-mono" title={value}>{value}</code>
    <button type="button" className="shrink-0 rounded p-0.5 hover:text-text" onClick={copy} aria-label={`Copy ${label}`}>
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  </span>;
}
