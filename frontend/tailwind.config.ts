import type { Config } from 'tailwindcss';

/** Token-backed colour that still supports Tailwind opacity modifiers (e.g. `bg-status-fail/10`). */
const tokenColor = (token: string) => `color-mix(in srgb, var(${token}) calc(<alpha-value> * 100%), transparent)`;

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      // ─── Colors — defined once here, mirror CSS vars in tokens.css ───
      colors: {
        // Base backgrounds
        base: {
          DEFAULT: 'var(--color-base)',
          canvas: 'var(--color-base-canvas)',
          sunken: 'var(--color-sunken)',
          pure: 'var(--color-base-canvas)',
        },
        // Status colours used by badges, alerts and decision buttons.
        status: {
          pass: tokenColor('--color-pass'),
          fail: tokenColor('--color-fail'),
          unsure: tokenColor('--color-unsure'),
          unavailable: tokenColor('--color-unavailable'),
          accepted: tokenColor('--color-accepted'),
          rejected: tokenColor('--color-rejected'),
          pending: tokenColor('--color-pending'),
          'needs-fix': tokenColor('--color-needs-fix'),
        },
        surface: {
          DEFAULT: 'var(--color-surface)',
          high: 'var(--color-surface-high)',
          raised: 'var(--color-surface-raised)',
        },
        border: {
          DEFAULT: 'var(--color-border)',
          muted: 'var(--color-border-muted)',
          strong: 'var(--color-border-strong)',
        },
        text: {
          DEFAULT: 'var(--color-text)',
          muted: 'var(--color-text-muted)',
          faint: 'var(--color-text-faint)',
        },
        // Accent / interactive
        accent: {
          DEFAULT: tokenColor('--color-accent'),
          dim: 'var(--color-accent-dim)',
        },
        // Machine finding outcomes
        pass: {
          DEFAULT: 'var(--color-pass)',
          bg: 'var(--color-pass-bg)',
        },
        fail: {
          DEFAULT: 'var(--color-fail)',
          bg: 'var(--color-fail-bg)',
        },
        unsure: {
          DEFAULT: 'var(--color-unsure)',
          bg: 'var(--color-unsure-bg)',
        },
        unavailable: {
          DEFAULT: 'var(--color-unavailable)',
          bg: 'var(--color-unavailable-bg)',
        },
        // Review decisions (separate concept from machine outcomes)
        decision: {
          accepted: 'var(--color-accepted)',
          rejected: 'var(--color-rejected)',
          pending: 'var(--color-pending)',
          'needs-fix': 'var(--color-needs-fix)',
        },
        // Job status
        job: {
          queued: 'var(--color-job-queued)',
          running: 'var(--color-job-running)',
          completed: 'var(--color-job-completed)',
          failed: 'var(--color-job-failed)',
          cancelled: 'var(--color-job-cancelled)',
          cancelling: 'var(--color-job-cancelling)',
        },
      },
      // ─── Typography ───────────────────────────────────────────────
      fontFamily: {
        sans: ['IBM Plex Sans', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        '3xs': ['9px', { lineHeight: '12px' }],
        '2xs': ['10px', { lineHeight: '14px' }],
        xs: ['11px', { lineHeight: '16px' }],
        sm: ['12px', { lineHeight: '18px' }],
        base: ['13px', { lineHeight: '20px' }],
        md: ['14px', { lineHeight: '22px' }],
        lg: ['15px', { lineHeight: '24px' }],
        xl: ['16px', { lineHeight: '26px' }],
        '2xl': ['18px', { lineHeight: '28px' }],
        '3xl': ['20px', { lineHeight: '30px' }],
      },
      // ─── Spacing — compact, dense workstation scale ───────────────
      spacing: {
        px: '1px',
        '0.5': '2px',
        '1': '4px',
        '1.5': '6px',
        '2': '8px',
        '2.5': '10px',
        '3': '12px',
        '4': '16px',
        '5': '20px',
        '6': '24px',
        '8': '32px',
        '10': '40px',
        '12': '48px',
        '16': '64px',
        '20': '80px',
        '24': '96px',
      },
      // ─── Border radius — small, scientific, not "friendly" ────────
      borderRadius: {
        none: '0px',
        sm: '2px',
        DEFAULT: '4px',
        md: '4px',
        lg: '4px',
        full: '9999px',
      },
      // ─── Box shadow — restrained ──────────────────────────────────
      boxShadow: {
        none: 'none',
        sm: '0 1px 2px 0 rgb(0 0 0 / 0.4)',
        DEFAULT: '0 1px 3px 0 rgb(0 0 0 / 0.5), 0 1px 2px -1px rgb(0 0 0 / 0.5)',
        md: '0 4px 6px -1px rgb(0 0 0 / 0.5), 0 2px 4px -2px rgb(0 0 0 / 0.5)',
        panel: 'none',
        focus: '0 0 0 2px var(--color-accent)',
      },
      // ─── Animation ────────────────────────────────────────────────
      transitionDuration: {
        fast: '100ms',
        base: '150ms',
        slow: '250ms',
      },
      keyframes: {
        'slide-in-right': {
          from: { transform: 'translateX(100%)' },
          to: { transform: 'translateX(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        pulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.4' },
        },
        'progress-indeterminate': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(400%)' },
        },
      },
      animation: {
        'slide-in-right': 'slide-in-right 150ms ease-out',
        'fade-in': 'fade-in 100ms ease-out',
        pulse: 'pulse 2s ease-in-out infinite',
        'progress-indeterminate': 'progress-indeterminate 1.5s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};

export default config;
