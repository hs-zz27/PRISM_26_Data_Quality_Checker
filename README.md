# Data Quality Checker — Worklet

An AI tool that checks training data (initially images with bounding boxes, segmentation masks and labels) against reviewer-confirmed rules extracted from plain text guidelines. The initial prototype runs locally and offline on a CPU using free/open-source models and tools.

- **[SRS.md](SRS.md):** the latest mentor clarifications and project context. These take precedence over earlier proposals and assumptions.
- **[RULES.md](RULES.md):** the Penn-Fudan development dataset and example rulebook. Sample data and rules are examples, not the complete, fixed product specification.

## Project structure

- **[backend/README.md](backend/README.md):** Python package scaffold. Implementation starts here, with the Penn-Fudan dataset reader.
- **[frontend/README.md](frontend/README.md):** reserved for the interface.

The user is writing the application. The scaffold contains folders and responsibility notes, with no application logic. The existing virtual environment, dependency list and dataset stay at the project root.

## Guideline input

Plain text guidelines are the primary requirement. Start with an editable text area where the user can type or paste them. Support for guideline documents, screenshots, scanned images and other formats is secondary and can be added later.

The tool must interpret the supplied guidelines and use the reviewer-confirmed rulebook. Dataset-specific checks must not silently replace the user's guidelines with a fixed Penn-Fudan rule set.

## What the tool does

1. **Rules:** reads the plain text guideline, extracts the rules and cites the source sentence for each one. A reviewer confirms or edits the list and can add rules by hand.
2. **Check:** checks every uploaded image and its annotation against the confirmed rules.
3. **Review:** shows flagged, uncertain and apparently correct data, with the check results and evidence. The reviewer controls final decisions, accepts or rejects findings, and can correct actual annotation errors. Confidence alone does not automatically accept or reject data.
4. **Improve:** records corrections and feedback and uses them to improve future audits. Implement and evaluate the improvement mechanism; storing a review history alone is insufficient.

Every finding, reviewer decision and correction is logged with its evidence and the rule version used.

## Environment

- **CPU prototype first.** Use free/open-source models and tools. Evaluate the prototype before a later GPU implementation. The intended deployment hardware and operating system have not been confirmed by the mentors.
- **AI providers:** the user clarified on 16 October 2026 that cloud AI services are allowed. The current implementation uses local Ollama; cloud provider support is not implemented.
- **Batch size:** determine it from measured CPU performance and available resources. The 170-image Penn-Fudan set is a local development example.
- **Deadline:** the user stated 31 October. Prioritize plain text guideline input and a complete audit, review and correction workflow.

## Provisional rulebook reuse design

For in-process reuse of a confirmed, immutable rulebook, we propose copying the list of references while sharing the rule objects. For example, a list of 100 rules would copy 100 references rather than duplicate all 100 rules' contents. Shared rules, including their nested parameters, must remain immutable during evaluation. Durable rulebook version pins and finding snapshots remain required.

This is a proposed optimization, not a claim about the current implementation or measured performance. The expected workload is currently unknown, including rule counts and sizes, batch sizes, concurrent audits and deployment resources. We will confirm or revise the approach using representative runtime, peak-memory and serialization measurements. The example of 100 rules is illustrative, not a workload requirement.

## Optional optimisation: newest-first review history

Proposed only; the current history methods return ascending versions with `limit`/`offset` pagination and a separate `COUNT(*)` query.

- Load the newest 10 saved reviews first. Fetch older reviews only when the user requests more, and retain already loaded entries in the interface.
- Use a cursor containing the chain identifiers, the review version at the start of browsing (`snapshot_review_version`) and the oldest version already displayed (`before_review_version`). A finding chain uses `finding_id`; a sample chain uses `audit_id`, `sample_id` and `sample_revision`.
- Fetch the next page with `revision < before_review_version`, ordered by `revision DESC`. Fetch 11 rows, display 10, and use the extra row only to determine whether more exist. The next cursor uses the oldest **displayed** row. This avoids increasingly large offsets.
- Keep the initial snapshot version as an upper bound while browsing. New decisions appear after an explicit refresh, which resets the cursor and refreshes the total; saving a decision should also refresh the view.

For example, these illustrative SQL queries load a finding's total and its history page. The `:name` values are bound parameters. First obtain the snapshot version:

```sql
SELECT COALESCE((
    SELECT revision FROM finding_reviews
    WHERE finding_id = :finding_id
    ORDER BY revision DESC LIMIT 1
), 0) AS total_reviews;
```

Under the current append-only write path, saved versions are consecutive from 1, so the latest version equals the number of saved reviews (0 when none exist). This avoids counting the whole chain. It depends on preserving that invariant: deleting history rows or importing versions with gaps would require a real count or a transactionally maintained counter instead. Pending version 0 is not a saved review.

```sql
SELECT * FROM finding_reviews
WHERE finding_id = :finding_id
  AND revision <= :snapshot_review_version
  AND revision < :before_review_version
ORDER BY revision DESC
LIMIT 11;
```

For the first page, omit the `before_review_version` condition; for subsequent pages, pass the oldest displayed version. Keep displaying the initial total as the total at the browsing snapshot. Apply the same queries to `sample_reviews` with all three sample-chain identifiers. Validate that the parent finding/sample exists, as the current repository does, so a missing parent is not reported as an empty history.

The existing primary-key indexes match the chain identifiers followed by `revision`; no additional index is proposed initially. For a list showing totals for many findings or samples, obtain the latest version for each visible chain in one SQL statement using `LEFT JOIN LATERAL (... ORDER BY revision DESC LIMIT 1)` and `COALESCE(revision, 0)`, rather than one application query per item. Where the current-review query already returns that version, reuse it for the displayed total. Validate performance on representative histories before implementation; no measured speedup is claimed.

## Queue technology choice

The backend currently stores batch-audit jobs and progress in PostgreSQL. Redis is a valid alternative, but it is not clearly better for our current needs. We would adopt a Redis-backed queue when dedicated queue functionality solves a concrete requirement and justifies operating another service.

This choice remains provisional because expected job volume, worker concurrency and latency requirements are currently unknown. Revisit it using representative workload measurements and requirements for scheduling, retries and recovery. Redis queue integration is not currently implemented.

## Measuring success

- Measure finding precision and recall against reviewer-verified examples, including missed errors and false alarms.
- Every guideline-based finding should cite its confirmed source rule. Identify manually authored rules explicitly.
- Verify that the reviewer can inspect apparently correct data, accept or reject findings, and correct annotations.
- Measure CPU processing time and memory use before selecting a batch size.
- Evaluate whether using reviewer feedback improves future audits on separate examples.

The earlier target of at least 95% correct automatic decisions is superseded by the mentors' requirement for reviewer-controlled final decisions.
