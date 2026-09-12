"""FastAPI application serving the local BrainLearn user interface."""

import json
from pathlib import Path
from typing import Literal

import uvicorn
from brainlearn_core import ValidationResult, Workflow, validate_workflow
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from brainlearn_server.capabilities import SystemCapabilities, inspect_system_capabilities

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_PATH = REPOSITORY_ROOT / "examples" / "eeg-first-look.workflow.json"


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "brainlearn-server"


app = FastAPI(
    title="BrainLearn local API",
    version="0.1.0",
    description="Local research workflow API. No scientific processing is implemented yet.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@app.get("/api/system/capabilities", response_model=SystemCapabilities)
def system_capabilities() -> SystemCapabilities:
    return inspect_system_capabilities()


@app.get("/api/workflows/example", response_model=Workflow)
def example_workflow() -> Workflow:
    return Workflow.model_validate(json.loads(EXAMPLE_PATH.read_text(encoding="utf-8")))


@app.get("/api/workflows/example/validation", response_model=ValidationResult)
def example_workflow_validation() -> ValidationResult:
    return validate_workflow(example_workflow())


def run() -> None:
    uvicorn.run("brainlearn_server.app:app", host="127.0.0.1", port=8000, reload=False)
