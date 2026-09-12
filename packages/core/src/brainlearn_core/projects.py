"""Versioned local project manifest and persisted layout.

A BrainLearn project is a directory chosen explicitly by the researcher::

    <project-root>/
      brainlearn.project.json   project manifest (versioned)
      workflow.json             canonical workflow graph (schema_version 1.0)
      workflow.previous.json    previous valid graph kept after each save

The manifest never embeds large artifacts; it references the workflow file and
records which workflow it was saved with. Migrations are explicit so old
project directories keep loading after schema updates.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECT_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SUPPORTED_PROJECT_VERSIONS: tuple[str, ...] = ("1.0",)

PROJECT_MANIFEST_FILENAME = "brainlearn.project.json"
WORKFLOW_FILENAME = "workflow.json"
PREVIOUS_WORKFLOW_FILENAME = "workflow.previous.json"


class ProjectManifest(BaseModel):
    """Small versioned record describing one project directory."""

    model_config = ConfigDict(extra="forbid")

    project_schema_version: Literal["1.0"] = PROJECT_SCHEMA_VERSION
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    workflow_id: str = Field(min_length=1)
    brainlearn_version: str = Field(min_length=1, default="0.1.0")
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_project_manifest(
    project_id: str,
    name: str,
    workflow_id: str,
    description: str = "",
    brainlearn_version: str = "0.1.0",
) -> ProjectManifest:
    now = utc_now_iso()
    return ProjectManifest(
        project_schema_version=PROJECT_SCHEMA_VERSION,
        id=project_id,
        name=name,
        description=description,
        workflow_id=workflow_id,
        brainlearn_version=brainlearn_version,
        created_at=now,
        updated_at=now,
    )


def migrate_project_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw project manifest dict to the current version.

    Only ``1.0`` exists today, so this validates the version marker and
    returns the payload unchanged. Unknown versions raise ``ValueError``
    with an actionable message instead of loading silently.
    """

    version = data.get("project_schema_version")
    if version not in SUPPORTED_PROJECT_VERSIONS:
        raise ValueError(
            f"Unsupported project_schema_version {version!r}. "
            f"Supported versions: {', '.join(SUPPORTED_PROJECT_VERSIONS)}. "
            "Open the project with a compatible BrainLearn release."
        )
    return data


def migrate_workflow_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw workflow dict to the current workflow schema."""

    version = data.get("schema_version")
    if version != "1.0":
        raise ValueError(
            f"Unsupported workflow schema_version {version!r}. "
            "Supported versions: 1.0. "
            "Open the workflow with a compatible BrainLearn release."
        )
    return data
