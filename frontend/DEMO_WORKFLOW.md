# Local integrated demo

Use the integration branch containing both `feat/frontend` and merged backend `main`.
The API alone accepts jobs; the matching workers must run to process them.
All shell commands below are run from the checkout root unless stated otherwise.
Keep Docker running for PostgreSQL. Use the backend setup guide for database configuration
and migrations; do not point a prototype demo at a production database.

## Start the processes

Start the API in one terminal:

```bash
/Users/harkamalsinghlubana/Desktop/proj/cdif/.venv/bin/python -B -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Start each required worker in a separate terminal using the same environment and private
storage configuration as the API. The absolute Python path below reuses the owner's
existing virtual environment; other developers should use their own environment's Python.

```bash
/Users/harkamalsinghlubana/Desktop/proj/cdif/.venv/bin/python -B -m backend.app.workers.dataset_import
```

```bash
/Users/harkamalsinghlubana/Desktop/proj/cdif/.venv/bin/python -B -m backend.app.workers.rule_extraction
```

```bash
/Users/harkamalsinghlubana/Desktop/proj/cdif/.venv/bin/python -B -m backend.app.workers.audit
```

```bash
/Users/harkamalsinghlubana/Desktop/proj/cdif/.venv/bin/python -B -m backend.app.workers.export
```

The feedback worker is needed only for feedback comparisons:

```bash
/Users/harkamalsinghlubana/Desktop/proj/cdif/.venv/bin/python -B -m backend.app.workers.feedback
```

Start Ollama for model-backed extraction or checks. Enable enclosure deliberately with
the settings in `backend/ENCLOSURE_CHECK.md`; it is disabled by default. Choose a confirmed
`pedestrian_box_enclosure` rule explicitly; generic visual rules do not switch methods
based on their wording. Do not attach a feedback policy to an enclosure rulebook.

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api/v1` to the local backend, including
asset and download requests. Planned operations may use development mocks; use the
transport indicator and browser Network panel to verify real requests. Never treat
mock responses as evidence that the integrated workflow succeeded.

## Completion checklist

1. Set a reviewer ID. Import a Penn-Fudan ZIP. Its import job must finish and the dataset
   must become ready; refresh after the job if necessary.
2. Extract a guideline, inspect every rule and parameter, and confirm its rulebook.
   For enclosure, explicitly select the check and verify server configuration.
3. Start an audit against the ready dataset and confirmed rulebook. Check that polling
   progresses to completion. A queued job with no progress usually means its worker
   is missing; check worker output before creating another job.
4. Open the review workspace. Confirm the displayed image uses the audited revision.
   For enclosure findings, inspect every box's verdict, reason, coordinates and verification
   status. These are model proposals, separate from human review decisions. A failing box
   can make a sample fail even when other boxes could not be checked.
5. Agree, override or mark findings unsure. Provide required reasons. Record the sample
   decision separately, then reload and verify that both review types persisted.
6. Send a sample for correction, save changed annotations as a new revision, and start a
   linked recheck. Confirm the child uses the new revision while the parent evidence and
   reviews remain intact. Review decisions are not inherited as new human decisions.
7. Create an export with the intended selection, wait for its job and download the artifact.
   Verify rejected or pending samples are excluded when the chosen selection requires it.

Completion means the actual browser actions persisted to the backend and the downloaded
artifact contains the selected reviewed revisions. An empty screen, build success,
synthetic fixtures or passing unit tests alone do not complete this checklist.

## Validation of this integration pass

Source inspection confirmed API calls for import, rule confirmation, audit submission,
job polling, finding/sample reviews, corrections, linked rechecks and exports. The
new enclosure evidence display has authored regression tests. Type checking, a production build and API-type freshness checks passed. The frontend suite subsequently passed all 94 tests. The owner completed browser
import, rulebook confirmation, audit, sample reviews and reviewed export. The
correction/recheck path was subsequently validated through live HTTP endpoints
and the real workers, but its browser automation disconnected at file upload. No new inference is needed for frontend tests.

## Correction/recheck validation record

A separate one-sample dataset with deliberately invalid labels was imported as
`CDIF correction validation (synthetic label)` under dataset
`dataset_ed6738b0eede4794b2ae247cdaf2517c`. The accepted original dataset was untouched.
Parent audit `audit_f3096535d2844b6581558cc5fea44846` recorded two label failures.
A correction restored the original `PASpersonWalking` labels in revision 2; linked
child audit `audit_abd31ae0fc6e4408b83f00363539dc12` completed with four passing
findings. Child finding reviews remained pending, and the parent audit detail and
revision-1 sample responses remained unchanged. This was a live API/worker check,
not a substitute for the remaining browser interaction check. No model calls were made.
