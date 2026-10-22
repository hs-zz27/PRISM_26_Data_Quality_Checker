# Box-grounding development handoff

This folder is the teammate's independent accuracy investigation. The owner is
testing numbered full-image overviews separately. This assignment investigates
cropped annotation regions and per-box decisions. It does not change the audit
application, feedback policies, detector integration or HTTP contracts.

Start with [TEAMMATE_PROMPT.md](TEAMMATE_PROMPT.md). It defines the four phases,
scope, evaluation rules and completion criteria. [RESULTS.md](RESULTS.md) explains
the earlier measurements; the machine-readable records are in
`reference_results/`.

## What is included

- `manifest.json`: three training and eight development cases, exact source
  hashes, object facts and explicit controlled-annotation recipes.
- `reviewed_labels.json`: the owner's model-blinded judgments and clarification
  history. Guided practice case 01 is excluded.
- `generated_annotations/`: only the four project-generated annotation
  replacements. Dataset photos, masks and original annotation files are not
  redistributed here.
- `system_prompt_v1.txt`: the exact historical system prompt.
- `prepare.py`: cross-platform, offline source verification and preparation.
- `views.py`: pure reference implementations for numbered views and crop sheets.
- `tests/`: synthetic offline checks for preparation and rendering.

An inference runner is intentionally not claimed as implemented here. Building
and evaluating the portable per-box experiment is the teammate's task. Local
owner scripts under ignored directories are not dependencies of this handoff.

## Windows setup

From the repository root, use Python 3.12 and an experiment environment. No
Torch, CNN checkpoint, PostgreSQL or full backend installation is needed:

```powershell
py -3.12 -m venv .cache/box-grounding/venv
.\.cache\box-grounding\venv\Scripts\python.exe -m pip install -r backend/experiments/box_grounding/requirements.txt
```

Run the supplied offline tests:

```powershell
.\.cache\box-grounding\venv\Scripts\python.exe -B -m pytest backend/experiments/box_grounding/tests -q --tb=short -p no:cacheprovider
```

The teammate's Claude Code can help implement and review the experiment. The
subscription does not execute the local Qwen model; real accuracy measurements
require Ollama, or the owner must run the finished experiment on the Mac.

## Obtain and verify the small source set

Read [DATA_PROVENANCE.md](DATA_PROVENANCE.md). Download the archive from the
[official Penn-Fudan page](https://www.cis.upenn.edu/~jshi/ped_html/), or reuse an
existing original dataset. Downloading is an explicit operator action:

```powershell
New-Item -ItemType Directory -Force .cache/box-grounding | Out-Null
Invoke-WebRequest -Uri "https://www.cis.upenn.edu/~jshi/ped_html/PennFudanPed.zip" -OutFile ".cache/box-grounding/PennFudanPed.zip"
.\.cache\box-grounding\venv\Scripts\python.exe -B -m backend.experiments.box_grounding.prepare --archive .cache/box-grounding/PennFudanPed.zip
```

Alternatively, point `--dataset-root` at the directory containing `PNGImages`
and `Annotation`. On macOS/Linux the equivalent existing-environment command is:

```bash
.venv/bin/python -B -m backend.experiments.box_grounding.prepare --dataset-root PennFudanPed
```

Preparation verifies every image and original annotation hash before applying a
declared replacement. It checks exact annotation bytes, dimensions, origin and
object facts, then copies eleven verified cases to `.cache/box-grounding/inputs`.
It does not extract a ZIP tree, download anything, run models or connect to a
database. Existing output is preserved; choose a new `--output` if necessary.
Its `scope.json` contains no human decisions or controlled-perturbation flags.

## Real-model prerequisites

Install [Ollama for Windows](https://docs.ollama.com/windows), then explicitly
download `qwen3-vl:4b-instruct` if it is not installed. The historical digest and
settings are in `reference_results/reviewed_comparison.json`. Record the exact
installed digest and check it before/after requests. A changed tag or model is
not interchangeable with the historical execution.

The Mac's ten-thread measurement is machine-specific. The portable experiment
should start with automatic thread selection, then keep the selected setting
fixed for its paired local raw/crop comparison. Use CPU-only inference to match
the prototype constraint. Do not promise a speedup from the RAM capacity alone.
Run one request at a time; these experiments do not need Docker.

## Current scientific status

The old example-based candidate regressed. Case 12 gained a missed violation;
both arms missed case 02. Those observations motivate this investigation but do
not establish the root cause. The current eight cases are exposed development
data. Any promising strategy requires new, independently reviewed held-out
images before an accuracy-improvement claim or production adoption.
