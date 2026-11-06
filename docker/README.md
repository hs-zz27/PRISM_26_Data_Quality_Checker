# Run the local application with Compose

Docker Desktop runs PostgreSQL, a one-shot migration service, FastAPI, the built
frontend, and five independent queue workers: extraction, import, audit, export,
and feedback. All workers start together and wait for jobs. They do not run model
inference merely because they are idle. Ollama stays installed on the host.

## Start

1. Start Docker Desktop. From the repository root, copy `.env.example` to `.env`
   **only on a new installation** and set `POSTGRES_PASSWORD`.
2. Stop native API/frontend servers and native workers before switching. They
   otherwise compete for ports and jobs. Leave PostgreSQL's existing container
   and volume in place: the `postgres` service and volume name are unchanged.
3. Keep your native Ollama server running for model-backed jobs. Docker Desktop
   provides `host.docker.internal`; the containers use port 11434. There is no
   model download, model server, or macOS virtual environment inside the images.
4. Run from the repository root:

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs -f --tail=50 api extraction-worker audit-worker
```

Open **http://localhost:5173**. API docs are at **http://localhost:8000/docs**.
The API service builds the backend image once; migrations and all workers reuse
that image without attempting separate builds or pulling it from a registry.
The first image build downloads Python/Node dependencies, including CPU PyTorch
wheels. Later starts reuse built images. `migrate` exiting with status 0 is normal.
A failed migration blocks the API and workers instead of starting against an
incompatible schema. Starting this stack explicitly applies pending migrations.

The frontend is a compiled build served by Nginx. It proxies `/api/v1/` to the API
on the internal Docker network, supports direct SPA links, and does not include
the development-only planned-endpoint mock fixtures. Rebuild after code changes.

## Ollama on the host

Check connectivity without generating a prediction:

```bash
docker compose exec api python -c "import urllib.request; r = urllib.request.urlopen('http://host.docker.internal:11434/api/tags', timeout=5); print(r.status)"
```

If the connection is refused, stop the existing Ollama server and start it in a
host terminal with a listener reachable from Docker:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

This listener can also be reachable from your LAN; restrict incoming connections
with your host firewall. Do not run a second Ollama server on an occupied port.
No CORS setting is needed: the backend, not the browser, contacts Ollama.

The application still rejects arbitrary remote model URLs. Only the exact
`host.docker.internal` hostname is additionally accepted when the container's
`CDIF_ALLOW_DOCKER_OLLAMA=1` flag is present. URL credentials, paths, queries and
redirect behavior remain restricted. Model digests and CPU execution stay pinned.

This setup targets Docker Desktop (Mac/Windows). Linux Docker Engine additionally
needs `extra_hosts: ["host.docker.internal:host-gateway"]` on each backend service,
and a host Ollama listener reachable from that gateway.

## Storage and existing native data

The PostgreSQL named volume is preserved across `up`, `stop`, and `down`.
Assets, import staging and exports use host bind mounts, shared by every backend
service at the **same absolute path**. Defaults are the repository's
`backend/data/assets`, `backend/data/imports`, and `backend/data/exports`.

If your database was populated from another checkout, set `ASSET_STORAGE_ROOT`,
`DATASET_IMPORT_ROOT`, and `EXPORT_ROOT` in `.env` to that checkout's existing
absolute directories **before starting**. On macOS prefer canonical paths
(`/private/tmp/...`, rather than the `/tmp` symlink). Do not point a populated
database at empty directories. Keep these directories on a persistent disk;
changing database/storage locations is a separate migration, not part of startup.
Do not delete a temporary checkout while it still holds your demo assets.

Existing native model jobs may pin `127.0.0.1` as their Ollama host; inside a
container that address refers to the container. Finish those jobs and their
rechecks with the original native setup. Create new model audits in Compose;
never rewrite immutable execution pins just to move a run between environments.
Legacy audits may also reference source paths outside these mounts. Snapshot
imports created through the application use the shared asset root.

Compose ignores a native `DATABASE_URL` inside containers and uses the existing
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` with service `postgres:5432`.
Host access still uses `POSTGRES_PORT` (55432 by default). No credentials or private
assets are copied into build images. Run from the same repository/project name to
reuse the same database volume; a different Compose project uses a different one.

## Person check model (Mask R-CNN)

The person check (`person_coverage`) uses torchvision's `maskrcnn_resnet50_fpn_v2` with COCO
weights. Nothing is downloaded by the application. Place the weights file once:

```bash
mkdir -p backend/data/models
curl -L -o backend/data/models/maskrcnn_resnet50_fpn_v2_coco-73cbd019.pth \
  https://download.pytorch.org/models/maskrcnn_resnet50_fpn_v2_coco-73cbd019.pth
```

The folder is mounted read-only at `/models` (set `PERSON_MODEL_ROOT` to mount another one).
The top bar's Mask R-CNN light shows whether the file is found. Without it, the person check
reports "could not check" for each image, never a pass. Model settings for enclosure/feedback
still require their explicit enablement and digests; starting Compose does not enable them.

## Stop and troubleshoot

```bash
docker compose logs --tail=100 migrate
docker compose logs -f --tail=50 import-worker export-worker feedback-worker
docker compose stop
# Restart without rebuilding:
docker compose up -d
```

`stop` preserves containers and all data. `down` also preserves the database volume
unless you add `--volumes`; do not add that option when retaining your database.
Workers have a 16-minute shutdown grace period for the default bounded sample.
Cancel long jobs in the application before stopping if you want a quicker shutdown.
If you raise processing limits, raise `COMPOSE_STOP_GRACE_PERIOD` accordingly.
A forced stop still relies on the existing lease recovery and ownership safeguards.

## Verification status

Compose configuration and whitespace are checked without starting services.
Container builds, migrations, runtime smoke checks and tests must be run separately;
configuration validation alone does not establish runtime compatibility of the
pinned Linux dependencies. No tests, model inference or database operations are
performed by the source-editing step.
