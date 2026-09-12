# Local development

This foundation runs a Python service on loopback and a Vite development server. It renders an example EEG workflow, validates its graph, and reports lightweight system capabilities. It does not execute EEG processing.

## Prerequisites

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 LTS or newer and npm

## Install

From the repository root:

```bash
uv sync --all-packages
npm --prefix apps/web install
```

The generated `uv.lock` and `apps/web/package-lock.json` should remain committed so a clean checkout resolves the reviewed dependency set.

## Run

Start the API in one terminal:

```bash
uv run uvicorn brainlearn_server.app:app --host 127.0.0.1 --port 8000 --reload
```

Start the interface in another terminal:

```bash
npm --prefix apps/web run dev
```

Open `http://127.0.0.1:5173`. Select a node to inspect its parameters and scientific port types. Vite proxies `/api` calls to the local service.

The canvas starts empty. Select registry entries to add nodes, drag compatible output and input handles to connect them, and select nodes or edges before pressing Delete to remove them. Parameter controls come from the backend manifest. Undo and redo cover graph edits. The Validate graph action sends the complete in-memory graph to the backend and confirms whether the returned workflow round-trips unchanged. All nodes remain non-executing examples.

## Local projects and drafts

The service prints a per-process session token at startup. Paste it into the
Local project panel once per browser session; it is stored in `sessionStorage`
and sent as `Authorization: Bearer <token>` on `/api/projects/*` calls only.

- Enter an absolute folder under Local project, then Create, Open, Save,
  Save as, or Recent. `create`/`save-as` require a new or empty folder and
  never overwrite existing work; `save` keeps `workflow.previous.json`.
  Typing a folder never changes the active project: Create, Open, and Save as
  switch the active project only after a current successful response, and Save
  always writes to the shown active project (disabled when none is active).
  A failed or ignored operation leaves the graph, active path, and draft
  association unchanged.
- Opening a folder explicitly authorizes exactly that directory. Saves to any
  other canonical path return `403`, and relative paths return `400`.
- Unsaved edits autosave to `localStorage` (`brainlearn.unsaved-workflow.v1`)
  and a banner offers recovery after a browser or app interruption. Discard
  clears the draft; the next edit starts a new one.
- Read `docs/threat-model.md` before changing Host/Origin handling or file
  access. The service binds to loopback, validates Host/Origin, and requires
  the session token for filesystem endpoints.

Useful API checks:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/system/capabilities
curl http://127.0.0.1:8000/api/workflows/example/validation
```

## Verify changes

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
npm --prefix apps/web run lint
npm --prefix apps/web run format:check
npm --prefix apps/web run test
npm --prefix apps/web run build
```

The system capability response separates lightweight discovery from validation. CUDA candidate detection requires both an installed PyTorch package and a detectable `nvidia-smi` tool; MPS candidate detection requires PyTorch on Apple Silicon. Container candidates are detected from their executables. Every result keeps `runtime_validated` false because this endpoint does not start a runtime or import PyTorch. Candidate detection does not certify hardware or containers for scientific execution.
