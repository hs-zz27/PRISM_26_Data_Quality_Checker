# PRISM Data Quality Checker — Frontend

Scientific workstation interface for AI-assisted human-in-the-loop review of computer vision dataset annotations (Penn-Fudan pedestrians, 1-based inclusive coordinates, segmentation masks, and classification rules).

---

## 1. Quick Start

### Prerequisites
- Node.js >= 18 (developed with Node v22.22.1, npm 9.2.0)
- Backend running at `http://127.0.0.1:8000` (optional in mock mode)

### Install Dependencies
```bash
cd frontend
npm install
```

### Start Development Server
```bash
npm run dev
```
The workstation opens at `http://localhost:5173`.

### Run Unit Tests
```bash
npm test
```
Runs the frontend unit suite.

### Typecheck & Build
```bash
npm run typecheck
npm run build
```

### OpenAPI type drift

Regenerate both generated type files after a contract change, then review the diff:

```bash
npm run gen:api
```

Check that committed generated types match both backend contracts without changing files:

```bash
npm run check:api
```

### Design preview

The optical-bench visual system is documented in [DESIGN_SPEC.md](DESIGN_SPEC.md).
The development-only component reference is available at `/dev/styleguide`.
IBM Plex Sans and IBM Plex Mono are bundled locally with `@fontsource` packages.

---

## 2. Dual-Mode Transport Architecture

All API requests go through `src/api/client.ts` (`/api/v1` base URL).

- **Implemented Operations (46 endpoints)**:
  - Proxied via Vite development proxy (`/api/v1` → `http://127.0.0.1:8000`).
  - Covers rulebooks (guidelines, extractions, versions, confirmation, revisions), datasets (ZIP upload, catalogues, samples, assets), batch quality audits, and reviewer finding/sample decisions.
- **Planned Operations (4 endpoints)**:
  - Intercepted transparently in the browser by Mock Service Worker (`src/mocks/browser.ts` & `src/mocks/handlers.ts`).
  - Serves synthetic fixtures loaded directly from `backend/api/examples.json`.
  - A **Mock** badge appears only after a response identifies itself as mocked.

---

## 3. Reviewer workflow

The screens follow the worklet's human-in-the-loop flow. The **Overview** (`/`) shows the five steps and highlights the next one.

- **Rulebook editor.** Citations are highlighted wherever their exact text appears in the guideline. Reviewers can confirm or reject each rule, edit a rule's description, check, parameters or citation, add a missed rule (highlight a sentence to use it as the citation), and **Re-check document** to list extraction proposals that are not already in the draft. **Confirm rulebook** stays disabled until every rule has a decision; rejected rules are saved out of the draft before confirmation, because the backend only confirms books whose retained rules are all confirmed.
- **Review workstation.** Each finding shows the five explainability fields: the rule, the result (in plain language), the check and its threshold, the measured evidence, and the guideline citation (or a note that the rule was reviewer-authored). Findings are grouped into *Needs attention*, *Pre-ticked by the tool* and *Rules with no result for this sample*. Overriding a finding, rejecting a sample, or sending it for correction requires a reason; the UI blocks the write until one is typed. The header shows review progress and a **Next unreviewed** jump. **Correct** opens the sample in the correction editor.
- **Audit report** (`/audits/:id/report`). Summary tiles, tool-result breakdown, per-rule pass/fail with reviewer agreement, and a per-sample table. Exports to CSV (formula-safe) and JSON, and prints cleanly. “Reviewer agreed” is agreement within the audit, not a measured accuracy.
- **Corrections.** Deep-linkable via `?dataset=&sample=&audit=`. Boxes can be moved or resized from their corners, objects are validated against the correction contract before saving, and the source audit is recorded only when it pinned the same revision (avoiding `SOURCE_AUDIT_MISMATCH`). A re-check links straight to the result.
- **Exports.** Choose a completed audit and the selection (`accepted_only` or `all_reviewed`). The manifest is shown as a table with plain-language exclusion reasons.
- **Feedback.** Pick up to four approved examples, then build held-out ground truth from an audit's reviewer decisions (agree keeps the tool's result, override flips it; every case is editable). Evaluate baseline against candidate in a metrics table and activate explicitly. Activated policies can be selected when launching an audit.

Plain-language labels for backend codes live in `src/lib/labels.ts`.

## 4. Keyboard Ergonomics

