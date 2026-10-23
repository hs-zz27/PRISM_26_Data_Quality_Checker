# Running the box-grounding experiment

Portable, offline-testable commands for the raw-image control (`raw-image-v1`) and
the per-box crop candidates (`box-crop-v1` and `box-crop-v2`). Read [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)
first: it fixes what is compared and how results may be interpreted.

Run every command from the repository root. The examples use the Windows venv from
[README.md](README.md); on macOS or Linux use `.venv/bin/python` instead.

```powershell
$py = ".\.cache\box-grounding\venv\Scripts\python.exe"
$exp = "backend.experiments.box_grounding.run_experiment"
```

## 1. Offline checks (no model, no dataset)

```powershell
& $py -B -m pytest backend/experiments/box_grounding/tests -q --tb=short -p no:cacheprovider
```

## 2. Prepare and verify the corpus

Download and prepare as described in [README.md](README.md), then verify:

```powershell
& $py -B -m $exp check-scope --scope .cache/box-grounding/inputs/scope.json
```

It confirms 11 cases (3 training, 8 development), no case-01, no label fields, the
system prompt hash, repeated object IDs, and prints every box's source coordinates,
the PIL crop box and the crop size. It contacts no model. If a hash differs, stop and
report the exact case and field; never substitute other images.

## 3. Check what would be sent (no network, writes nothing)

```powershell
& $py -B -m $exp run --scope .cache/box-grounding/inputs/scope.json --run-name plan --dry-run
```

Expected for the full corpus: 30 chat requests (8 control + 22 candidate).

## 4. Real-model prerequisites

Install Ollama for Windows, start it (it listens on `http://127.0.0.1:11434`), and pull
the model explicitly. Nothing in this folder downloads a model.

```powershell
ollama pull qwen3-vl:4b-instruct
ollama list
```

Only a loopback `--host` is accepted. The run pins the installed digest and checks it
before and after every call. Use `--model-digest historical` to require the historical
digest (the run stops if it differs), or leave the default `installed` and read the
warning printed when it differs.

## 5. Smoke run on one named development case

```powershell
& $py -B -m $exp run --scope .cache/box-grounding/inputs/scope.json --run-name smoke-case-03 --cases case-03 --timeout-seconds 1800
& $py -B -m $exp status --run-dir .cache/box-grounding/runs/smoke-case-03
```

This sends 2 chat requests (one per arm) and preserves the attempt. Note the model-call
time in `status`: it tells you how slow this CPU is. Choose one `--timeout-seconds` for
the full run that is comfortably above the slowest expected request, because a timed-out
request becomes an unavailable result (it is never retried).

## 6. The paired eight-case run

```powershell
& $py -B -m $exp run --scope .cache/box-grounding/inputs/scope.json --run-name paired-1 --timeout-seconds 1800
```

Sequential, 30 chat requests, interleaved order (the first arm alternates per case).
It prints a start message immediately and progress before and after each sample and
box. In a second terminal, at any time:

```powershell
& $py -B -m $exp status --run-dir .cache/box-grounding/runs/paired-1
```

`status` reads saved files only and runs no inference. Keep `--threads auto` unless you
have a reason to pin; if you pin, use the same value for every run you compare.

## 7. Score

Labels are read only here, after inference:

```powershell
& $py -B -m $exp score --run-dir .cache/box-grounding/runs/paired-1 --labels backend/experiments/box_grounding/reviewed_labels.json
```

The report keeps unsure, unavailable and worse outcomes visible, prints per-case and
per-box results, and labels an incomplete run as incomplete. It is saved once as
`score.json` and never overwritten.

