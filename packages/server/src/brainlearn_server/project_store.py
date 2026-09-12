"""Filesystem-backed project store with explicit root authorization."""

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brainlearn_core import (
    PREVIOUS_WORKFLOW_FILENAME,
    PROJECT_MANIFEST_FILENAME,
    WORKFLOW_FILENAME,
    GraphMetadata,
    ProjectManifest,
    Workflow,
    migrate_project_dict,
    migrate_workflow_dict,
    new_project_manifest,
    validate_workflow,
)
from brainlearn_core.validation import ValidationResult

from brainlearn_server.registry import NODE_REGISTRY_BY_ID

RECENT_FILENAME = "recent.json"


def _default_state_dir() -> Path:
    override = os.environ.get("BRAINLEARN_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "brainlearn"


def canonicalize_project_path(raw: str) -> Path:
    """Resolve a user-supplied path without touching disallowed locations."""

    if not raw or not raw.strip():
        raise ValueError("Project path must be a non-empty absolute path.")
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        raise ValueError(
            f"Project path '{raw}' must be absolute. "
            "Choose a directory inside an explicitly opened project folder."
        )
    # ``strict=False`` normalizes ``..`` and symlinks that already exist
    # without creating anything on disk.
    return candidate.resolve(strict=False)


def atomic_write_json(target: Path, payload: dict[str, Any] | list[Any]) -> None:
    """Write JSON atomically so an interruption never leaves a half-written file."""

    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@dataclass
class ProjectStore:
    state_dir: Path = field(default_factory=_default_state_dir)
    allowed_roots: set[Path] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # -- authorization -------------------------------------------------
    def register_root(self, root: Path) -> Path:
        resolved = root.resolve(strict=False)
        self.allowed_roots.add(resolved)
        return resolved

    def is_allowed(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(resolved == root or resolved.is_relative_to(root) for root in self.allowed_roots)

    def require_allowed(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if not self.is_allowed(resolved):
            raise PermissionError(
                f"Project path '{resolved}' is outside the explicitly opened "
                "project folders. Open or create the project first so the "
                "researcher authorizes that directory."
            )
        return resolved

    # -- recent projects -----------------------------------------------
    def _recent_path(self) -> Path:
        return self.state_dir / RECENT_FILENAME

    def list_recent(self) -> list[dict[str, str]]:
        recent_path = self._recent_path()
        if not recent_path.exists():
            return []
        try:
            payload = json.loads(recent_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        entries: list[dict[str, str]] = []
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                entries.append(
                    {
                        "path": str(item["path"]),
                        "name": str(item.get("name", "")),
                        "last_opened": str(item.get("last_opened", "")),
                    }
                )
        return entries

    def add_recent(self, path: Path, name: str, timestamp: str) -> None:
        entries = [entry for entry in self.list_recent() if entry["path"] != str(path)]
        entries.insert(0, {"path": str(path), "name": name, "last_opened": timestamp})
        atomic_write_json(self._recent_path(), [entry for entry in entries[:20]])

    # -- project documents ----------------------------------------------
    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
        return slug or "project"

    def _default_workflow(self, name: str) -> Workflow:
        workflow_id = f"{self._slugify(name)}-{uuid.uuid4().hex[:6]}"
        return Workflow(
            schema_version="1.0",
            id=workflow_id,
            metadata=GraphMetadata(
                name=name,
                description="Local BrainLearn project. Nodes do not execute yet.",
                created_with="BrainLearn 0.1.0",
                modality="EEG",
                status="example",
            ),
            nodes=[],
            edges=[],
        )

    def create_project(
        self, raw_path: str, name: str, workflow: Workflow | None = None
    ) -> tuple[Path, ProjectManifest, Workflow]:
        target = canonicalize_project_path(raw_path)
        manifest_path = target / PROJECT_MANIFEST_FILENAME
        if manifest_path.exists() or (target.exists() and any(target.iterdir())):
            # Refuse to overwrite user work: a project directory must be new or
            # empty. Use save-as with a different folder for copies.
            if manifest_path.exists():
                raise FileExistsError(
                    f"Project '{target}' already exists. Open it or choose "
                    "a different folder for save-as."
                )
            if target.exists() and any(target.iterdir()):
                raise FileExistsError(
                    f"Directory '{target}' is not empty. Choose an empty folder for a new project."
                )
        clean_name = name.strip() or "Untitled project"
        graph = workflow if workflow is not None else self._default_workflow(clean_name)
        project_id = f"{self._slugify(clean_name)}-{uuid.uuid4().hex[:8]}"
        manifest = new_project_manifest(
            project_id=project_id,
            name=clean_name,
            workflow_id=graph.id,
        )
        target.mkdir(parents=True, exist_ok=True)
        atomic_write_json(target / WORKFLOW_FILENAME, graph.model_dump(mode="json"))
        atomic_write_json(target / PROJECT_MANIFEST_FILENAME, manifest.model_dump(mode="json"))
        self.register_root(target)
        self.add_recent(target, manifest.name, manifest.updated_at)
        return target, manifest, graph

    def open_project(self, raw_path: str) -> tuple[Path, ProjectManifest, Workflow]:
        target = canonicalize_project_path(raw_path)
        manifest_path = target / PROJECT_MANIFEST_FILENAME
        workflow_path = target / WORKFLOW_FILENAME
        if not manifest_path.is_file() or not workflow_path.is_file():
            raise FileNotFoundError(
                f"'{target}' is not a BrainLearn project directory. "
                f"It must contain {PROJECT_MANIFEST_FILENAME} and {WORKFLOW_FILENAME}."
            )
        # Opening is the explicit user authorization for this root.
        self.register_root(target)
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        workflow_raw = json.loads(workflow_path.read_text(encoding="utf-8"))
        if not isinstance(manifest_raw, dict) or not isinstance(workflow_raw, dict):
            raise ValueError(f"Project '{target}' contains invalid JSON documents.")
        manifest = ProjectManifest.model_validate(migrate_project_dict(manifest_raw))
        graph = Workflow.model_validate(migrate_workflow_dict(workflow_raw))
        self.add_recent(target, manifest.name, manifest.updated_at)
        return target, manifest, graph

    def save_project(
        self,
        raw_path: str,
        workflow: Workflow,
        name: str | None = None,
    ) -> tuple[Path, ProjectManifest, Workflow, ValidationResult]:
        target = self.require_allowed(canonicalize_project_path(raw_path))
        manifest_path = target / PROJECT_MANIFEST_FILENAME
        workflow_path = target / WORKFLOW_FILENAME
        if not manifest_path.is_file() or not workflow_path.is_file():
            raise FileNotFoundError(
                f"'{target}' is not an opened BrainLearn project. "
                "Create or open the project before saving."
            )
        manifest = ProjectManifest.model_validate(
            migrate_project_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
        )
        new_validation = validate_workflow(workflow, NODE_REGISTRY_BY_ID)
        # Keep the most recent semantically valid persisted graph in the
        # recovery file. Work-in-progress saves may be invalid, but an invalid
        # graph must never replace the last-valid recovery copy.
        try:
            previous_raw = json.loads(workflow_path.read_text(encoding="utf-8"))
            previous_graph = Workflow.model_validate(migrate_workflow_dict(previous_raw))
            previous_valid = validate_workflow(previous_graph, NODE_REGISTRY_BY_ID).valid
        except (OSError, ValueError):
            previous_graph = None
            previous_valid = False
        if previous_graph is not None and previous_valid:
            atomic_write_json(
                target / PREVIOUS_WORKFLOW_FILENAME,
                previous_graph.model_dump(mode="json"),
            )
        atomic_write_json(workflow_path, workflow.model_dump(mode="json"))
        updated = manifest.model_copy(
            update={
                "name": name.strip() if name and name.strip() else manifest.name,
                "workflow_id": workflow.id,
                "updated_at": manifest.updated_at,
            }
        )
        # Refresh updated_at explicitly so run records can order saves.
        from brainlearn_core.projects import utc_now_iso

        updated = updated.model_copy(update={"updated_at": utc_now_iso()})
        atomic_write_json(manifest_path, updated.model_dump(mode="json"))
        self.add_recent(target, updated.name, updated.updated_at)
        return target, updated, workflow, new_validation
