"""Versioned, project-local researcher annotations for inspected EEG recordings."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from brainlearn_server.project_store import ProjectStore, atomic_write_json

_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[Path, threading.RLock] = {}


def _project_lock(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _PROJECT_LOCKS.setdefault(path, threading.RLock())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ResearcherDecision(BaseModel):
    """One explicit human annotation or inspection/QC decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    id: str
    researcher: str = Field(min_length=1, max_length=200)
    dataset_path: str = Field(min_length=1, max_length=1024)
    recording_path: str = Field(min_length=1, max_length=1024)
    source_content_identity: str = Field(min_length=1, max_length=256)
    decision: Literal["accepted", "rejected", "needs_review"]
    note: str = Field(default="", max_length=4000)
    time_start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    time_end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    channel_names: tuple[str, ...] = Field(default=(), max_length=512)
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def validate_location(self) -> ResearcherDecision:
        if self.time_start_seconds is not None and self.time_end_seconds is not None:
            if self.time_end_seconds <= self.time_start_seconds:
                raise ValueError("Annotation end time must follow its start time.")
        if any(not name or len(name) > 512 for name in self.channel_names):
            raise ValueError("Channel names must be non-empty and at most 512 characters.")
        if len(set(self.channel_names)) != len(self.channel_names):
            raise ValueError("Channel names must be unique.")
        return self


class ResearcherDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    researcher: str = Field(min_length=1, max_length=200)
    dataset_path: str = Field(min_length=1, max_length=1024)
    recording_path: str = Field(min_length=1, max_length=1024)
    source_content_identity: str = Field(min_length=1, max_length=256)
    decision: Literal["accepted", "rejected", "needs_review"]
    note: str = Field(default="", max_length=4000)
    time_start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    time_end_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    channel_names: tuple[str, ...] = Field(default=(), max_length=512)


class ResearcherDecisionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    records: tuple[ResearcherDecision, ...] = Field(default=(), max_length=10000)


class ResearcherDecisionStore:
    """Store human decisions beneath an authorized project, away from raw BIDS data."""

    def __init__(self, projects: ProjectStore) -> None:
        self.projects = projects

    def _collection_path(self, raw_project_path: str) -> tuple[Path, Path]:
        project = self.projects.require_allowed(Path(raw_project_path).expanduser())
        if (
            not project.is_dir()
            or not (project / "brainlearn.project.json").is_file()
            or not (project / "workflow.json").is_file()
        ):
            raise ValueError("The selected directory is not an open BrainLearn project.")
        directory = project / "annotations"
        target = directory / "eeg-researcher-decisions.json"
        for path in (directory, target):
            if path.is_symlink():
                raise OSError("Researcher decision storage is blocked by a symlink.")
        if directory.exists() and not directory.is_dir():
            raise OSError("Researcher decision storage is not a directory.")
        if target.exists() and not target.is_file():
            raise OSError("Researcher decision storage is not a regular file.")
        return project, target

    @staticmethod
    def _read(target: Path) -> ResearcherDecisionCollection:
        if not target.exists():
            return ResearcherDecisionCollection()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            return ResearcherDecisionCollection.model_validate(payload)
        except (OSError, ValueError) as exc:
            raise OSError("The saved researcher decisions are unreadable or invalid.") from exc

    def list(
        self,
        raw_project_path: str,
        dataset_path: str,
        recording_path: str,
        source_content_identity: str,
    ) -> ResearcherDecisionCollection:
        project, target = self._collection_path(raw_project_path)
        with _project_lock(project):
            collection = self._read(target)
        selected = tuple(
            item
            for item in collection.records
            if item.dataset_path == dataset_path
            and item.recording_path == recording_path
            and item.source_content_identity == source_content_identity
        )
        return ResearcherDecisionCollection(records=selected)

    def create(
        self, raw_project_path: str, payload: ResearcherDecisionCreate
    ) -> ResearcherDecision:
        project, target = self._collection_path(raw_project_path)
        now = _now()
        record = ResearcherDecision(
            **payload.model_dump(),
            id=f"decision-{uuid.uuid4().hex}",
            created_at=now,
            updated_at=now,
        )
        with _project_lock(project):
            collection = self._read(target)
            updated = ResearcherDecisionCollection(records=(*collection.records, record))
            atomic_write_json(target, updated.model_dump(mode="json"))
        return record