Unexecuted development cases remain in each arm's denominator as unavailable,
with zero model-call duration. Their case-table entries remain `not run`.
A subset, interrupted, unfinished or source-changed run cannot claim an improved
paired comparison, even if its completed cases look favorable. Completed earlier
run records are preserved; these reporting corrections do not rewrite old scores.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--cases` | all development | Comma-separated development cases. Training cases are refused. |
| `--arms` | raw + v1 | `raw-image-v1`, `box-crop-v1`, `box-crop-v2`. Choose one crop version, optionally paired with raw. |
| `--order` | `interleaved` | `blocked` runs one arm completely, then the other. |
| `--threads` | `auto` | CPU threads; fixed within a run. |
| `--num-ctx` / `--num-predict` | 16384 / 2048 | Same in both arms. |
| `--timeout-seconds` | 600 | Per request; finite, up to 3600. |
| `--max-request-bytes` / `--max-response-bytes` | 64 MiB / 65536 | Bounded. |
| `--max-consecutive-failures` | 3 | Safety stop for a broken server. A stop is not a retry. |

Exit codes: 0 success, 1 error, 2 run aborted by the safety stop, 130 interrupted.

## What a run writes (all under `.cache/box-grounding/runs/<name>/`, ignored by Git)

- `run.json`: settings, digest, versions, hardware, hashes of scope, prompts and code.
  Written once, before the first model call.
- `progress.jsonl`: append-only events (start, per box and sample, finish).
- `results.jsonl`: one record per case and arm with every box's outcome, reason,
  timings, request hash, crop hash and the model's raw answer.
- `crops/`: the exact crops that were sent.
- `summary.json`: written once at the end (completed, aborted, interrupted or failed).
- `score.json`: written once by `score`.

An existing run directory is never reused, and there is no resume. After an
interruption or failure, keep the partial run and start a new `--run-name`.

## Unavailable reason codes

| Code | Meaning |
| --- | --- |
| `request_timeout` | The request exceeded `--timeout-seconds`. |
| `connection_failed` / `http_error` | Ollama was unreachable or returned an error. |
| `model_identity_changed` / `identity_unavailable` | The pinned model was not the installed one, or could not be identified. |
| `local_vision_unverified` | The model is remote or reports no vision capability. |
| `context_limit` | The request did not fit the configured bounds; nothing was truncated. |
| `context_unverified` / `incomplete_response` | The response did not fit the window, or was cut off. |
| `response_limit` / `invalid_response` / `invalid_proposal` | Oversized, malformed, duplicate-field or schema-invalid output. |
| `identity_mismatch` | The model echoed the wrong case, rule, box or object. It is not repaired. |
| `unsupported_origin_or_box_count`, `invalid_box_coordinates`, `box_outside_source_or_inverted`, `invalid_object_fields`, `source_byte_limit`, `source_pixel_limit`, `rendered_byte_limit` | The case or box was rejected before any request. |

## Crop v2: omit source geometry from model input

`box-crop-v2` removes `bbox`, `bbox_convention` and `source_image_size` from the
model-facing box object. Source coordinates, annotation index, object ID, PIL
crop bounds and crop hash remain in `results.jsonl`. Pixels, instruction text,
system prompt, output schema and inference safeguards stay the same. The request
also identifies its new variant as `box-crop-v2`; the earlier diagnostic used the
v1 identifier while removing geometry. Evaluate this version rather than assuming
its results from that diagnostic. No padding, resizing or threshold changes.

The default remains raw + v1 for reproducibility. Select v2 explicitly and use a
new run name. Only one crop version is allowed per run; each can be paired with
its own fresh raw control. Original run folders and scores are never overwritten.

Windows, using the environment and prepared scope above:

```powershell
& $py -B -m $exp run --scope .cache/box-grounding/inputs/scope.json --run-name paired-crop-v2 --arms raw-image-v1,box-crop-v2 --threads auto --timeout-seconds 1800 --dry-run
& $py -B -m $exp run --scope .cache/box-grounding/inputs/scope.json --run-name paired-crop-v2 --arms raw-image-v1,box-crop-v2 --threads auto --timeout-seconds 1800
& $py -B -m $exp score --run-dir .cache/box-grounding/runs/paired-crop-v2 --labels backend/experiments/box_grounding/reviewed_labels.json
```

Owner's Mac, using the already verified local scope:

```bash
.venv/bin/python -B -m backend.experiments.box_grounding.run_experiment run --scope .cache/box-grounding/handoff-verification/scope.json --run-name paired-crop-v2-mac --arms raw-image-v1,box-crop-v2 --threads 10 --timeout-seconds 600
.venv/bin/python -B -m backend.experiments.box_grounding.run_experiment score --run-dir .cache/box-grounding/runs/paired-crop-v2-mac --labels backend/experiments/box_grounding/reviewed_labels.json
```

Ten threads is the owner's M4 Air setting, not a portable performance default.
Both commands run sequentially on CPU; keep Ollama running. This experiment does
not use PostgreSQL or Docker. The full paired run makes 30 model requests across
8 development cases: 8 raw requests and 22 crop requests. Progress prints before
and after each box; a slow request can leave its start message visible for minutes.

For saved progress in a second terminal:

```bash
.venv/bin/python -B -m backend.experiments.box_grounding.run_experiment status --run-dir .cache/box-grounding/runs/paired-crop-v2-mac
```

Completion means all 16 case/arm units are durably recorded, all 22 candidate
boxes remain visible, and scoring reports coverage, unavailable outcomes, false
positives and false negatives against the fresh raw control. It does not mean
accuracy improved. Inspect background and pedestrian boxes separately; preserve
identity mismatches and other unavailable outcomes. A candidate that still flags
every box fails the investigation even with perfect violation recall. Do not
activate this experiment or claim generalization from these exposed cases; any
accuracy improvement requires a fresh, independently reviewed set.

## Identity binding: bound-v1

The optional `--identity-schema bound-v1` mode adds per-request JSON Schema
`const` values for `case_id` and `rule_id`, plus `box_number` and `object_id` for
crops. It applies to both arms. The default `echo-v1` preserves earlier requests.
These constants are known request identifiers, never expected outcomes or reviewer
labels. The output model and strict equality checks still reject malformed or
mismatched responses. Nothing is filled in, repaired, retried or silently accepted.

Ollama accepts a JSON schema through its `format` parameter; `const` restricts a
field to one value. See the [Ollama structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs)
and [JSON Schema const reference](https://json-schema.org/understanding-json-schema/reference/const).
Runtime support and elimination of copying failures still need a real-model run;
a schema does not guarantee visual accuracy.

Start with the previously failing case-05 (one raw and two crop requests):

```bash
.venv/bin/python -B -m backend.experiments.box_grounding.run_experiment run --scope .cache/box-grounding/handoff-verification/scope.json --run-name crop-v2-bound-case05 --arms raw-image-v1,box-crop-v2 --identity-schema bound-v1 --cases case-05 --threads 10 --timeout-seconds 600
```

Check the saved results for exact identities and model verification, not merely
successful completion. A failed identity still produces unavailable. The subset
is a transport diagnostic, not an accuracy comparison. Do not replace the old
case-05 outcomes with these new ones. Full comparison requires a new run over
all cases with the same mode in both arms. Windows uses the portable Python
command and prepared inputs path above, with `--threads auto`.

The selected identity mode is recorded in execution settings, provenance and
scores. Base schema hashes describe the unbound model schemas; each recorded
request SHA covers the exact schema with its identity constants. Source images,
prompts, crop version and identities remain bound through existing hashes and
records. Earlier saved scores without identity mode are reported as echo-v1.

## Selected-box context diagnostic

See [CONTEXT_PROBE.md](CONTEXT_PROBE.md) for the separate eight-box paired probe,
its owner-run test/inference commands, frozen padding/marker policy and decision
rule. Its records describe selected boxes, not complete images. Existing runner
variants and historical results are unchanged.
