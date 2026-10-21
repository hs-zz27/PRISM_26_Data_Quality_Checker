import React from 'react';
import { Badge } from './Badge';
import { useTransportStore } from '../../store/transportStore';

interface MockBadgeProps {
  className?: string;
  tooltip?: string;
  operationId?: string;
}

/**
 * Unobtrusive "Mock" badge displayed on any screen or component
 * backed by planned mock handlers rather than the live backend.
 */
export const MockBadge: React.FC<MockBadgeProps> = ({
  className = '',
  tooltip = 'This value came from a development fixture.',
  operationId,
}) => {
  const isMock = useTransportStore((state) => operationId ? state.sources[operationId] === 'mock' : false);
  if (!isMock) return null;
  return (
    <span title={tooltip} className="inline-flex">
      <Badge variant="mock" size="sm" className={className}>Mock</Badge>
    </span>
  );
};
