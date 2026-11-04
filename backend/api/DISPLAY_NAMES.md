# Readable audit and rulebook names

People see permanent sequential labels instead of internal IDs:

| Record | Label | With title |
|---|---|---|
| Root audit | `Audit 014` | `Audit 014 \| LabelMe demo` |
| First recheck of Audit 014 | `Audit 014 R01` | `Audit 014 R01 \| Feedback recheck` |
| Rulebook (every version) | `Rules 003`, shown as `Rules 003 v3` | `Rules 003 v3 \| Person annotation checks` |

Numbers have at least three digits and grow without truncation (`Audit 1000`).
Recheck suffixes have at least two digits (`R99`, `R100`). A recheck of a
recheck keeps the original root's number and takes the root's next suffix.

Internal IDs (`audit_…`, `rulebook_…`) remain the only identifiers for
requests, routes, relationships and exports. Never parse a label to recover an
ID; the label format is for people and may gain detail later.

## What is permanent and what is editable

- The number, recheck suffix and root are allocated once by the server and never
  change, are never reused and are never deleted. A database trigger rejects any
  attempt to change or delete them.
- The title is editable. Editing it changes nothing else: no rulebook version,
  review, rule, citation, confirmation state, audit request, finding, sample
  review, correction or recheck.

## Default titles

Titles are derived from stored data at allocation time. No model is used.

| Record | Default title | Fallback |
|---|---|---|
| Root audit | The dataset name (dataset names cannot change) | `Untitled audit` when the dataset row is absent (legacy audits) |
| Recheck | `Feedback recheck` when the recheck carried reviewer feedback, otherwise `Recheck` | — |
| Rulebook | The first nonblank guideline line, without a leading list marker such as `1.`, `(2)`, `3)`, `-`, `*` or `•`, with whitespace collapsed and shortened to 80 characters (`…`) | `Untitled guidelines` |

A rulebook's default title comes from its first version and does not follow later
edits to the guideline text. Rename it to change it.

The frontend shows `Unnumbered audit` or `Unnumbered rules` only for a record
that has no name at all, which happens only on a database without migration 016.

## Allocation design

- Names live in their own tables (`audit_names`, `rulebook_names`). Audit runs,
  rulebook documents, pinned audit requests, rule fingerprints, feedback policy
  fingerprints, frozen exports and manifests are not touched, so no hash changes.
- Numbers come from PostgreSQL sequences, never from `MAX(number) + 1`. Concurrent
  creations get distinct numbers. A rolled-back creation can leave a gap; gaps
  are expected and numbers are never reused.
- Allocation is a deferred trigger that runs when the creating transaction
  commits, so every insert path is covered. A recheck's lineage row exists by then,
  so the trigger can tell a recheck from a root audit.
- Recheck suffixes come from a counter on the root's name row, updated under its
  row lock, so concurrent rechecks of one root get distinct suffixes.
- Reads never allocate or modify names. A retry or restart reads the stored name.

## Existing records (migration 016)

The migration names every existing record in one transaction, so it either completes
or leaves the database untouched, and it can be rerun after a failure.

- Root audits are numbered by `created_at`, then audit ID (byte order), from 001.
- Rechecks take their root's number. Suffixes are assigned within each root by
  `created_at`, then audit ID, following lineage through rechecks of rechecks.
- Rulebooks get one number per logical book, ordered by the earliest recorded
  time: the extraction job's submission time, or failing that the first review
  time. Books with no recorded time (manually created and never reviewed) follow,
  ordered by rulebook ID. No timestamp is invented: `first_recorded_at` stays
  null for those books.
- New records continue after the highest backfilled number.

## API

Audit responses (`AuditRun`: get, list, start, recheck, title edit) include:

- `name`: `AuditName` with `audit_id`, `root_audit_id`, `number`, `recheck_number`
  (null for a root), `label`, `title`, `title_revision`, `title_updated_at`,
  `title_updated_by`.
- `rulebook_name`: the audited rulebook's `RulebookName`. Pair it with
  `rulebook_version`; the label does not include a version.

