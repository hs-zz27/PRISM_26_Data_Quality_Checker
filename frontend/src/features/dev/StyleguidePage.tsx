import React from 'react';
import { Activity, Check, CircleHelp, Clock3, Database, FlaskConical, MinusCircle, X } from 'lucide-react';
import { Badge } from '../../components/ui/Badge';

const colorTokens = [
  ['Base', '--color-base'], ['Sunken', '--color-sunken'], ['Panel', '--color-surface'], ['Raised', '--color-surface-high'],
  ['Text', '--color-text'], ['Secondary', '--color-text-muted'], ['Muted', '--color-text-faint'], ['Active', '--color-accent'],
  ['Pass', '--color-pass'], ['Fail', '--color-fail'], ['Unsure', '--color-unsure'], ['Unavailable', '--color-unavailable'],
];

export const StyleguidePage: React.FC = () => (
  <div className="styleguide-page">
    <header className="styleguide-header">
      <div>
        <p className="eyebrow">PRISM / SYSTEM REFERENCE</p>
        <h1>Optical bench components</h1>
        <p className="muted">Shared interface parts, tokens, and state treatments.</p>
      </div>
      <span className="mono">DEV ONLY</span>
    </header>

    <section className="styleguide-section">
      <h2>Color tokens</h2>
      <div className="token-grid">
        {colorTokens.map(([label, token]) => (
          <div className="token-swatch" key={token}>
            <span className="token-color" style={{ backgroundColor: `var(${token})` }} />
            <span>{label}</span><code>{token}</code>
          </div>
        ))}
      </div>
      <div className="styleguide-type"><span>IBM Plex Sans · Interface at 13px</span><span className="mono">IBM Plex Mono · 412, 198 · audit_03</span></div>
    </section>

    <section className="styleguide-section">
      <h2>Status markers</h2>
      <div className="styleguide-row">
        <span className="state-label"><Check size={14} /><i className="state-dot pass" />Pass</span>
        <span className="state-label"><X size={14} /><i className="state-dot fail" />Fail</span>
        <span className="state-label"><CircleHelp size={14} /><i className="state-dot unsure" />Unsure</span>
        <span className="state-label"><MinusCircle size={14} /><i className="state-dot unavailable" />Unavailable</span>
        <Badge variant="mock" size="sm">Mock</Badge>
        <span className="ready-chip"><Activity size={14} />API online</span>
        <span className="ready-chip is-unavailable"><Database size={14} />Mask R-CNN unavailable</span>
      </div>
    </section>

    <section className="styleguide-section">
      <h2>Controls</h2>
      <div className="styleguide-row">
        <button className="btn btn-primary">Save changes</button>
        <button className="btn btn-outline">Open history</button>
        <button className="btn btn-ghost">Cancel</button>
        <button className="field-input styleguide-input">Filter samples…</button>
        <span className="count-chip">Count</span>
      </div>
    </section>

    <section className="styleguide-section">
      <h2>State samples</h2>
      <div className="state-grid">
        <div className="state-example"><span className="eyebrow">Loading</span><div className="skeleton styleguide-skeleton" /><div className="skeleton styleguide-skeleton short" /></div>
        <div className="state-example"><span className="eyebrow">Empty</span><p>No samples match these filters.</p><button className="btn btn-ghost">Clear filters</button></div>
        <div className="state-example"><span className="eyebrow">Error state</span><p>Could not load the current queue.</p><button className="btn btn-outline">Retry</button></div>
        <div className="state-example selected-example"><span className="eyebrow">Selected row</span><span className="mono">Selected sample</span><span className="state-label"><Clock3 size={14} /><i className="state-dot unsure" />Needs review</span><FlaskConical size={15} className="muted" /></div>
      </div>
    </section>
  </div>
);
