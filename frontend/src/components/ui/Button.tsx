import React from 'react';
import { Loader2 } from 'lucide-react';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'outline' | 'subtle';
export type ButtonSize = 'sm' | 'md' | 'lg' | 'icon';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  leftIcon?: React.ReactNode;
  rightIcon?: React.ReactNode;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-base-pure font-semibold hover:bg-accent-dim focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-base border border-transparent shadow-sm',
  secondary:
    'bg-surface-high text-text border border-border hover:bg-surface-high/80 hover:border-border-muted focus-visible:ring-2 focus-visible:ring-accent',
  outline:
    'bg-transparent text-text border border-border hover:border-accent hover:text-accent focus-visible:ring-2 focus-visible:ring-accent',
  ghost:
    'bg-transparent text-text-muted hover:text-text hover:bg-surface-high border border-transparent focus-visible:ring-2 focus-visible:ring-accent',
  danger:
    'bg-status-fail/15 text-status-fail border border-status-fail/30 hover:bg-status-fail/25 focus-visible:ring-2 focus-visible:ring-status-fail',
  subtle:
    'bg-surface text-text-muted hover:text-text border border-border/50 hover:border-border',
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'text-xs px-2.5 py-1 rounded gap-1.5 h-7',
  md: 'text-sm px-3.5 py-1.5 rounded gap-2 h-9',
  lg: 'text-base px-5 py-2.5 rounded gap-2.5 h-11',
  icon: 'p-1.5 rounded h-8 w-8 justify-center items-center',
};

export const Button: React.FC<ButtonProps> = ({
  variant = 'secondary',
  size = 'md',
  isLoading = false,
  leftIcon,
  rightIcon,
  children,
  className = '',
  disabled,
  ...props
}) => {
  return (
    <button
      disabled={disabled || isLoading}
      className={`inline-flex items-center justify-center font-medium transition-colors select-none disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none cursor-pointer ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...props}
    >
      {isLoading ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : (
        <>
          {leftIcon}
          {children}
          {rightIcon}
        </>
      )}
    </button>
  );
};
