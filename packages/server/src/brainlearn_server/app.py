"""FastAPI application serving the local BrainLearn user interface."""

import json
from pathlib import Path
from typing import Literal

import uvicorn
from brainlearn_core import (
    NodeManifest,
    ProjectManifest,
    ValidationResult,
    Workflow,
    validate_workflow,
)
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from brainlearn_server.auth import get_session_token, require_session_token
from brainlearn_server.capabilities import SystemCapabilities, inspect_system_capabilities
from brainlearn_server.project_store import ProjectStore
from brainlearn_server.registry import NODE_REGISTRY_BY_ID, get_node_manifest, list_node_manifests
from brainlearn_server.security import HostOriginValidationMiddleware

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "eeg-first-look.workflow.json"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "brainlearn-server"


class WorkflowValidationResponse(BaseModel):
    workflow: Workflow
    validation: ValidationResult


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


store = ProjectStore()

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
    return Workflow.model_validate(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))


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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _project_response(
        path, manifest, workflow, validate_workflow(workflow, NODE_REGISTRY_BY_ID)
    )


@app.get("/api/projects/recent", response_model=list[RecentProject])
def recent_projects(_auth: None = Depends(require_session_token)) -> list[RecentProject]:
    return [RecentProject.model_validate(entry) for entry in store.list_recent()]


def run() -> None:
    token_preview = get_session_token()[:6] + "…"
    print(f"BrainLearn session token (keep local): {get_session_token()}")
    print(f"Token preview for log redaction checks: {token_preview}")
    uvicorn.run("brainlearn_server.app:app", host="127.0.0.1", port=8000, reload=False)
