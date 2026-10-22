# Handoff notes: per-box crop experiment

For the developer who continues this work. Read [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)
first (what is compared and how results may be read), then [RUNNER.md](RUNNER.md)
(commands). This file records what exists, what was verified, what is still open
and why the code is shaped as it is.

## 1. Status at a glance

| Item | Status |
| --- | --- |
| Portable runner, status and score commands | Implemented |
| Offline tests | 231 passed on Linux (clean environment from `requirements.txt`) and 231 passed natively on Windows (28 supplied plus 203 added) |
| Test sensitivity | 15 deliberately injected defects, all caught by the tests |
| Metric code | Reproduces all three recorded historical result sets exactly |
| Prepared corpus verified with the real dataset | Done: 11 cases verified against the published hashes; `check-scope` passed |
| Smoke run on case-03 | Done (`smoke-case-03-b`); the first attempt was interrupted before any model call |
| Paired eight-case run, control vs candidate | Done (`paired-1`, 30 model calls) |
| Case-level error analysis | Done, in [PER_BOX_RESULTS.md](PER_BOX_RESULTS.md) |

## Headline result

The per-box crop candidate is **not promising** on this model. It answered fail for all 21 boxes it
judged (one box was unavailable), so all five compliant cases became false positives. F1 was 0.55 against
0.50 for the fresh control, but recall 1.00 only reflects that everything was flagged. The control missed
cases 02 and 12 and had two unavailable results. The installed model digest differs from the historical
Mac run. Details, per-box reasons, evidence versus hypotheses and limits are in
[PER_BOX_RESULTS.md](PER_BOX_RESULTS.md). The run directories are git-ignored; that file is the record.

## 2. What was delivered

All under `backend/experiments/box_grounding/`. Nothing in `backend/app/`,
`backend/api/`, dependencies, workers, schemas or contracts changed.

- `run_experiment.py`: the command line (`check-scope`, `run`, `status`, `score`).
- `runner.py`: the sequential loop, preflight, provenance, safety stop.
- `client.py`: the bounded Ollama client with the production safeguards.
- `crops.py`, `prompts.py`: per-box crops and the two request builders.
- `scoring.py`, `reporting.py`: aggregation, metrics, status and score reports.
- `scope.py`, `settings.py`, `run_files.py`: scope verification, settings, run files.
- `per_box_instruction_v1.txt`: the exact candidate wording (version `per-box-instruction-v1`).
- `EXPERIMENT_PLAN.md`, `RUNNER.md`, `PER_BOX_RESULTS.md`, this file.
- `tests/`: helpers, a fake local Ollama server and eight test modules.

## 3. How to continue

From the repository root, on a branch such as `experiment/per-box-grounding`:

1. Run the offline tests, then `check-scope` on the prepared corpus. If any source
   hash differs, stop and report the exact case and field. Never substitute images.
2. Run `run --dry-run` and confirm 30 chat requests for the eight development cases.
3. Run the one-case smoke run, read its model-call time, then choose one
   `--timeout-seconds` for the paired run (it must be the same for both arms).
4. Run the paired eight-case run and `score` it. Keep partial or failed runs; start a
   new `--run-name` instead of resuming.
5. Report all variants, including worse outcomes. Any new variant gets a new plan version.
6. Obtain the owner's approval before any commit, push or publication. Use normal
   human commit messages without AI authorship credit.

## 4. Design decisions and why

- **Production client not imported.** It reads the root `.env` and pulls in the
  database stack, which the portable environment does not have. `client.py` mirrors
  its safeguards instead, with an explicit loopback host. The prediction types are
  reused from `backend/app/models/feedback.py`.
- **Truth separation.** Request builders take evidence and the rule only. The scope
  loader refuses any label or review field. `score` is the only code that opens
  `reviewed_labels.json`, and a test asserts inference never opens it.
- **Aggregation.** Any supported failing box fails the sample, then unavailable, then
  unsure, then pass. Every box is judged; there is no early stop. Zero boxes are
  unavailable, never compliant.
- **Shared gate.** Unrepresentable cases (zero or more than 32 boxes, bad geometry,
  size limits) are rejected before any request in both arms and stay in coverage.
- **Interleaved order by default.** The first arm alternates per case, so neither arm
  always benefits from a warm cache. Timing still is not a controlled benchmark.
- **Digest policy.** `installed` pins whatever is installed and warns if it differs
  from history; `historical` stops on a difference. Equivalence is never assumed.
- **Safety stop.** After three consecutive server-side failures the run aborts (exit
  code 2) and records what did not run. A stop is not a retry.
- **No overwrite, no resume.** Settings and summary are written once; progress and
  results are append-only; an existing run directory is refused.
- **Per-box identity.** The model echoes the case, rule, box number and object ID,
  as the production client requires of case and rule. A wrong echo is unavailable.
- **Reason codes** are more granular than production (timeout, connection, HTTP
  error are separate) so failures can be diagnosed from the record.
- **Durations** include the two verification requests, as in the historical records.

## 5. Known limitations and open questions

- The development violations are homogeneous: in cases 02, 06, 11 and 12 the replaced
  annotation is object 1 at `(1,1)-(80,55)`. A result here cannot show general
  background-box detection.
- The control's guideline JSON is a reconstruction (the historical rule object is not
  in the handoff), so the control is not byte-identical to the historical baseline.
- The candidate changes pixels, request structure and wording together; effects cannot
  be separated without further variants.
- Smallest crops are 32 by 70 and 80 by 55 pixels, sent unresized. If Ollama rejects
  or distorts them it will show as unavailable reasons.
- The tests use a fake server. They show the wiring and the safeguards, not how the
  real model behaves.
- On a slow CPU the default 600 s timeout may be too small. Choose it from the smoke run.

## 6. Suggested next steps (each needs approval)

- Do not tune on these eight cases. Treat them as exposed development data.
- Variants worth a new plan version, each reported separately: a crop with declared surrounding context or
  padding; wording that states a partly visible pedestrian still satisfies the rule; a crop sheet; a different
  or larger model. Hypotheses 1 to 3 in `PER_BOX_RESULTS.md` say what each would test.
- A diagnostic, also a new variant: record the image token count per request to check whether small crops are
  upscaled.
- A new human-reviewed test set, hash-disjoint from `prior_image_exclusions.json`, with varied background boxes
  (not one repeated corner box); labels frozen before any prediction.
- Only after a fresh evaluation and owner approval: any production change. Nothing here activates a policy.

## 7. Not done, by design

No production code, API contract, migration, dependency pin, worker or policy was
changed. No model was fine-tuned. Nothing was committed or pushed. No real-model
results were produced or estimated.