The Review Workstation is keyboard-first. Press `?` anywhere to bring up the interactive help dialog:

| Key | Action | Context |
|---|---|---|
| `A` / `R` / `U` | **Finding decision** | Agree with, override (reason required), or mark the selected finding unsure |
| `Shift+A` / `Shift+R` / `Shift+N` | **Sample verdict** | Accept, reject (reason required), or send the sample for correction (reason required) |
| `→` / `J` | **Next Sample** | Advances to next sample in queue |
| `←` / `K` | **Previous Sample** | Returns to previous sample in queue |
| `M` | **Toggle Mask** | Toggles segmentation mask overlay |
| `B` | **Toggle Bounding Boxes** | Toggles 1-based box overlays |
| `F` | **Fit to View** | Resets canvas zoom and centers image |
| `Space` + drag | **Pan** | Moves the image in the canvas |
| `+` / `-` | **Zoom In / Out** | Scales canvas viewport |
| `?` | **Shortcut Help** | Displays modal with all keyboard bindings |

*Note: Keyboard shortcuts are automatically suppressed when typing inside any text input or comment field.*

---

## 5. Coordinate System Convention

The backend contract requires **1-based inclusive** bounding box coordinates `[xmin, ymin, xmax, ymax]`.
CSS rendering uses zero-based canvas pixel coordinates:
- `left = xmin - 1`
- `top = ymin - 1`
- `width = xmax - xmin + 1`
- `height = ymax - ymin + 1`

All coordinate math is isolated and tested in `src/lib/coords.ts` (`coords.test.ts`). Never inline coordinate math inside UI components.

---

## 6. Folder Map

```
frontend/
├── index.html                   # Dark-mode HTML entry point
├── package.json                 # Dependencies & scripts
├── tailwind.config.ts           # Token definitions matching CSS custom properties
├── vite.config.ts               # Vite bundler & /api/v1 proxy configuration
├── vitest.config.ts             # Vitest jsdom test runner
└── src/
    ├── main.tsx                 # App mount & dual-mode transport initialization
    ├── App.tsx                  # Client router across all hubs
    ├── api/
    │   ├── client.ts            # Typed fetch wrapper with ApiError envelopes
    │   ├── queryClient.ts       # Centralized TanStack Query client & query keys
    │   ├── transport.ts         # Dual-mode router (planned vs implemented)
    │   └── generated/           # OpenAPI generated TypeScript definitions
    ├── components/
    │   ├── layout/              # Navbar, Sidebar, AppLayout
    │   └── ui/                  # Badge, Button, MockBadge, ReviewerPromptModal, KeyboardHelpModal
    ├── features/
    │   ├── rulebooks/           # RulebooksPage & RulebookEditorPage (citations & freeze)
    │   ├── datasets/            # DatasetsPage & DatasetDetailPage (ZIP upload & sample cards)
    │   ├── overview/            # OverviewPage — workflow steps and next action
    │   ├── audits/              # AuditsPage, AuditReviewWorkspace (3-pane), FindingCard, AuditReportPage
    │   ├── corrections/         # CorrectionsPage (annotation revisions & rechecks)
    │   ├── exports/             # ExportsPage (filtered packaging & manifests)
    │   └── feedback/            # FeedbackPage (ground-truth benchmarks & F1 metrics)
    ├── hooks/
    │   └── useJobPolling.ts     # Universal 6-state job polling hook
    ├── lib/
    │   ├── coords.ts            # 1-based ↔ CSS coordinate math & validation
    │   ├── format.ts            # Display formatters for dates, bytes, counts
    │   └── keyboard.ts          # Central shortcut registry and text-target guards
    ├── mocks/
    │   ├── browser.ts           # MSW worker initialization
    │   └── handlers.ts          # MSW synthetic fixture handlers
    ├── store/
    │   ├── reviewerStore.ts     # Reviewer attribution (localStorage persistence)
    │   └── canvasStore.ts       # Canvas zoom, pan, layer toggles, mask opacity
    └── styles/
        ├── tokens.css           # Single source of truth CSS custom properties
        └── globals.css          # Tailwind base layers, typography, custom scrollbars
```

## Integrated demo

See [DEMO_WORKFLOW.md](DEMO_WORKFLOW.md) for required API/worker processes and the
real import → audit → review → correction → recheck → export acceptance checklist.
The enclosure check now displays per-box model proposals separately from human decisions.
