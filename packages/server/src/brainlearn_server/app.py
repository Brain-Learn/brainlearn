"""FastAPI application serving the local BrainLearn user interface."""

import json
import re
import time
import unicodedata
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import uvicorn
from brainlearn_core import (
    RUN_TERMINAL_STATES,
    NodeManifest,
    ProjectManifest,
    RunRecord,
    ValidationResult,
    Workflow,
    migrate_workflow_dict,
    validate_workflow,
)
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from brainlearn_server.auth import get_session_token, require_session_token
from brainlearn_server.capabilities import SystemCapabilities, inspect_system_capabilities
from brainlearn_server.error_log import ErrorLoggingMiddleware, error_log_dir
from brainlearn_server.project_store import ProjectStore
from brainlearn_server.registry import NODE_REGISTRY_BY_ID, get_node_manifest, list_node_manifests
from brainlearn_server.run_store import RunStore
from brainlearn_server.security import HostOriginValidationMiddleware
from brainlearn_server.worker import ReviewConflictError, WorkerService, build_run_record

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "eeg-first-look.workflow.json"
EXAMPLE_WORKFLOWS: dict[str, Path] = {
    "eeg-first-look": EXAMPLE_PATH,
    "demo-branched": REPOSITORY_ROOT / "examples" / "demo-branched.workflow.json",
}


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "brainlearn-server"


class WorkflowValidationResponse(BaseModel):
    workflow: Workflow
    validation: ValidationResult


class ExampleInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    schema_version: Literal["1.0"] = "1.0"


class ExampleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    examples: list[ExampleInfo]


def load_example_workflow(example_id: str) -> Workflow:
    """Load one versioned example workflow by its stable route key."""

    path = EXAMPLE_WORKFLOWS.get(example_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown example workflow {example_id!r}.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Example workflow {example_id!r} is unreadable."
        ) from exc
    return Workflow.model_validate(migrate_workflow_dict(raw))


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    name: str = Field(default="Untitled project")
    workflow: Workflow | None = None


class ProjectOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class ProjectSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    workflow: Workflow
    name: str | None = None


class ProjectSaveAsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dest_path: str = Field(min_length=1)
    workflow: Workflow
    name: str | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    manifest: dict[str, object]
    workflow: Workflow
    validation: ValidationResult


class RecentProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    name: str = ""
    last_opened: str = ""


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    run: RunRecord


class RunOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class RunSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    run: RunRecord


class ArtifactOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)


class RunRecoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    run_id: str
    run: RunRecord


store = ProjectStore()
runs = RunStore(store)
workers = WorkerService(runs)

app = FastAPI(
    title="BrainLearn local API",
    version="0.1.0",
    description="Local research workflow API. No scientific processing is implemented yet.",
)
app.add_middleware(HostOriginValidationMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-BrainLearn-Token"],
)
app.add_middleware(ErrorLoggingMiddleware)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/api/system/capabilities", response_model=SystemCapabilities)
def system_capabilities() -> SystemCapabilities:
    return inspect_system_capabilities()


@app.get("/api/registry/nodes", response_model=list[NodeManifest])
def node_registry() -> list[NodeManifest]:
    return list_node_manifests()


@app.get("/api/registry/nodes/{node_type}", response_model=NodeManifest)
def node_registry_detail(node_type: str) -> NodeManifest:
    manifest = get_node_manifest(node_type)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Node manifest '{node_type}' was not found.")
    return manifest


@app.get("/api/workflows/example", response_model=Workflow)
def example_workflow() -> Workflow:
    return load_example_workflow("eeg-first-look")


@app.get("/api/workflows/examples", response_model=ExampleListResponse)
def list_example_workflows() -> ExampleListResponse:
    infos: list[ExampleInfo] = []
    for example_id in EXAMPLE_WORKFLOWS:
        workflow = load_example_workflow(example_id)
        infos.append(
            ExampleInfo(
                id=example_id,
                name=workflow.metadata.name,
                description=workflow.metadata.description,
            )
        )
    return ExampleListResponse(examples=infos)


@app.get("/api/workflows/examples/{example_id}", response_model=Workflow)
def example_workflow_by_id(example_id: str) -> Workflow:
    return load_example_workflow(example_id)


