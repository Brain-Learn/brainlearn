"""Filesystem-backed project store with explicit root authorization."""

import json
import os
import re
import tempfile
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brainlearn_core import (
    PREVIOUS_WORKFLOW_FILENAME,
    PROJECT_MANIFEST_FILENAME,
    WORKFLOW_FILENAME,
    CanvasPosition,
    GraphMetadata,
    ProjectManifest,
    Workflow,
    migrate_project_dict,
    migrate_workflow_dict,
    new_project_manifest,
    validate_workflow,
)
from brainlearn_core.validation import ValidationResult

from brainlearn_server.registry import (
    NODE_REGISTRY_BY_ID,
    instantiate_registered_node,
)

RECENT_FILENAME = "recent.json"

_LEGACY_FILTER_DESCRIPTION = "Configure a visible, provenance-tracked frequency filter."
_LEGACY_ICA_REVIEW_DESCRIPTION = "Pause for a researcher to review proposed ICA components."


def migrate_project_workflow_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Apply project-schema migrations and exact registered-node migrations."""

    migrated = migrate_workflow_dict(data)
    nodes = migrated.get("nodes")
    edges = migrated.get("edges")
    if not isinstance(nodes, list):
        return migrated
    edge_list = edges if isinstance(edges, list) else []
    legacy_ica_sources = _legacy_ica_review_sources(nodes, edge_list)
    migrated_nodes = [
        _migrate_legacy_filter_node(node)
        if not _is_legacy_ica_review_node(node)
        else _migrate_legacy_ica_review_node(node)
        if isinstance(node, dict) and node.get("id") in legacy_ica_sources
        else node
        for node in nodes
    ]
    additions: list[dict[str, Any]] = []
    migrated_edges = list(edge_list)
    used_node_ids = {
        node.get("id")
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    for index, (review_node_id, (source_node_id, source_port_id)) in enumerate(
        legacy_ica_sources.items(), start=1
    ):
        fit_node_id = f"ica-fit-migration-{index}"
        while fit_node_id in used_node_ids:
            fit_node_id += "-next"
        used_node_ids.add(fit_node_id)
        legacy_review = next(
            node
            for node in migrated_nodes
            if isinstance(node, dict) and node.get("id") == review_node_id
        )
        position = legacy_review.get("position", {})
        x = position.get("x", 0) if isinstance(position, dict) else 0
        y = position.get("y", 0) if isinstance(position, dict) else 0
        x = float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0
        y = float(y) if isinstance(y, (int, float)) and not isinstance(y, bool) else 0.0
        legacy_review["position"] = {"x": x + 180.0, "y": y}
        fit_node = instantiate_registered_node(
            "eeg.ica_fit",
            fit_node_id,
            CanvasPosition(x=max(0.0, x), y=y + 100.0),
        )
        additions.append(fit_node.model_dump(mode="json"))
        used_edge_ids = {edge.get("id") for edge in migrated_edges if isinstance(edge, dict)}
        for suffix, source, target in (
            (
                "fit-input",
                {"node_id": source_node_id, "port_id": source_port_id},
                {"node_id": fit_node_id, "port_id": "raw"},
            ),
            (
                "fit-review",
                {"node_id": fit_node_id, "port_id": "ica"},
                {"node_id": review_node_id, "port_id": "fit"},
            ),
        ):
            edge_id = f"migration-ica-{index}-{suffix}"
            while edge_id in used_edge_ids:
                edge_id += "-next"
            used_edge_ids.add(edge_id)
            migrated_edges.append({"id": edge_id, "source": source, "target": target})
    if not additions and all(
        before is after for before, after in zip(nodes, migrated_nodes, strict=True)
    ):
        return migrated
    result = dict(migrated)
    result["nodes"] = [*migrated_nodes, *additions]
    if isinstance(edges, list):
        result["edges"] = migrated_edges
    return result


def _is_legacy_ica_review_node(value: Any) -> bool:
    """Recognize only the original illustrative ICA review placeholder."""

    if not isinstance(value, dict) or value.get("type") != "eeg.ica_review":
        return False
    expected_ports = [
        ("raw", "Raw EEG", "input", "raw_eeg"),
        ("reviewed", "Reviewed EEG", "output", "reviewed_eeg"),
    ]
    ports = value.get("ports")
    return (
        value.get("label") == "ICA Review"
        and value.get("category") == "Quality control"
        and value.get("description") == _LEGACY_ICA_REVIEW_DESCRIPTION
        and value.get("pauses_for_review") is True
        and value.get("parameters") == []
        and isinstance(ports, list)
        and len(ports) == len(expected_ports)
        and all(
            isinstance(port, dict)
            and not (set(port) - {"id", "label", "direction", "data_type", "required"})
            and (
                port.get("id"),
                port.get("label"),
                port.get("direction"),
                port.get("data_type"),
            )
            == expected
            and port.get("required", True) is True
            for port, expected in zip(ports, expected_ports, strict=True)
        )
    )


def _migrate_legacy_ica_review_node(value: dict[str, Any]) -> dict[str, Any]:
    """Upgrade the exact placeholder to the explicit raw + fitted-model contract."""

    manifest = NODE_REGISTRY_BY_ID.get("eeg.ica_review")
    if manifest is None:
        return value
    migrated = deepcopy(value)
    migrated["label"] = manifest.label
    migrated["category"] = manifest.category
    migrated["description"] = manifest.description
    migrated["pauses_for_review"] = manifest.review_behavior == "required"
    migrated["ports"] = [port.model_dump(mode="json") for port in manifest.ports]
    migrated["parameters"] = []
    return migrated


def _legacy_ica_review_sources(nodes: list[Any], edges: list[Any]) -> dict[str, tuple[str, str]]:
    """Find legacy review nodes with exactly one compatible raw-input edge."""

    result: dict[str, tuple[str, str]] = {}
    for node in nodes:
        if not _is_legacy_ica_review_node(node):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        incoming = [
            edge
            for edge in edges
            if isinstance(edge, dict)
            and isinstance(edge.get("target"), dict)
            and edge["target"].get("node_id") == node_id
            and edge["target"].get("port_id") == "raw"
        ]
        if len(incoming) != 1 or not isinstance(incoming[0].get("source"), dict):
            continue
        source = incoming[0]["source"]
        source_node_id = source.get("node_id")
        source_port_id = source.get("port_id")
        if isinstance(source_node_id, str) and isinstance(source_port_id, str):
            result[node_id] = (source_node_id, source_port_id)
    return result


def _migrate_legacy_filter_node(value: Any) -> Any:
    """Upgrade only the exact Step 5 illustrative filter snapshot."""

    if not isinstance(value, dict) or value.get("type") != "eeg.filter":
        return value
    ports = value.get("ports")
    parameters = value.get("parameters")
    expected_ports = [
        ("raw", "Raw EEG", "input", "raw_eeg"),
        ("filtered", "Filtered EEG", "output", "raw_eeg"),
    ]
    if (
        value.get("label") != "Band-pass Filter"
        or value.get("category") != "Preprocessing"
        or value.get("description") != _LEGACY_FILTER_DESCRIPTION
        or value.get("pauses_for_review", False) is not False
        or not isinstance(ports, list)
        or len(ports) != len(expected_ports)
        or not isinstance(parameters, list)
        or len(parameters) != 2
    ):
        return value
    for port, (port_id, label, direction, data_type) in zip(ports, expected_ports, strict=True):
        if (
            not isinstance(port, dict)
            or set(port) - {"id", "label", "direction", "data_type", "required"}
            or (port.get("id"), port.get("label"), port.get("direction"), port.get("data_type"))
            != (port_id, label, direction, data_type)
            or port.get("required", True) is not True
        ):
            return value
    expected_parameters = [
        ("low_hz", "Low cutoff (Hz)"),
        ("high_hz", "High cutoff (Hz)"),
    ]
    parameter_values: dict[str, Any] = {}
    for parameter, (parameter_id, label) in zip(parameters, expected_parameters, strict=True):
        if (
            not isinstance(parameter, dict)
            or set(parameter) - {"id", "label", "value", "required", "description"}
            or (parameter.get("id"), parameter.get("label")) != (parameter_id, label)
            or parameter.get("required") is not True
            # Pydantic adds ``description: null`` when an older workflow is
            # parsed and saved. Accept that serializer-only field while
            # leaving any researcher-authored description untouched.
            or parameter.get("description") is not None
        ):
            return value
        parameter_values[parameter_id] = parameter.get("value")

    manifest = NODE_REGISTRY_BY_ID.get("eeg.filter")
    if manifest is None:
        return value
    migrated = deepcopy(value)
    migrated["label"] = manifest.label
    migrated["category"] = manifest.category
    migrated["description"] = manifest.description
    migrated["pauses_for_review"] = manifest.review_behavior == "required"
    migrated["ports"] = [port.model_dump(mode="json") for port in manifest.ports]
    migrated["parameters"] = [
        {
            "id": schema.id,
            "label": schema.label,
            "value": parameter_values.get(schema.id, schema.default),
            "required": schema.required,
            "description": schema.description,
        }
        for schema in manifest.parameters
    ]
    return migrated


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
        graph = Workflow.model_validate(migrate_project_workflow_dict(workflow_raw))
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
        # graph must never replace the last-valid recovery copy. Filesystem
        # errors propagate so an unreadable previous graph fails the save
        # instead of silently overwriting the only copy.
        previous_text = workflow_path.read_text(encoding="utf-8")
        try:
            previous_raw = json.loads(previous_text)
            previous_graph = Workflow.model_validate(migrate_project_workflow_dict(previous_raw))
            previous_valid = validate_workflow(previous_graph, NODE_REGISTRY_BY_ID).valid
        except ValueError:
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
