# System endpoints: health, capabilities and readiness

Three read-only endpoints let a frontend show whether the backend is alive, what it can
do, and whether its local model services are ready. They start nothing, download nothing
and run no inference. Generated schemas are in `openapi.json`.

## `GET /api/v1/health` (`getHealth`)

Liveness only: `{"status": "ok", "api_version": "v1"}`. It never touches PostgreSQL,
Ollama, the person model or inference. An offline optional service cannot make it
fail. A reachable API says nothing about model readiness.

## `GET /api/v1/capabilities` (`getCapabilities`)

What this build supports. It does not depend on which files are installed.

- `dataset_formats`, `guideline_input_formats`: `penn_fudan`, `coco` and `labelme`; `plain_text`.
- `coordinate_convention`: `format_specific`. Penn-Fudan boxes are one-based inclusive; COCO and
  LabelMe use zero-based continuous coordinates.
- `checks`: the executable guideline checks a rule may name, taken from the real registry.
  Only these can appear in a rulebook. A listed check can still report unavailable results
for a sample when its inputs are missing: the model-assisted `annotation_guideline` check,
for example, needs the configured local model, so read `GET /system/status` alongside this list.
- `execution_device`: `cpu`.

## `GET /api/v1/system/status` (`getSystemStatus`)

Bounded, read-only readiness. Always HTTP 200 unless the server itself fails; an offline
service is a normal state, not an error. The server probes Ollama with one short model-list
request (never text generation) and checks that the person model's weights file is installed,
without loading, hashing or running it.

`ollama`:

| Field | Meaning |
| --- | --- |
| `configuration` | `valid`, or `invalid` when the local settings cannot be read. |
| `reachable` | Whether the short metadata request succeeded. |
| `model` | The configured model name, or null when settings are invalid. |
| `model_available` | `true`, `false`, or null when unknown (server unreachable or its answer unusable). |

`person_model`: the Mask R-CNN model the person check (`person_coverage`) uses.

| Field | Meaning |
| --- | --- |
| `model` | The model name, `maskrcnn_resnet50_fpn_v2_coco`. |
| `checkpoint_file` | `present`, `missing` or `unreadable`. A present file is not loaded or hashed by this read. |

The top bar shows this as the Mask R-CNN light. Without the file, the person check reports
"could not check" for each sample, never a pass.

Paths, credentials and raw error text are never returned. Unexpected server failures use the
normal error envelope with HTTP 500.

## Mock scenarios

Illustrative and hand-written. The generated contract carries one typical example.

```json
{
  "ollama": {"configuration": "valid", "reachable": false, "model": "example-local-model:latest", "model_available": null},
  "person_model": {"model": "maskrcnn_resnet50_fpn_v2_coco", "checkpoint_file": "present"},
  "execution_device": "cpu"
}
```

```json
{
  "ollama": {"configuration": "valid", "reachable": true, "model": "example-local-model:latest", "model_available": false},
  "person_model": {"model": "maskrcnn_resnet50_fpn_v2_coco", "checkpoint_file": "missing"},
  "execution_device": "cpu"
}
```

## Verification status

The committed generated contracts include all three system operations. Regenerate them
when their source models or routes change. The system tests use mocked dependencies;
they do not establish that the installed weights produce good person outlines.
