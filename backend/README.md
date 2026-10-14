# Backend

This folder contains the dataset reader, box and mask measurements, sample audit
service, rule records and local Ollama client. The remaining modules contain
responsibility notes for later implementation.

## Structure

```text
backend/
  app/
    main.py                  Application entry point
    core/                    Configuration and local paths
    models/                  Structured records for data, rules and review
    datasets/                Dataset and annotation readers
    checks/                  Measurements and rule evaluation
    inference/               Local Ollama and CPU detector integration
    services/                Workflow coordination
    storage/                 Persistence of rules, evidence and corrections
    routers/                 Future API endpoints, if an HTTP API is used
  tests/                     Backend tests added as features are implemented
```

Routers will accept requests and call services if an HTTP API is introduced. A separate controller layer is not needed initially: services coordinate the workflow. No API framework has been installed or chosen by this scaffold.

`models/` means Python data structures, such as an annotation or a finding. AI model integration belongs in `inference/`; downloaded weights remain outside the source code, currently in the project's `.cache/torch` folder.

## First task

Start in `app/datasets/penn_fudan.py`:

1. Load `FudanPed00001.png`, its annotation text and its mask from the existing root-level `PennFudanPed/` folder.
2. Parse person IDs, labels and box coordinates.
3. Check that the image and mask dimensions agree and that instance IDs match the annotations.

The annotation declares a coordinate origin of `(1, 1)`. Choose and document an internal coordinate convention before converting boxes; preserve the original convention when exporting.

The mask stores IDs `0`, `1` and `2` for this example, so it looks nearly black in a normal image viewer. Use display colours for visualization while retaining the original mask IDs.

## Environment and files

Use the existing Python 3.12 environment. From the project root:

```bash
source .venv/bin/activate
```

From inside `backend/`:

```bash
source ../.venv/bin/activate
```

Dependencies are recorded in the root-level `requirements.txt`. The dataset remains at `../PennFudanPed/` when your terminal is inside this folder. Configuration should resolve paths from a known project location rather than depending on the terminal's current directory.

Follow `../MENTOR_REQUIREMENTS.md`: plain text guidelines first, CPU inference, reviewer-controlled final decisions, actual annotation corrections and evaluated use of feedback.

## Ollama configuration

Edit `ollama.toml` in this folder to change the local model. Set `model` to the
exact name shown by `ollama list`; the model must already be installed. The same
file controls the local server address, request timeout in seconds, context
length and maximum output tokens.

`app/core/config.py` loads and validates these settings independently of the
terminal's working directory. `propose_rules()` reads the file on each call,
so subsequent requests use saved changes. Invalid settings fail before an API
request. CPU execution remains enforced by the client.

For a one-off model selection, use `propose_rules(text, model="installed-model:tag")`.
To supply an alternate configuration file, pass
`settings=load_ollama_settings(path)`. Import the loader from
`backend.app.core.config` when running from the project root. Per-call model,
host and timeout overrides take precedence over the supplied settings and are
validated without modifying them.

## Proposed frontend API

See [api/README.md](api/README.md) for the draft endpoint contract, request/response
schemas and frontend fixtures. [api/ENDPOINTS.md](api/ENDPOINTS.md) lists every
planned operation. These are templates; no HTTP routes are running yet.
