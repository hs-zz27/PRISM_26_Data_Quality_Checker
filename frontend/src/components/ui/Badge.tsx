import React from 'react';
import {
  CheckCircle2,
  XCircle,
  HelpCircle,
  MinusCircle,
  Clock,
  Wrench,
  FlaskConical,
  Info,
} from 'lucide-react';

export type BadgeVariant =
  | 'pass'
  | 'fail'
  | 'unsure'
  | 'unavailable'
  | 'pending'
  | 'accepted'
  | 'rejected'
  | 'needs-fix'
  | 'mock'
  | 'neutral'
  | 'info';

interface BadgeProps {
  variant: BadgeVariant;
  children?: React.ReactNode;
  icon?: boolean;
  className?: string;
  size?: 'sm' | 'md';
}

const variantConfig: Record<
  BadgeVariant,
  { label: string; icon: React.ComponentType<{ className?: string }>; style: string }
> = {
  pass: {
    label: 'Pass',
    icon: CheckCircle2,
    style: 'bg-status-pass/10 text-status-pass border-status-pass/30',
  },
  accepted: {
    label: 'Accepted',
    icon: CheckCircle2,
    style: 'bg-status-accepted/10 text-status-accepted border-status-accepted/30',
  },
  fail: {
    label: 'Fail',
    icon: XCircle,
    style: 'bg-status-fail/10 text-status-fail border-status-fail/30',
  },
  rejected: {
    label: 'Rejected',
    icon: XCircle,
    style: 'bg-status-rejected/10 text-status-rejected border-status-rejected/30',
  },
  unsure: {
    label: 'Unsure',
    icon: HelpCircle,
    style: 'bg-status-unsure/10 text-status-unsure border-status-unsure/30',
  },
  'needs-fix': {
    label: 'Needs Fix',
    icon: Wrench,
    style: 'bg-status-needs-fix/10 text-status-needs-fix border-status-needs-fix/30',
  },
  unavailable: {
    label: 'Unavailable',
    icon: MinusCircle,
    style: 'bg-status-unavailable/10 text-status-unavailable border-status-unavailable/30',
  },
  pending: {
    label: 'Pending',
    icon: Clock,
    style: 'bg-status-pending/10 text-status-pending border-status-pending/30',
  },
  mock: {
    label: 'Mock',
    icon: FlaskConical,
    style: 'bg-unsure/10 text-unsure border-unsure/30',
  },
  info: {
    label: 'Info',
    icon: Info,
    style: 'bg-accent/10 text-accent border-accent/30',
  },
  neutral: {
    label: 'Neutral',
    icon: MinusCircle,
    style: 'bg-surface-high text-text-muted border-border',
  },
};

export const Badge: React.FC<BadgeProps> = ({
  variant,
  children,
  icon = true,
  className = '',
  size = 'md',
}) => {
  const config = variantConfig[variant];
  const IconComponent = config.icon;
  const sizeClasses = size === 'sm' ? 'px-1.5 py-0.5 text-2xs' : 'px-2 py-0.5 text-xs';
  const iconSizeClasses = size === 'sm' ? 'w-3 h-3' : 'w-3.5 h-3.5';

  return (
    <span
      className={`inline-flex items-center gap-1.5 font-mono font-medium rounded border tracking-tight ${config.style} ${sizeClasses} ${className}`}
    >
      {icon && <IconComponent className={iconSizeClasses} aria-hidden="true" />}
      <span>{children ?? config.label}</span>
    </span>
  );
};