@app.get("/api/workflows/example/validation", response_model=ValidationResult)
def example_workflow_validation() -> ValidationResult:
    return validate_workflow(example_workflow(), NODE_REGISTRY_BY_ID)


@app.post("/api/workflows/validate", response_model=WorkflowValidationResponse)
def submitted_workflow_validation(workflow: Workflow) -> WorkflowValidationResponse:
    return WorkflowValidationResponse(
        workflow=workflow,
        validation=validate_workflow(workflow, NODE_REGISTRY_BY_ID),
    )


def _project_response(
    path: Path, manifest: ProjectManifest, workflow: Workflow, validation: ValidationResult
) -> ProjectResponse:
    return ProjectResponse(
        path=str(path),
        manifest=manifest.model_dump(mode="json"),
        workflow=workflow,
        validation=validation,
    )


@app.post("/api/projects/create", response_model=ProjectResponse)
def create_project(
    payload: ProjectCreateRequest, _auth: None = Depends(require_session_token)
) -> ProjectResponse:
    try:
        path, manifest, workflow = store.create_project(
            payload.path, payload.name, payload.workflow
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(
        path, manifest, workflow, validate_workflow(workflow, NODE_REGISTRY_BY_ID)
    )


@app.post("/api/projects/open", response_model=ProjectResponse)
def open_project(
    payload: ProjectOpenRequest, _auth: None = Depends(require_session_token)
) -> ProjectResponse:
    try:
        path, manifest, workflow = store.open_project(payload.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(
        path, manifest, workflow, validate_workflow(workflow, NODE_REGISTRY_BY_ID)
    )


@app.post("/api/projects/save", response_model=ProjectResponse)
def save_project(
    payload: ProjectSaveRequest, _auth: None = Depends(require_session_token)
) -> ProjectResponse:
    try:
        path, manifest, workflow, validation = store.save_project(
            payload.path, payload.workflow, payload.name
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(path, manifest, workflow, validation)


@app.post("/api/projects/save-as", response_model=ProjectResponse)
def save_project_as(
    payload: ProjectSaveAsRequest, _auth: None = Depends(require_session_token)
) -> ProjectResponse:
    try:
        path, manifest, workflow = store.create_project(
            payload.dest_path, payload.name or "Untitled project", payload.workflow
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(
        path, manifest, workflow, validate_workflow(workflow, NODE_REGISTRY_BY_ID)
    )


@app.get("/api/projects/recent", response_model=list[RecentProject])
def recent_projects(_auth: None = Depends(require_session_token)) -> list[RecentProject]:
    return [RecentProject.model_validate(entry) for entry in store.list_recent()]


@app.post("/api/runs/create", response_model=RunResponse)
def create_run(
    payload: RunCreateRequest, _auth: None = Depends(require_session_token)
) -> RunResponse:
    try:
        record = runs.create_run(payload.path, payload.run)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunResponse(path=payload.path, run_id=record.id, run=record)


@app.post("/api/runs/open", response_model=RunResponse)
def open_run(payload: RunOpenRequest, _auth: None = Depends(require_session_token)) -> RunResponse:
    try:
        record = runs.get_run(payload.path, payload.run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunResponse(path=payload.path, run_id=record.id, run=record)


@app.post("/api/runs/save", response_model=RunResponse)
def save_run(payload: RunSaveRequest, _auth: None = Depends(require_session_token)) -> RunResponse:
    try:
        record = runs.save_run(payload.path, payload.run)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunResponse(path=payload.path, run_id=record.id, run=record)


@app.get("/api/runs/list", response_model=list[RunResponse])
def list_runs(path: str, _auth: None = Depends(require_session_token)) -> list[RunResponse]:
    try:
        records = runs.list_runs(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [RunResponse(path=path, run_id=record.id, run=record) for record in records]


def content_disposition(filename: str) -> str:
    """Build a header-safe attachment disposition for a download filename.

    The raw name is never interpolated: the ``filename`` fallback carries
    pure ASCII (transliterated, with quotes, backslashes, and controls
    replaced) so the header value itself stays valid HTTP, while
    ``filename*`` carries the exact name percent-encoded per RFC 5987/6266.
    """

    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r'["\\\x00-\x1f\x7f]', "_", ascii_name).strip() or "artifact"
    encoded = urllib.parse.quote(filename, safe="")
    if fallback == filename and filename.isascii():
        return f'attachment; filename="{fallback}"'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


@app.post("/api/artifacts/open")
def open_artifact(
    payload: ArtifactOpenRequest, _auth: None = Depends(require_session_token)
) -> StreamingResponse:
    from brainlearn_server.run_store import _ARTIFACT_CHUNK_SIZE

    try:
        stream, artifact = runs.open_artifact_stream(
            payload.path, payload.run_id, payload.artifact_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = artifact.path.rsplit("/", 1)[-1] or artifact.artifact_id

    def _chunks() -> Iterator[bytes]:
        try:
            while True:
                data = stream.read(_ARTIFACT_CHUNK_SIZE)
                if not data:
                    break
                yield data
        finally:
            stream.close()

    return StreamingResponse(
        _chunks(),
        media_type=artifact.media_type,
        headers={"Content-Disposition": content_disposition(filename)},
    )


@app.post("/api/runs/recover", response_model=list[RunResponse])
def recover_runs(
    payload: RunRecoverRequest, _auth: None = Depends(require_session_token)
) -> list[RunResponse]:
    try:
        records = workers.recover_project(payload.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [RunResponse(path=payload.path, run_id=record.id, run=record) for record in records]


class RunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    workflow: Workflow | None = None
    run_id: str | None = None
    seed: int = 0


class RunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class RunReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    node_run_id: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    note: str = ""


@app.post("/api/runs/start", response_model=RunResponse)
def start_run(
    payload: RunStartRequest, _auth: None = Depends(require_session_token)
) -> RunResponse:
    if (payload.workflow is None) == (payload.run_id is None):
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'workflow' (new run) or 'run_id' (resume).",
        )
    try:
        if payload.workflow is not None:
            record = workers.runs.create_run(
                payload.path, build_run_record(payload.workflow, seed=payload.seed)
            )
            started = workers.start_existing(payload.path, record.id)
        else:
            started = workers.start_existing(payload.path, payload.run_id or "")
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunResponse(path=payload.path, run_id=started.id, run=started)


@app.post("/api/runs/cancel", response_model=RunResponse)
def cancel_run(
    payload: RunCancelRequest, _auth: None = Depends(require_session_token)
) -> RunResponse:
    try:
        record = workers.cancel_run(payload.path, payload.run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunResponse(path=payload.path, run_id=record.id, run=record)


@app.post("/api/runs/review", response_model=RunResponse)
def review_run(
    payload: RunReviewRequest, _auth: None = Depends(require_session_token)
) -> RunResponse:
    try:
        record = workers.review_node(
            payload.path, payload.run_id, payload.node_run_id, payload.decision, payload.note
        )
    except ReviewConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunResponse(path=payload.path, run_id=record.id, run=record)


@app.get("/api/runs/events")
def run_events(
    path: str, run_id: str, after: int = -1, _auth: None = Depends(require_session_token)
) -> StreamingResponse:
    try:
        runs.get_run(path, run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _stream() -> Iterator[str]:
        last = after
        deadline = time.monotonic() + 30.0
        while True:
            try:
                record = runs.get_run(path, run_id)
            except (FileNotFoundError, ValueError, PermissionError):
                break
            for event in record.events:
                if event.seq > last:
                    yield f"data: {event.model_dump_json()}\n\n"
                    last = event.seq
            if record.state in RUN_TERMINAL_STATES:
                break
            if time.monotonic() > deadline:
                break
            time.sleep(0.25)

    return StreamingResponse(_stream(), media_type="text/event-stream")


def run() -> None:
    token_preview = get_session_token()[:6] + "…"
    print(f"BrainLearn session token (keep local): {get_session_token()}")
    print(f"Token preview for log redaction checks: {token_preview}")
    print(f"Fault logs (date folder, hour file): {error_log_dir()}")
    uvicorn.run("brainlearn_server.app:app", host="127.0.0.1", port=8000, reload=False)
