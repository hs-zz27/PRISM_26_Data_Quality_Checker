# Running the box-grounding experiment

Portable, offline-testable commands for the raw-image control (`raw-image-v1`) and
the per-box crop candidate (`box-crop-v1`). Read [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)
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

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--cases` | all development | Comma-separated development cases. Training cases are refused. |
| `--arms` | both | `raw-image-v1`, `box-crop-v1` or both. |
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
