# PRISM 26 · Data Quality Checker

A tool for auditing annotated training data against the project's own written guidelines.

You paste in the guideline text. The tool pulls out the rules and quotes the sentence each rule came from, and a reviewer confirms the list. Every sample is then checked against that confirmed rulebook. The tool flags problems with evidence, but a person makes the final call on every sample.

The first version targets **images with bounding boxes, segmentation masks and labels**, using the Penn-Fudan pedestrian dataset for development. It runs locally on a CPU with free, open-source models and tools.

> **Read these first**
> - [SRS.md](SRS.md) has the latest mentor clarifications. Where it disagrees with the project brief or older notes, SRS.md wins.
> - [RULES.md](RULES.md) describes the Penn-Fudan dataset and an example rulebook. Those rules are examples to develop against, not the product's fixed rule set.

---

## Contents

- [Why this exists](#why-this-exists)
- [How it works](#how-it-works)
- [Workflow in detail](#workflow-in-detail)
- [What a finding looks like](#what-a-finding-looks-like)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Environment and constraints](#environment-and-constraints)
- [Measuring success](#measuring-success)
- [Scope: now and later](#scope-now-and-later)
- [Priorities](#priorities-for-the-31-october-deadline)
- [Design notes](#design-notes)

---

## Why this exists

Right now, annotated data is checked by people comparing each file to a written guideline, one rule at a time. That has three problems:

| Problem | What happens |
|---|---|
| **Slow** | More data means more reviewer hours, and nothing caps it. |
| **Inconsistent** | Two reviewers can judge the same file differently, and standards drift over time. |
| **Expensive** | Skilled reviewers spend most of their time on simple, repetitive checks. |

The idea behind this project is that **the guideline document is the rulebook**. Nobody hand-codes a check per rule, and the AI doesn't invent rules. It only finds, cites and applies rules that already exist in the guideline text, plus any rules a reviewer adds by hand. Moving to a new project should mean changing the guideline text, not the code.

---

## How it works

The work splits into two parts. Part A runs once per guideline. Part B runs every time a new batch of data comes in, reusing the same confirmed rulebook.

```text
 PART A: build the rulebook (once per guideline)

 ┌─────────────────┐   ┌───────────────────┐   ┌─────────────────┐   ┌────────────────────┐
 │ Paste guideline │──>│ Extract rules     │──>│ Reviewer checks │──>│ Confirmed rulebook │
 │ text            │   │ + source sentence │   │ the rule list   │   │ (versioned)        │
 └─────────────────┘   └───────────────────┘   └────────┬────────┘   └─────────┬──────────┘
                                 ^                      │                      │
                                 └──────────────────────┘                      │
                                   edit / add / re-scan                        │
                                                                               │
 PART B: audit the data (every batch)                                          │
                                 ┌────────────── uses confirmed rules ─────────┘
                                 v
 ┌─────────────────┐   ┌───────────────────┐   ┌─────────────────┐   ┌────────────────────┐
 │ Upload images   │──>│ Check each sample │──>│ Findings with   │──>│ Reviewer decides:  │
 │ + annotations   │   │ against the rules │   │ evidence +      │   │ accept / reject /  │
 └─────────────────┘   └───────────────────┘   │ citation        │   │ correct annotation │
                                 ^             └─────────────────┘   └─────────┬──────────┘
                                 │                                             │
                                 └────────────────── feedback ─────────────────┘
```

In short, there are four stages:

1. **Rules.** Read the plain-text guideline, extract the rules and cite the source sentence for each one. A reviewer confirms or edits the list and can add rules by hand.
2. **Check.** Check every uploaded image and its annotation against the confirmed rules.
3. **Review.** Show flagged, uncertain and apparently correct samples with their results and evidence. The reviewer accepts or rejects findings and can fix real annotation errors.
4. **Improve.** Record corrections and feedback and use them to make later audits better. Just storing review history isn't enough. The improvement has to be implemented and measured.

Every finding, reviewer decision and correction is logged with its evidence and the rulebook version that was used.

---

## Workflow in detail

### Step 1: Guideline input

The project guideline documents are DRM-protected, so no script can open them directly. Instead, the reviewer copies the text out of an authorised viewer and pastes it into the tool. Nothing DRM-locked ever reaches the system.

```text
 ┌───────────────────────────┐
 │ Guideline document        │   DRM-protected. The tool never opens this file.
 └─────────────┬─────────────┘
               │  reviewer opens it in an authorised viewer
               v
 ┌───────────────────────────┐
 │ Reviewer copies the text  │
 └─────────────┬─────────────┘
               │  paste (plain text only)
               v
 ┌───────────────────────────┐
 │ Editable text area        │
 └─────────────┬─────────────┘
               v
 ┌───────────────────────────┐
 │ Rule extraction           │
 └───────────────────────────┘
```

Plain text pasted into an editable text area is the main input. Uploading documents, screenshots or scans may come later, but it isn't a priority for the first version.

The tool has to work from **the guideline the user supplies**. Dataset-specific checks must never quietly replace that guideline with a fixed Penn-Fudan rule set.

### Step 2: Confirming the rules

Nothing gets checked until a person has seen the extracted rules. That turns rule extraction into a checkpoint instead of a silent assumption.

```text
                rules extracted from pasted text
                              │
                              v
                    ┌───────────────────┐
         ┌─────────>│     Extracted     │
         │          └─────────┬─────────┘
         │                    v
         │          ┌───────────────────┐
         └──────────┤   Under review    │<──┐
          re-scan   │                   │───┘  edit a rule,
          the text  └─────────┬─────────┘      add a missed rule
                              │  reviewer confirms
                              v
                    ┌───────────────────┐
                    │     Confirmed     │──>  rulebook version is pinned
                    └───────────────────┘
```

| Action | What it does |
|---|---|
| **Confirm** | The reviewer agrees the list is complete and accurate, and the rulebook is locked as a version. |
| **Add** | The reviewer types in a rule the extractor missed. It is marked as manually authored. |
| **Re-scan** | Runs extraction again, for example after more text has been pasted in. |

### Step 3: Checking and review

The tool checks each sample, then a reviewer goes through the results. The reviewer can look at samples the tool thinks are fine, not just the flagged ones. **The tool never accepts or rejects a sample by itself.**

```text
Reviewer                  Review screen              Checking engine               PostgreSQL
    │                           │                           │                           │
    │ start audit on a batch    │                           │                           │
    │──────────────────────────>│                           │                           │
    │                           │                           │                           │
    │                           │ samples + rule version    │                           │
    │                           │──────────────────────────>│                           │
    │                           │                           │                           │
    │                           │                           │ for each sample: measure  │
    │                           │                           │ it, compare to the rules  │
    │                           │                           │                           │
    │                           │                           │ save findings             │
    │                           │                           │──────────────────────────>│
    │                           │                           │                           │
    │                           │ load samples to review    │                           │
    │                           │──────────────────────────────────────────────────────>│
    │                           │                           │                           │
    │ show findings + evidence  │                           │                           │
    │<──────────────────────────│                           │                           │
    │                           │                           │                           │
    │ accept / reject + reason, │                           │                           │
    │──────────────────────────>│                           │                           │
    │ or correct the annotation │                           │                           │
    │                           │                           │                           │
    │                           │ append review: decision,  │                           │
    │                           │──────────────────────────────────────────────────────>│
    │                           │ reason, rulebook version  │                           │
    │                           │                           │                           │
```

Overrides always need a reason, so every decision can be traced later.

---

## What a finding looks like

Each check on each sample produces five things:

| Field | Meaning |
|---|---|
| **Rule** | Which confirmed rule was applied |
| **Result** | Pass, fail, unsure, or could not check |
| **Reason** | Why the tool reached that result, for example "label is not in the approved list" |
| **Evidence** | The measurements taken, for example box-to-mask edge differences |
| **Citation** | The exact guideline sentence the rule came from, or a note that a reviewer wrote the rule |

### Example: one sample, start to finish

Sample: `FudanPed00012.png` and its mask/annotation.

```text
 Image + annotation ──> Measured ──> Checked ──> Findings ──> Review queue
```

| Rule | Result | Evidence |
|---|---|---|
| Labels must use an approved category | Pass | All instances labelled `person` |
| Box must fit the object tightly | **Fail** | Gap of about 14 px on the left edge of instance 2, compared against the mask outline |
| Every visible pedestrian is annotated | Pass | Mask count matches box count |

Result: **flagged for review.** The sample waits for a reviewer's decision.

*(This sample and its rules are only an illustration. The real rules come from whatever guideline is pasted in.)*

### How a rule is stored

```text
 ┌────────────────────┐   contains    ┌────────────────────┐
 │ Rulebook           │──────────────>│ Rule               │
 │                    │   1 ... many  │                    │
 │ rulebook_id        │               │ rule_id            │
 │ version            │               │ description        │
 │ guideline_text     │               │ check_name         │
 │ confirmation_state │               │ parameters         │
 └────────────────────┘               │ origin             │
          ^                           │ source_sentence    │
          │ pinned version            │ confirmation_state │
          │                           └────────────────────┘
          │                                     ^
 ┌────────┴───────────┐          cites          │
 │ Finding            │─────────────────────────┘
 │                    │
 │ finding_id         │
 │ audit_id           │
 │ sample_id          │
 │ sample_revision    │
 │ object_id          │
 │ rulebook_id        │
 │ rulebook_version   │
 │ rule_id            │
 │ outcome            │
 │ reason             │
 │ evidence           │
 │ source_sentence    │
 └────────┬───────────┘
          │ append-only history (1 ... many)
          v
 ┌────────────────────┐
 │ Finding review     │
 │                    │
 │ revision           │
 │ resolution         │
 │ reviewer_id        │
 │ comment            │
 │ updated_at         │
 └────────────────────┘
```

| Field | Values |
|---|---|
| `origin` | `extracted` (taken from the guideline) or `reviewer` (added by hand) |
| `confirmation_state` | `draft`, `confirmed` or `rejected` |
| `outcome` | `pass`, `fail`, `unsure` or `unavailable` (could not check) |
| `resolution` | `pending`, `accepted`, `rejected` or `unsure` |

An extracted rule must quote a sentence that appears word for word in `guideline_text`. A rule added by a reviewer may have no source sentence. Sample-level decisions (accepted, rejected, needs correction) are stored in a separate append-only history.

Confirmed rulebooks are immutable. A finding always points at the exact rulebook version it was checked against, so editing the rules later never changes what an old audit said.

---

## Architecture

```text
 ┌──────────────────────────────── frontend/ ────────────────────────────────┐
 │  Guideline text area      ·      Rule confirmation      ·   Review screen │
 └─────────────────────────────────────┬─────────────────────────────────────┘
                                       ^
                                       v
 ┌─────────────────────────────── backend/ (Python) ─────────────────────────┐
 │                                                                           │
 │  Rule extraction        Checking engine + batch jobs  <──  Dataset reader │
 │                                     ^                      (Penn-Fudan)   │
 │                                     │                                     │
 │                         Feedback / improvement                            │
 │                                                                           │
 └────────┬─────────────────────────────┬───────────────────────────┬────────┘
          ^                             ^                           ^
          v                             v                           v
 ┌──────────────────┐       ┌───────────────────────┐       ┌──────────────────┐
 │ Ollama           │       │ PostgreSQL            │       │ Dataset          │
 │ (local model)    │       │ rulebooks, findings,  │       │ images +         │
 │                  │       │ reviews, job progress │       │ annotations      │
 └──────────────────┘       └───────────────────────┘       └──────────────────┘
```

- **Rule extraction** sends the pasted text to a local model through Ollama and returns rules, each with its source sentence.
- **Checking engine** measures each annotation and compares it with the rules that apply.
- **Batch audit jobs** keep their state and progress in PostgreSQL (see "Queue choice" under [Design notes](#design-notes)).
- **Feedback** feeds reviewer decisions and corrections back into later audits.

The core (extraction, rulebook, citations, review log) is meant to stay the same across data types. Supporting a new kind of data should mean adding a new measurement module, not rebuilding the pipeline.

---

## Project structure

```
PRISM_26_Data_Quality_Checker/
├── backend/          Python package (start here: Penn-Fudan dataset reader)
│   └── README.md
├── frontend/         Interface: guideline input, rule confirmation, review
│   └── README.md
├── SRS.md            Mentor clarifications (takes precedence)
├── RULES.md          Penn-Fudan dataset notes + example rulebook
├── requirements.txt
└── README.md
```

The scaffold has folders and notes on what each part is responsible for. The virtual environment, dependency list and dataset stay at the project root.

---

## Getting started

For the complete local stack, see [Docker Compose setup](docker/README.md).
With Docker Desktop, `.env` credentials and host Ollama ready, one command starts
PostgreSQL, migrations, the API, frontend and all five workers:

```bash
docker compose up --build -d
```

Open http://localhost:5173. Existing native installs should read the storage and
model-pin transition notes before switching. The manual setup below remains
available when developing individual services.

Requirements: Python 3.12, Docker (to run PostgreSQL), Node.js 18+ (for the frontend), and [Ollama](https://ollama.com) with a local model pulled.

```bash
git clone https://github.com/hs-zz27/PRISM_26_Data_Quality_Checker.git
cd PRISM_26_Data_Quality_Checker

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# Copy .env.example to .env and set POSTGRES_PASSWORD, then start PostgreSQL
docker compose up -d postgres
```

Setup for the database, the model and the dataset is described in [backend/README.md](backend/README.md).

### Branches

Work happens on feature branches named `area/what-it-does`, such as `backend/audit-findings-list` or `fix/frontend-review-workflow`. Open a pull request into `main`. For frontend work that builds on an existing frontend branch, open it into that branch instead.

---

## Environment and constraints

- **CPU first.** Build and evaluate on CPU with free, open-source tools before any GPU version. The deployment hardware and OS haven't been confirmed yet.
- **AI provider.** The current code uses a local model through Ollama. Cloud AI services were cleared for use on 16 October 2026, but cloud provider support isn't implemented.
- **Guidelines.** Plain text paste is the main input, because the source documents are DRM-protected.
- **Batch size.** To be chosen from measured CPU time and memory, not guessed. The 170-image Penn-Fudan set is only for local development.

---

## Measuring success

| What | How we check it |
|---|---|
| Finding quality | Precision and recall against reviewer-verified examples, counting both missed errors and false alarms |
| Traceability | Every guideline-based finding cites its confirmed source rule. Manual rules are labelled as manual. |
| No invented rules | No finding points at a rule that isn't in the confirmed rulebook |
| Review workflow | A reviewer can inspect passing samples, accept or reject findings, and correct annotations |
| Performance | CPU time and memory per batch, measured before a batch size is fixed |
| Improvement | Audits that use reviewer feedback do measurably better on a separate set of examples |

The project brief proposed a target of at least 95% correct automatic decisions. That target has been **dropped**, because the mentors require reviewers to make the final decision on every sample.

---

## Scope: now and later

```text
 NOW                              LATER: same engine, new measurement modules
 ┌──────────────────────────┐     ┌──────────────────────────┐
 │ Images                   │     │ Polygons, keypoints      │
 │ Bounding boxes           │────>│ Video                    │
 │ Segmentation masks       │     │ Audio                    │
 │ Labels                   │     │ Text                     │
 │                          │     │ 3D / LiDAR               │
 └──────────────────────────┘     └──────────────────────────┘
```

The longer-term goal is one engine for every data type. Only the measurement module would change, while extraction, citation and review stay the same. For the current deadline, the focus is a complete image workflow: **paste guideline → confirm rules → audit → review → correct**.

---

## Priorities for the 31 October deadline

```text
 1. Plain-text guideline input
       │
       v
 2. Rule extraction + confirmation
       │
       v
 3. Full audit of a batch
       │
       v
 4. Review + correction
       │
       v
 5. Feedback that improves audits
       │
       v
 6. Evaluation (precision, recall, CPU time)
```

A complete workflow matters more than extra features. Uploading other document formats, other data types and GPU support all come after this.

---

## Design notes

These are proposals, not descriptions of the current code. Each one gets confirmed or revised once we have real workload numbers.

<details>
<summary><strong>Reusing a confirmed rulebook in memory</strong></summary>

For in-process reuse of a confirmed, immutable rulebook, copy the list of references and share the rule objects. A list of 100 rules would copy 100 references instead of duplicating every rule's contents. Shared rules, including their nested parameters, must stay immutable during evaluation. Durable rulebook version pins and finding snapshots are still required.

The workload is unknown so far, including rule counts and sizes, batch sizes, concurrent audits and deployment resources. The 100-rule figure is only an illustration. This will be confirmed or revised using runtime, peak-memory and serialization measurements.

</details>

<details>
<summary><strong>Newest-first review history</strong></summary>

At the moment, the history methods return versions in ascending order, with `limit`/`offset` pagination and a separate `COUNT(*)` query.

Proposed change:

- Load the newest 10 reviews first. Fetch older ones only when the user asks for more, and keep what's already loaded in the interface.
- Use a cursor holding the chain identifiers, the review version when browsing started (`snapshot_review_version`) and the oldest version shown so far (`before_review_version`). A finding chain uses `finding_id`. A sample chain uses `audit_id`, `sample_id` and `sample_revision`.
- Fetch the next page with `revision < before_review_version`, ordered by `revision DESC`. Fetch 11 rows, show 10, and use the extra row only to tell whether more exist. The next cursor uses the oldest **displayed** row, which avoids ever-growing offsets.
- Keep the snapshot version as an upper bound while browsing. New decisions show up after an explicit refresh, which resets the cursor and the total. Saving a decision also refreshes the view.

Total for a finding (the `:name` values are bound parameters):

```sql
SELECT COALESCE((
    SELECT revision FROM finding_reviews
    WHERE finding_id = :finding_id
    ORDER BY revision DESC LIMIT 1
), 0) AS total_reviews;
```

The write path is append-only, so saved versions run consecutively from 1. That means the latest version equals the number of saved reviews (0 if there are none). This only holds while that invariant does. Deleting history rows or importing versions with gaps would need a real count or a counter maintained in the same transaction. Pending version 0 isn't a saved review.

Page of history:

```sql
SELECT * FROM finding_reviews
WHERE finding_id = :finding_id
  AND revision <= :snapshot_review_version
  AND revision < :before_review_version
ORDER BY revision DESC
LIMIT 11;
```

Leave out the `before_review_version` condition on the first page. Apply the same queries to `sample_reviews` using all three sample-chain identifiers. Check that the parent finding or sample exists, as the repository already does, so a missing parent isn't reported as an empty history.

The existing primary-key indexes already cover the chain identifiers followed by `revision`, so no new index is proposed for now. For a list showing totals for many items, get the latest version per visible chain in one statement with `LEFT JOIN LATERAL (... ORDER BY revision DESC LIMIT 1)` and `COALESCE(revision, 0)`, rather than running one query per item. No speedup is claimed until it has been measured on realistic histories.

</details>

<details>
<summary><strong>Queue choice</strong></summary>

Batch-audit jobs and their progress are stored in PostgreSQL. Redis would also work, but it isn't clearly better for what we need right now. We'd move to a Redis-backed queue only if it solves a specific requirement that justifies running another service.

This stays provisional until we know the job volume, worker concurrency and latency requirements, and what scheduling, retry and recovery need to look like. Redis isn't integrated.

</details>