Rulebook responses (`RulebookRecord` and `RulebookRecordPage`) include `name`, a
`RulebookName` shared by every version. The stored rulebook document is
unchanged. The `name` field is display metadata only and the server rejects it
in rulebook request bodies.

When a record has no name, the field is omitted rather than invented.

| Operation | Purpose |
|---|---|
| `PUT /api/v1/audits/{audit_id}/title` | Rename an audit |
| `PUT /api/v1/rulebooks/{rulebook_id}/title` | Rename a rulebook (all versions) |
| `GET /api/v1/display-names?audit_id=…&rulebook_id=…` | Labels for IDs held by other records |

Title edits send `{"title", "expected_title_revision", "reviewer_id"}`. The title
is trimmed and must be 1-120 characters without line breaks or control
characters. A stale `expected_title_revision` returns 409 `VERSION_CONFLICT`
without writing. Each edit is recorded in `display_title_edits` with the
reviewer and time. Renaming does not need, and does not create, an annotation
correction.

The lookup takes up to 100 IDs of each kind, repeating the parameter
(`?audit_id=a&audit_id=b`). It omits unknown IDs, returns duplicates once and
keeps request order. Use it for findings, revisions (`source_audit_id`),
exports, sample attempts and feedback examples.

## Frontend

`src/lib/names.ts` formats every label (`auditLabel`, `auditHeading`,
`rulebookLabel`, `rulebookHeading`). `src/hooks/useDisplayNames.ts` batches
lookups, `src/components/ui/RecordName.tsx` renders label and title (long titles
truncate, with the full text in the tooltip) plus a labelled technical-ID copy
control, and `src/features/names/TitleEditor.tsx` renames.

Labels appear in the dashboard, audit list, audit review workspace (header,
attempt history, recheck notices), report (heading, CSV/JSON file names and a
`result_audit_label` column appended after the existing columns), rulebook
list, editor and notices, audit creation, correction, export and feedback
selectors, revision history, export history and manifests, feedback example
cards, the job tray, breadcrumbs and the command palette (which also matches
internal IDs).

Intentional technical IDs:

- Routes, query parameters and request bodies, which must use IDs.
- The labelled "Audit ID" and "Rulebook ID" copy controls.
- Tooltips on labels in history, export and feedback cards.
- The report CSV `result_audit_id` column and the JSON `audit` object.
- Export IDs, ground-truth IDs and policy IDs, which are outside this naming scheme.

The export archive (`manifest.json`, `README.txt`) is unchanged. It is frozen,
machine-readable input, so labels are not added to it.

## Owner steps

Integration verification on 4 November 2026 combined this change with PR #19's
rule applicability and focused visual rechecks. Both rule model fields are
preserved, and regenerating the API contracts and frontend types changes no files.
The full backend run passed 3,573 tests and exposed one obsolete legacy-migration
fixture. That fixture now seeds the pre-014 schema directly, checks all original
fields for preservation, and checks the added format defaults separately. The
follow-up PostgreSQL run passed all 48 dataset-migration and display-name tests.
Frontend verification passed 158 tests, ESLint and the production build, including
TypeScript checking. Database tests used isolated schemas; no live review records
were migrated during these checks. No model inference was required.

Back up the database first (it holds real reviews):

```sh
docker compose exec postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > prism-before-016.sql
```

Pull, rebuild and start. Starting the stack runs the `migrate` service, which applies
016. A failed migration blocks the API and workers and leaves the data unchanged.

```sh
git pull
docker compose up --build -d
docker compose logs --tail=100 migrate     # exit status 0 is normal
```

Check that existing records are named:

```sh
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1;"'
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT n.display_number, n.recheck_number, n.title, r.created_at FROM audit_names n JOIN audit_runs r USING (audit_id) ORDER BY 1, 2 NULLS FIRST;"'
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT display_number, title, first_recorded_at FROM rulebook_names ORDER BY 1;"'
```

The first query should print 16. Then open http://localhost:5173: the dashboard,
Audits and Rulebooks pages should show `Audit NNN` and `Rules NNN` labels for old
records. Restarting the stack does not rerun or renumber anything.
