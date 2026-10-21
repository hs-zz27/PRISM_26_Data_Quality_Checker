# PRISM visual design spec

## Identity

PRISM is an optical bench for reviewing computer-vision annotations. Its interface should feel like a calibrated instrument: the chrome is quiet and exact, while image evidence, measurements, and reviewer decisions carry the visual weight. The recognizable refraction mark is a thin, low-saturation spectrum used only in the PRISM mark, active navigation edge, and running-job progress.

## Tokens

| Token | Value | Use |
| --- | --- | --- |
| `--color-base` | `#0A0D0F` | App background |
| `--color-surface` | `#101417` | Main panels |
| `--color-raised` | `#161B1F` | Menus and selected rows |
| `--color-sunken` | `#0C1012` | Inputs and image surround |
| `--color-line` | `rgb(255 255 255 / 10%)` | Hairline dividers |
| `--color-text` | `#E6EAEC` | Primary text |
| `--color-text-secondary` | `#9AA4AA` | Supporting text |
| `--color-text-muted` | `#5F6A71` | Labels and quiet metadata |
| `--color-accent` | `#45C6C2` | Selected, active, machine-proposed |
| `--color-pass` | `#71B79A` | Pass marker |
| `--color-fail` | `#D77B78` | Fail marker |
| `--color-unsure` | `#C6A96A` | Unsure marker |
| `--color-unavailable` | `#7D888D` | Unavailable marker |

The spectral line is an isolated named token and may appear only in the three signature locations. No other gradients, purple/indigo UI colors, static panel shadows, or component-level hex values are allowed.

## Typography and spacing

- IBM Plex Sans: interface copy, body at 13px, dense table copy at 12px, section labels at 11px, page titles at 19px / weight 500.
- IBM Plex Mono: IDs, coordinates, counts, metrics, and timestamps, with tabular numerals.
- Font packages: `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono` are included so both families are self-hosted and do not depend on a CDN.
- Playwright: added as a dev dependency to capture and verify the requested 1440 × 900 and 1920 × 1080 screenshots.
- 4px spacing grid. Compact controls are 28–32px tall; top bar is 40px; icon rail is 48px and expands to 200px when pinned.
- Inputs and badges use 2px corners; buttons and panels use 4px corners. No pill controls.
- Motion is limited to 120–160ms action feedback and respects reduced-motion settings.

## Layout plan

All content is left-aligned and uses the available viewport width. The shell uses a narrow rail, compact breadcrumb bar, and flush work regions separated by translucent hairlines. Lists use dense tables; only image collections use grids. The review screen reserves the center for the image, with resizable queue and inspector rails.

```text
┌─ 48 rail ─┬──────────────────── 40px breadcrumb / readiness / reviewer ──┐
│           ├───────────────────────────────────────────────────────────────┤
│ icons     │ route toolbar: title · count · filters · primary action       │
│           ├───────────────────────────────────────────────────────────────┤
│           │ full-width work area, hairline-divided                        │
└───────────┴───────────────────────────────────────────────────────────────┘
```

```text
┌─ 264 queue ─┬────── image canvas / compact tool strip ──────┬─ 360 inspector ─┐
│ sample rows  │ centered evidence, dark surround, live coords │ findings/objects │
│ filters      │                                                │ verdict/history  │
└──────────────┴───────────────────────────────────────────────┴─────────────────┘
```

## Shared component inventory

- App rail, breadcrumb bar, readiness chip, reviewer control, command palette, job tray, shortcut help.
- Compact page toolbar, segmented filters, dense data table, selectable row, count chip.
- Status marker with icon and text, mock-source badge, inline error, empty state, shaped skeleton.
- Small primary/quiet/danger buttons, sunken text and numeric fields, tabs, hairline divider.
- Review canvas toolbar, coordinate readout, inspector section, evidence quote, decision controls.

## Per-screen layouts

- **Review:** resizable 264px queue, dominant canvas, resizable 360px inspector with Findings / Objects / Verdict / History; sample verdict remains visually distinct from finding decisions.
- **Rulebooks:** prose and linked citations on the left, expandable dense rule rows on the right, version and reviewer history drawer.
- **Datasets:** table catalogue; selected dataset becomes a dense thumbnail grid with an evidence drawer; ZIP import is a progress-aware dialog.
- **Audits:** compact run table with pins, lifecycle, progress and counts; launch is a three-step right drawer.
- **Corrections:** image-led annotation editor with synchronized object table, mask staging, reason, and explicit diff.
- **Exports:** history table and detail drawer with manifest evidence and clear download readiness.
- **Feedback:** real-state horizontal workflow from examples through activation; measured comparison table or an honest empty state.
- **Styleguide:** `/dev/styleguide` displays shared tokens and components in populated, loading, empty, error, selected, and status states.

## Design constraints

Status always has icon, text, and a restrained marker color. No fabricated metrics or placeholder content. Keep API request behavior and review authority unchanged. Every state should explain the next useful action in domain-specific language.
