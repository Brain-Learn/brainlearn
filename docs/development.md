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
