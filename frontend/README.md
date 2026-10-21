# PRISM Data Quality Checker — Frontend

Scientific workstation interface for AI-assisted human-in-the-loop review of computer vision dataset annotations (Penn-Fudan pedestrians, 1-based inclusive coordinates, segmentation masks, and classification rules).

## Optical-bench redesign captures

Captured from the local Vite app at 1440×900 and 1920×1080. Review workspace also
has a 1280×900 layout capture. The local database used for this pass had two
invalid datasets and no rulebooks or audits, so data-dependent screens show their
real empty, unavailable, or not-found states. These captures document layout and
do not imply successful review mutations.

| Screen | 1440×900 | 1920×1080 |
|---|---|---|
| Audits | [capture](docs/screenshots/audits-1440x900.png) | [capture](docs/screenshots/audits-1920x1080.png) |
| Audit review | [capture](docs/screenshots/review-1440x900.png) | [capture](docs/screenshots/review-1920x1080.png) |
| Rulebooks | [capture](docs/screenshots/rulebooks-1440x900.png) | [capture](docs/screenshots/rulebooks-1920x1080.png) |
| Rulebook editor | [capture](docs/screenshots/editor-1440x900.png) | [capture](docs/screenshots/editor-1920x1080.png) |
| Dataset catalogue | [capture](docs/screenshots/datasets-1440x900.png) | [capture](docs/screenshots/datasets-1920x1080.png) |
| Sample explorer | [capture](docs/screenshots/samples-1440x900.png) | [capture](docs/screenshots/samples-1920x1080.png) |
| Corrections | [capture](docs/screenshots/corrections-1440x900.png) | [capture](docs/screenshots/corrections-1920x1080.png) |
| Exports | [capture](docs/screenshots/exports-1440x900.png) | [capture](docs/screenshots/exports-1920x1080.png) |
| Feedback | [capture](docs/screenshots/feedback-1440x900.png) | [capture](docs/screenshots/feedback-1920x1080.png) |

Review 1280px capture: [review-1280x900.png](docs/screenshots/review-1280x900.png).

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
The development-only component reference is available at `/dev/styleguide`; its
captures are [1440 × 900](docs/screenshots/styleguide-1440x900.png) and
[1920 × 1080](docs/screenshots/styleguide-1920x1080.png).
The command palette shell is captured [here](docs/screenshots/shell-palette-1440x900.png).
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

## 3. Keyboard Ergonomics

The Review Workstation is keyboard-first. Press `?` anywhere to bring up the interactive help dialog:

| Key | Action | Context |
|---|---|---|
| `A` / `R` / `U` | **Finding decision** | Accept, reject, or mark the selected finding proposal unsure |
| `Shift+A` / `Shift+R` / `Shift+N` | **Sample verdict** | Records accepted, rejected, or needs-correction sample verdict |
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

## 4. Coordinate System Convention

The backend contract requires **1-based inclusive** bounding box coordinates `[xmin, ymin, xmax, ymax]`.
CSS rendering uses zero-based canvas pixel coordinates:
- `left = xmin - 1`
- `top = ymin - 1`
- `width = xmax - xmin + 1`
- `height = ymax - ymin + 1`

All coordinate math is isolated and tested in `src/lib/coords.ts` (`coords.test.ts`). Never inline coordinate math inside UI components.

---

## 5. Folder Map

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
    │   ├── audits/              # AuditsPage, ReviewCanvas (high-DPI) & AuditReviewWorkspace (3-pane)
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
