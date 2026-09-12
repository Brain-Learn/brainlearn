"""Background worker lifecycle for demonstration runs.

Work executes in daemon threads outside FastAPI request handlers. Every
persisted mutation goes through :class:`RunStore` (and therefore the Step 4B
serialized writer path): the driver re-reads the record before each step and
saves through the same locks the API uses.

Execution allowlist: only the internal adapters in
:mod:`brainlearn_server.demo_nodes` may run. Unknown node types fail
deterministically with ``unsupported_node_type``; shell commands, module
paths, and arbitrary Python are never accepted from payloads.
"""

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brainlearn_core import (
    NODE_TERMINAL_STATES,
    RUN_TERMINAL_STATES,
    ArtifactRecord,
    Edge,
    EnvironmentRecord,
    FailureRecord,
    NodeInstance,
    NodeRunRecord,
    NodeRunState,
    PortDirection,
    ReviewPauseRecord,
    RunEvent,
    RunEventKind,
    RunRecord,
    RunState,
    Workflow,
    content_identity,
    environment_identity,
    node_content_identity,
    validate_workflow,
)
from brainlearn_core.projects import utc_now_iso
from brainlearn_core.scheduler import downstream_ids, ready_node_ids, topological_order

from brainlearn_server.demo_nodes import (
    DEMO_NODES,
    CancelledByUser,
    ControlledFailure,
    NodeContext,
    ReviewPauseRequested,
)
from brainlearn_server.error_log import log_fault
from brainlearn_server.run_store import RunStore

JOIN_TIMEOUT_SECONDS = 15.0
STAGING_DIRNAME = "staging"
ARTIFACTS_DIRNAME = "artifacts"
QUARANTINE_DIRNAME = "quarantine"


def _owned_child(root: Path, node_id: str, label: str, *names: str) -> Path:
    """Create/find ``root/names...`` while rejecting symlinked components.

    Every existing component must be a real directory; missing trailing
    components are created. Anything else raises ``ControlledFailure`` before
    any file is read, written, moved, or deleted, so worker-owned trees can
    never be redirected through planted symlinks.
    """

    current = root
    for depth, name in enumerate(names):
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ControlledFailure(
                f"Node {node_id!r} has an unsafe {label} path component {name!r}."
            )
        if current.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} {label} escapes through a symlinked directory."
            )
        nxt = current / name
        if nxt.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} {label} component {name!r} is a symlink; refusing it."
            )
        if nxt.exists() and not nxt.is_dir():
            raise ControlledFailure(
                f"Node {node_id!r} {label} component {name!r} is not a directory."
            )
        nxt.mkdir(exist_ok=True)
        if nxt.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} {label} component {name!r} changed underfoot."
            )
        current = nxt
    if current.is_symlink():
        raise ControlledFailure(f"Node {node_id!r} {label} resolves through a symlink.")
    return current


def _remove_tree(path: Path, node_id: str, label: str) -> None:
    """Remove a worker-owned scratch tree; never follow symlinks."""

    import shutil

    if not path.exists() or path.is_symlink():
        return
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError as exc:
        log_fault(
            summary=f"{type(exc).__name__}: {exc}",
            method="WORKER",
            path=f"{label} :: {node_id}",
            exc=exc,
        )


def _canonical_relative(raw: str) -> str | None:
    """Canonical forward-slash form of a project-relative path, or None.

    Rejects empty paths, absolute paths, drive qualifiers, UNC prefixes, and
    empty/dot/parent segments without touching the filesystem.
    """

    import re

    canonical = raw.replace("\\", "/")
    if not canonical or canonical.startswith("/") or re.match(r"^[A-Za-z]:", canonical):
        return None
    segments = canonical.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return None
    return canonical


def _contained_child(root: Path, canonical: str) -> Path | None:
    """Resolve ``canonical`` under ``root``; return None on escape or symlinks.

    Every path component must exist as a non-symlink directory and the final
    target must be reachable without leaving ``root``. Pure validation: reads
    metadata only, never modifies files.
    """

    current = root.resolve(strict=False)
    for segment in canonical.split("/"):
        current = current / segment
        if current.is_symlink():
            return None
    resolved = current.resolve(strict=False)
    if (
        resolved != root.resolve(strict=False)
        and root.resolve(strict=False) not in resolved.parents
    ):
        return None
    return resolved


def _free_quarantine_target(quarantine_root: Path, preferred: str) -> Path | None:
    """First free quarantine destination, never replacing an existing entry.

    ``preferred`` and ``preferred-1`` … are tried in order. A candidate that
    is a symlink (even a broken one) or that already exists is treated as
    occupied so an external sentinel or earlier quarantine is never
    overwritten. Returns None when no free name is found.
    """

    for suffix in range(100):
        name = preferred if suffix == 0 else f"{preferred}-{suffix}"
        candidate = quarantine_root / name
        try:
            if candidate.is_symlink():
                continue
            if candidate.exists():
                continue
            return candidate
        except OSError:
            continue
    return None


def _is_contained(target: Path, run_dir: Path) -> bool:
    """True when ``target`` resolves inside ``run_dir``."""

    try:
        run_resolved = run_dir.resolve(strict=False)
        tgt_resolved = target.resolve(strict=False)
    except OSError:
        return False
    return tgt_resolved == run_resolved or run_resolved in tgt_resolved.parents


def _parent_chain_has_symlink(target: Path, run_dir: Path) -> bool:
    """True when ``target`` or its parents up to ``run_dir`` contain a symlink."""

    try:
        cur: Path = target
        for _ in range(8):
            if cur.is_symlink():
                return True
            if cur == run_dir:
                return False
            if run_dir in cur.parents:
                cur = cur.parent
                continue
            # Outside the run tree: treat as unsafe.
            return True
        return True
    except OSError:
        return True


def _safe_remove_dir(target: Path, run_dir: Path) -> bool:
    """Contained removal of a worker-owned directory; never follows symlinks.

    Returns True when ``target`` is absent afterwards or was already absent.
    Returns False when removal was refused (symlink, escape, or failure) so
    the caller can log and choose a further fallback.
    """

    import shutil

    try:
        if target.is_symlink():
            return False
        if not target.exists():
            return True
        if not target.is_dir():
            return False
        if not _is_contained(target, run_dir):
            return False
        if _parent_chain_has_symlink(target, run_dir):
            return False
        shutil.rmtree(target, ignore_errors=True)
        try:
            target.parent.rmdir()
        except OSError:
            pass
        return not target.exists()
    except OSError:
        return False


class ReviewConflictError(ValueError):
    """A review decision was requested for a node that is not awaiting one."""


def service_environment() -> EnvironmentRecord:
    """Describe the environment workers execute in."""

    import importlib.metadata
    import platform

    def _version(distribution: str) -> str:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    return EnvironmentRecord(
        schema_version="1.0",
        operating_system=platform.system(),
        architecture=platform.machine(),
        python_version=platform.python_version(),
        packages={
            "brainlearn-core": _version("brainlearn-core"),
            "brainlearn-server": _version("brainlearn-server"),
        },
        accelerator="cpu",
    )


def workflow_identity(workflow: Workflow) -> str:
    """Identity over computation-relevant workflow content only.

    Canvas positions, display labels, descriptions, and edge IDs are
    excluded: renaming an edge or rearranging the graph never changes the
    identity. Edges sort by their endpoints, so list insertion order is
    inert while endpoint or topology changes alter the hash.
    """

    nodes: list[dict[str, Any]] = []
    for node in sorted(workflow.nodes, key=lambda item: item.id):
        ports: list[dict[str, str]] = sorted(
            (
                {
                    "id": port.id,
                    "direction": port.direction.value,
                    "data_type": port.data_type.value,
                }
                for port in node.ports
            ),
            key=lambda port: port["id"],
        )
        nodes.append(
            {
                "id": node.id,
                "type": node.type,
                "ports": ports,
                "parameters": {
                    parameter.id: parameter.value
                    for parameter in sorted(node.parameters, key=lambda item: item.id)
                },
            }
        )
    edges: list[dict[str, Any]] = sorted(
        (
            {
                "source": {"node": edge.source.node_id, "port": edge.source.port_id},
                "target": {"node": edge.target.node_id, "port": edge.target.port_id},
            }
            for edge in workflow.edges
        ),
        key=lambda edge: (
            str(edge["source"]["node"]),
            str(edge["source"]["port"]),
            str(edge["target"]["node"]),
            str(edge["target"]["port"]),
        ),
    )
    return content_identity("workflow", {"schema_version": "1.0", "nodes": nodes, "edges": edges})


def next_attempt(record: RunRecord, node_id: str) -> int:
    """Fresh positive attempt from record and structured event history.

    Recovery resets interrupted nodes to attempt 0 while preserving the
    voided number on events, so the next attempt is one past the highest
    number seen in either place. Fresh nodes start at 1.
    """

    node = next(item for item in record.node_runs if item.id == node_id)
    prior = [node.attempt]
    prior.extend(
        event.attempt
        for event in record.events
        if event.node_run_id == node_id and event.attempt is not None
    )
    return max([0, *prior]) + 1


def _validate_node_ports(node: NodeInstance, connected_inputs: set[str] | None = None) -> None:
    """Validate a workflow node's ports against its adapter manifest.

    Unknown node types skip validation here and fail deterministically at
    execution time. Known types must declare only manifest ports with
    matching directions, every required input must be declared and supplied
    by an edge, and every required output must be declared.
    """

    manifest = DEMO_NODES.get(node.type)
    if manifest is None:
        return
    declared_inputs = {port.id for port in node.ports if port.direction == PortDirection.INPUT}
    declared_outputs = {port.id for port in node.ports if port.direction == PortDirection.OUTPUT}
    for port in node.ports:
        if port.direction == PortDirection.INPUT and port.id not in manifest.inputs:
            raise ValueError(
                f"Node {node.id!r} declares unknown input port {port.id!r} "
                f"for adapter {node.type!r}."
            )
        if port.direction == PortDirection.OUTPUT and port.id not in manifest.outputs:
            raise ValueError(
                f"Node {node.id!r} declares unknown output port {port.id!r} "
                f"for adapter {node.type!r}."
            )
    for port_id, required in manifest.inputs.items():
        if required and port_id not in declared_inputs:
            raise ValueError(
                f"Node {node.id!r} is missing required input port {port_id!r} "
                f"for adapter {node.type!r}."
            )
    for port_id, required in manifest.outputs.items():
        if required and port_id not in declared_outputs:
            raise ValueError(
                f"Node {node.id!r} is missing required output port {port_id!r} "
                f"for adapter {node.type!r}."
            )
    if connected_inputs is not None:
        for port_id, required in manifest.inputs.items():
            if required and port_id not in connected_inputs:
                raise ValueError(
                    f"Node {node.id!r} requires input port {port_id!r} "
                    f"for adapter {node.type!r} but no edge supplies it. "
                    "Connect an upstream output before running."
                )


def build_run_record(workflow: Workflow, seed: int = 0, created_at: str | None = None) -> RunRecord:
    """Create a queued run record from a semantically valid workflow."""

    validation = validate_workflow(workflow)
    if not validation.valid:
        details = "; ".join(issue.message for issue in validation.issues)
        raise ValueError(f"Cannot start an invalid workflow: {details}")
    environment = service_environment()
    env_identity = environment_identity(environment.model_dump(mode="json"))
    dependencies = {
        node.id: sorted(
            {edge.source.node_id for edge in workflow.edges if edge.target.node_id == node.id}
        )
        for node in workflow.nodes
    }
    order = topological_order(dependencies)
    by_id = {node.id: node for node in workflow.nodes}
    edges_by_target: dict[str, list[Edge]] = {}
    for edge in sorted(workflow.edges, key=lambda item: item.id):
        edges_by_target.setdefault(edge.target.node_id, []).append(edge)
    contents: dict[str, str] = {}
    node_runs: list[NodeRunRecord] = []
    timestamp = created_at or utc_now_iso()
    for node_id in order:
        node = by_id[node_id]
        connected = {edge.target.port_id for edge in edges_by_target.get(node_id, [])}
        _validate_node_ports(node, connected)
        inputs: dict[str, str] = {}
        sources: dict[str, dict[str, str]] = {}
        for edge in edges_by_target.get(node_id, []):
            if edge.target.port_id not in inputs:
                inputs[edge.target.port_id] = contents[edge.source.node_id]
            sources.setdefault(
                edge.target.port_id,
                {"node": edge.source.node_id, "port": edge.source.port_id},
            )
        manifest = DEMO_NODES.get(node.type)
        version = manifest.version if manifest is not None else "0.0.0"
        parameters = {parameter.id: parameter.value for parameter in node.parameters}
        declared_outputs = sorted(
            port.id for port in node.ports if port.direction == PortDirection.OUTPUT
        )
        settings: dict[str, Any] = {
            "input_sources": sources,
            "declared_outputs": declared_outputs,
        }
        identity = node_content_identity(
            node_type=node.type,
            node_version=version,
            inputs=inputs,
            parameters=parameters,
            environment_identity=env_identity,
            seed=seed,
            settings=settings,
        )
        contents[node_id] = identity
        node_runs.append(
            NodeRunRecord(
                schema_version="1.0",
                id=node_id,
                node_id=node_id,
                node_type=node.type,
                node_version=version,
                dependencies=dependencies[node_id],
                attempt=0,
                state=NodeRunState.QUEUED,
                started_at=None,
                finished_at=None,
                inputs=inputs,
                parameters=parameters,
                environment_identity=env_identity,
                seed=seed,
                settings=settings,
                content_identity=identity,
                artifacts=[],
                failure=None,
                review_pause=None,
            )
        )
    return RunRecord(
        schema_version="1.0",
        id=f"run-{uuid.uuid4().hex[:12]}",
        workflow_id=workflow.id,
        workflow_schema_version="1.0",
        workflow_identity=workflow_identity(workflow),
        state=RunState.QUEUED,
        created_at=timestamp,
        started_at=None,
        finished_at=None,
        environment=environment,
        seed=seed,
        node_runs=node_runs,
        events=[
            RunEvent(
                schema_version="1.0",
                seq=0,
                at=timestamp,
                kind=RunEventKind.RUN_QUEUED,
                node_run_id=None,
                message="Run created and queued.",
            )
        ],
        failure=None,
    )


@dataclass
class WorkerService:
    runs: RunStore
    _threads: dict[tuple[str, str], threading.Thread] = field(default_factory=dict)
    _cancel: dict[tuple[str, str], threading.Event] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    # -- public controls -------------------------------------------------
    def start_existing(self, project: str, run_id: str) -> RunRecord:
        """Resume or begin driving a persisted nonterminal run."""

        record = self.runs.get_run(project, run_id)
        if record.state in RUN_TERMINAL_STATES:
            raise ValueError(f"Run {run_id!r} is already {record.state.value}.")
        with self._guard:
            thread = self._threads.get((project, run_id))
            if thread is not None and thread.is_alive():
                return record
            worker = threading.Thread(target=self._drive, args=(project, run_id), daemon=True)
            self._threads[(project, run_id)] = worker
            self._cancel.setdefault((project, run_id), threading.Event())
            worker.start()
            return record

    def recover_project(self, project: str, now: str | None = None) -> list[RunRecord]:
        """Recover interrupted runs: quarantine staging, reconcile, resume.

        Leftover attempt staging trees move to ``quarantine/`` first so no
        stale file can be promoted later. Records reconcile through the Step
        4B recovery path (preserving structured voided attempts); every
        reconciled run with schedulable queued work resumes driving, while
        runs parked on valid pending reviews stay parked. Returns the
        reconciled runs.
        """

        from brainlearn_core.scheduler import ready_node_ids

        timestamp = now or utc_now_iso()
        recovered: list[RunRecord] = []
        for record in self.runs.find_nonterminal_runs(project):
            with self._guard:
                thread = self._threads.get((project, record.id))
                if thread is not None and thread.is_alive():
                    continue
            self._quarantine_leftover_staging(project, record.id)
            repaired = self.runs.recover_run(project, record.id, now=timestamp)
            self._quarantine_orphaned_attempts(project, record.id)
            if repaired is not None:
                recovered.append(repaired)
        resumed: list[RunRecord] = []
        for record in recovered:
            fresh = self.runs.get_run(project, record.id)
            if any(node.state == NodeRunState.WAITING_FOR_REVIEW for node in fresh.node_runs):
                continue
            states = {node.id: node.state for node in fresh.node_runs}
            dependencies = {node.id: list(node.dependencies) for node in fresh.node_runs}
            if ready_node_ids(states, dependencies):
                self._ensure_driver(project, record.id)
            resumed.append(self.runs.get_run(project, record.id))
        return resumed

    def _quarantine_leftover_staging(self, project: str, run_id: str) -> None:
        """Move leftover attempt staging trees to quarantine before reconcile.

        Quarantine destinations use the first free ``*-interrupted`` name so a
        planted symlink or earlier quarantine is never overwritten. When no
        free destination exists or the move fails, the worker-owned staging
        tree is removed by a contained non-symlink operation so stale files
        can never be promoted later.
        """

        import shutil

        try:
            run_dir = self._run_dir(project, run_id)
            quarantine_root = _owned_child(run_dir, run_id, "quarantine", QUARANTINE_DIRNAME)
        except ControlledFailure as exc:
            log_fault(
                summary=f"ControlledFailure: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}",
            )
            try:
                run_dir = self._run_dir(project, run_id)
            except ControlledFailure:
                return
            staging_fallback = run_dir / STAGING_DIRNAME
            if staging_fallback.is_symlink() or not staging_fallback.is_dir():
                return
            for child in sorted(staging_fallback.iterdir()):
                if child.is_symlink() or not child.is_dir():
                    continue
                if not _safe_remove_dir(child, run_dir):
                    log_fault(
                        summary=(f"OSError: could not quarantine or remove staging {child}"),
                        method="WORKER",
                        path=f"{project} :: {run_id}",
                    )
            return
        staging = self._run_dir(project, run_id) / STAGING_DIRNAME
        if not staging.is_dir() or staging.is_symlink():
            return
        for child in sorted(staging.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            run_dir = self._run_dir(project, run_id)
            if not _is_contained(child, run_dir) or _parent_chain_has_symlink(child, run_dir):
                log_fault(
                    summary=(f"OSError: refusing unsafe staging source {child}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}",
                )
                continue
            target = _free_quarantine_target(quarantine_root, f"{child.name}-interrupted")
            if target is None:
                if not _safe_remove_dir(child, run_dir):
                    log_fault(
                        summary=(f"OSError: no free quarantine name for staging {child}"),
                        method="WORKER",
                        path=f"{project} :: {run_id}",
                    )
                continue
            try:
                shutil.move(str(child), str(target))
            except OSError as exc:
                log_fault(
                    summary=f"{type(exc).__name__}: {exc}",
                    method="WORKER",
                    path=f"{project} :: {run_id}",
                    exc=exc,
                )
                if not _safe_remove_dir(child, run_dir):
                    log_fault(
                        summary=(f"OSError: could not quarantine or remove staging {child}"),
                        method="WORKER",
                        path=f"{project} :: {run_id}",
                    )

    def _quarantine_orphaned_attempts(self, project: str, run_id: str) -> None:
        """Move final attempt directories no successful record references.

        A crash between atomic promotion and record persistence leaves an
        unreferenced attempt under ``artifacts/``. Recovery moves such
        directories to the first free ``*-orphaned`` quarantine name so a
        planted symlink or earlier quarantine is never overwritten. When
        quarantine cannot complete, the unreferenced worker-owned attempt is
        removed by a contained non-symlink operation so no unreferenced final
        attempt remains. Directories referenced by successful artifact
        records, and any symlink, are left untouched.
        """

        import shutil

        record = self.runs.get_run(project, run_id)
        referenced: set[str] = set()
        for node in record.node_runs:
            for artifact in node.artifacts:
                parts = artifact.path.split("/")
                if (
                    len(parts) >= 5
                    and parts[0] == "runs"
                    and parts[1] == run_id
                    and parts[2] == ARTIFACTS_DIRNAME
                    and parts[3] == node.id
                ):
                    referenced.add(f"{parts[3]}/{parts[4]}")
        try:
            run_dir = self._run_dir(project, run_id)
            artifacts_root = run_dir / ARTIFACTS_DIRNAME
            quarantine_root = _owned_child(run_dir, run_id, "quarantine", QUARANTINE_DIRNAME)
        except ControlledFailure as exc:
            log_fault(
                summary=f"ControlledFailure: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}",
            )
            try:
                run_dir = self._run_dir(project, run_id)
                artifacts_root = run_dir / ARTIFACTS_DIRNAME
            except ControlledFailure:
                return
            if artifacts_root.is_symlink() or not artifacts_root.is_dir():
                return
            for node_dir in sorted(artifacts_root.iterdir()):
                if node_dir.is_symlink() or not node_dir.is_dir():
                    continue
                for attempt_dir in sorted(node_dir.iterdir()):
                    if attempt_dir.is_symlink() or not attempt_dir.is_dir():
                        continue
                    if f"{node_dir.name}/{attempt_dir.name}" in referenced:
                        continue
                    if not _safe_remove_dir(attempt_dir, run_dir):
                        log_fault(
                            summary=(
                                f"OSError: could not quarantine or remove orphan {attempt_dir}"
                            ),
                            method="WORKER",
                            path=f"{project} :: {run_id}",
                        )
            return
        if not artifacts_root.is_dir() or artifacts_root.is_symlink():
            return
        for node_dir in sorted(artifacts_root.iterdir()):
            if node_dir.is_symlink() or not node_dir.is_dir():
                continue
            for attempt_dir in sorted(node_dir.iterdir()):
                if attempt_dir.is_symlink() or not attempt_dir.is_dir():
                    continue
                if f"{node_dir.name}/{attempt_dir.name}" in referenced:
                    continue
                run_dir = self._run_dir(project, run_id)
                if not _is_contained(attempt_dir, run_dir) or _parent_chain_has_symlink(
                    attempt_dir, run_dir
                ):
                    log_fault(
                        summary=(f"OSError: refusing unsafe orphan source {attempt_dir}"),
                        method="WORKER",
                        path=f"{project} :: {run_id}",
                    )
                    continue
                target = _free_quarantine_target(
                    quarantine_root, f"{node_dir.name}-{attempt_dir.name}-orphaned"
                )
                if target is None:
                    if not _safe_remove_dir(attempt_dir, run_dir):
                        log_fault(
                            summary=(f"OSError: no free quarantine name for {attempt_dir}"),
                            method="WORKER",
                            path=f"{project} :: {run_id}",
                        )
                    continue
                try:
                    shutil.move(str(attempt_dir), str(target))
                    try:
                        node_dir.rmdir()
                    except OSError:
                        pass
                except OSError as exc:
                    log_fault(
                        summary=f"{type(exc).__name__}: {exc}",
                        method="WORKER",
                        path=f"{project} :: {run_id}",
                        exc=exc,
                    )
                    if not _safe_remove_dir(attempt_dir, run_dir):
                        log_fault(
                            summary=(
                                f"OSError: could not quarantine or remove orphan {attempt_dir}"
                            ),
                            method="WORKER",
                            path=f"{project} :: {run_id}",
                        )

    def cancel_run(
        self, project: str, run_id: str, timeout: float = JOIN_TIMEOUT_SECONDS
    ) -> RunRecord:
        """Cancel queued and running work; idempotent once terminal."""

        record = self.runs.get_run(project, run_id)
        if record.state in RUN_TERMINAL_STATES:
            return record
        with self._guard:
            flag = self._cancel.setdefault((project, run_id), threading.Event())
            flag.set()
            thread = self._threads.get((project, run_id))
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            return self.runs.get_run(project, run_id)
        repaired = _cancel_record(record, utc_now_iso())
        return self.runs.save_run(project, repaired)

    def review_node(
        self,
        project: str,
        run_id: str,
        node_run_id: str,
        decision: str,
        note: str = "",
    ) -> RunRecord:
        """Apply an approval or rejection to a waiting node and resume driving."""

        record = self.runs.get_run(project, run_id)
        node = next((item for item in record.node_runs if item.id == node_run_id), None)
        if (
            node is None
            or node.state != NodeRunState.WAITING_FOR_REVIEW
            or node.review_pause is None
            or node.review_pause.decided_at is not None
        ):
            raise ReviewConflictError(
                f"Node run {node_run_id!r} is not awaiting review in run {run_id!r}."
            )
        timestamp = utc_now_iso()
        if decision == "approved":
            decided = node.review_pause.model_copy(
                update={"decided_at": timestamp, "decision": "approved", "note": note}
            )
            nodes = [
                item.model_copy(
                    update={
                        "state": NodeRunState.SUCCEEDED,
                        "finished_at": timestamp,
                        "review_pause": decided,
                    }
                )
                if item.id == node_run_id
                else item
                for item in record.node_runs
            ]
            events = [
                *record.events,
                RunEvent(
                    schema_version="1.0",
                    seq=len(record.events),
                    at=timestamp,
                    kind=RunEventKind.REVIEW_DECIDED,
                    node_run_id=node_run_id,
                    attempt=node.attempt,
                    message=note or "Review approved.",
                ),
            ]
            repaired = record.model_copy(
                update={"state": RunState.RUNNING, "node_runs": nodes, "events": events}
            )
            saved = self.runs.save_run(
                project, RunRecord.model_validate(repaired.model_dump(mode="json"))
            )
            self._ensure_driver(project, run_id)
            return saved
        decided = node.review_pause.model_copy(
            update={"decided_at": timestamp, "decision": "rejected", "note": note}
        )
        failure = FailureRecord(
            schema_version="1.0",
            code="review_rejected",
            message=note or "Reviewer rejected the paused output.",
            node_run_id=node_run_id,
            at=timestamp,
        )
        return self.runs.save_run(
            project, _fail_run(record, node_run_id, failure, timestamp, decided)
        )

    # -- driver ----------------------------------------------------------
    def _ensure_driver(self, project: str, run_id: str) -> None:
        with self._guard:
            thread = self._threads.get((project, run_id))
            if thread is not None and thread.is_alive():
                return
            worker = threading.Thread(target=self._drive, args=(project, run_id), daemon=True)
            self._threads[(project, run_id)] = worker
            self._cancel.setdefault((project, run_id), threading.Event())
            worker.start()

    def _cancel_flag(self, project: str, run_id: str) -> threading.Event:
        with self._guard:
            return self._cancel.setdefault((project, run_id), threading.Event())

    def _drive(self, project: str, run_id: str) -> None:
        key = (project, run_id)
        try:
            while True:
                try:
                    record = self.runs.get_run(project, run_id)
                except FileNotFoundError:
                    return
                if self._cancel_flag(project, run_id).is_set():
                    self.runs.save_run(project, _cancel_record(record, utc_now_iso()))
                    return
                if record.state in RUN_TERMINAL_STATES:
                    return
                dependencies = {n.id: list(n.dependencies) for n in record.node_runs}
                states = {n.id: n.state for n in record.node_runs}
                ready = ready_node_ids(states, dependencies)
                waiting = [
                    n.id for n in record.node_runs if n.state == NodeRunState.WAITING_FOR_REVIEW
                ]
                if not ready:
                    self._settle(project, record, bool(waiting))
                    return
                if waiting and record.state != RunState.WAITING_FOR_REVIEW:
                    parked = record.model_copy(update={"state": RunState.WAITING_FOR_REVIEW})
                    self.runs.save_run(
                        project, RunRecord.model_validate(parked.model_dump(mode="json"))
                    )
                self._execute_node(project, record, sorted(ready)[0])
        except BaseException as exc:
            log_fault(
                summary=f"{type(exc).__name__}: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}",
                exc=exc,
            )
        finally:
            with self._guard:
                if self._threads.get(key) is threading.current_thread():
                    self._threads.pop(key, None)

    def _settle(self, project: str, record: RunRecord, has_waiting: bool) -> None:
        """Park a waiting run or finish/stalemate one with nothing ready."""

        if has_waiting:
            if record.state == RunState.WAITING_FOR_REVIEW:
                return
            parked = record.model_copy(update={"state": RunState.WAITING_FOR_REVIEW})
            self.runs.save_run(project, RunRecord.model_validate(parked.model_dump(mode="json")))
            return
        if all(
            node.state in {NodeRunState.SUCCEEDED, NodeRunState.CACHE_REUSED}
            for node in record.node_runs
        ):
            timestamp = utc_now_iso()
            finished = RunRecord.model_validate(
                record.model_copy(
                    update={"state": RunState.SUCCEEDED, "finished_at": timestamp}
                ).model_dump(mode="json")
            )
            events = [
                *finished.events,
                RunEvent(
                    schema_version="1.0",
                    seq=len(finished.events),
                    at=timestamp,
                    kind=RunEventKind.RUN_SUCCEEDED,
                    node_run_id=None,
                    message="All nodes completed.",
                ),
            ]
            self.runs.save_run(
                project,
                RunRecord.model_validate(
                    finished.model_copy(update={"events": events}).model_dump(mode="json")
                ),
            )
            return
        timestamp = utc_now_iso()
        failure = FailureRecord(
            schema_version="1.0",
            code="scheduler_stall",
            message="No queued node is ready and no review is pending; the run cannot proceed.",
            node_run_id=None,
            at=timestamp,
        )
        stalled = record.model_copy(update={"state": RunState.FAILED, "failure": failure})
        if stalled.finished_at is None:
            stalled = stalled.model_copy(update={"finished_at": timestamp})
        repaired = RunRecord.model_validate(stalled.model_dump(mode="json"))
        events = [
            *repaired.events,
            RunEvent(
                schema_version="1.0",
                seq=len(repaired.events),
                at=timestamp,
                kind=RunEventKind.RUN_FAILED,
                node_run_id=None,
                message=failure.message,
            ),
        ]
        self.runs.save_run(
            project,
            RunRecord.model_validate(
                repaired.model_copy(update={"events": events}).model_dump(mode="json")
            ),
        )

    def _execute_node(self, project: str, record: RunRecord, node_id: str) -> None:
        fresh = self.runs.get_run(project, record.id)
        node = next(item for item in fresh.node_runs if item.id == node_id)
        if node.state != NodeRunState.QUEUED or self._cancel_flag(project, record.id).is_set():
            return
        timestamp = utc_now_iso()
        attempt = next_attempt(fresh, node_id)
        started = node.model_copy(
            update={"state": NodeRunState.RUNNING, "started_at": timestamp, "attempt": attempt}
        )
        nodes = [started if item.id == node_id else item for item in fresh.node_runs]
        events = [
            *fresh.events,
            RunEvent(
                schema_version="1.0",
                seq=len(fresh.events),
                at=timestamp,
                kind=RunEventKind.NODE_STARTED,
                node_run_id=node_id,
                attempt=attempt,
                message=f"Attempt {attempt} started.",
            ),
        ]
        if fresh.state == RunState.QUEUED:
            started_run: RunRecord = fresh.model_copy(
                update={"state": RunState.RUNNING, "started_at": timestamp}
            )
            run_started = RunEvent(
                schema_version="1.0",
                seq=len(events),
                at=timestamp,
                kind=RunEventKind.RUN_STARTED,
                node_run_id=None,
                message="Run started.",
            )
            events.append(run_started)
        else:
            started_run = fresh
        self.runs.save_run(
            project,
            RunRecord.model_validate(
                started_run.model_copy(update={"node_runs": nodes, "events": events}).model_dump(
                    mode="json"
                )
            ),
        )
        staging: Path | None = None
        ctx_inputs: dict[str, Path] = {}
        manifest = DEMO_NODES.get(node.node_type)
        try:
            run_dir = self._run_dir(project, fresh.id)
            # Reserve worker-owned staging before the adapter runs: adapters must
            # never precreate these paths, and symlinked components are refused.
            staging = _owned_child(
                run_dir, node_id, "staging", STAGING_DIRNAME, node_id, f"attempt-{attempt}"
            )
            ctx_inputs = self._resolve_inputs(project, fresh, node)
            if self._cancel_flag(project, fresh.id).is_set():
                raise CancelledByUser(f"Node {node_id!r} cancelled before starting.")
            if manifest is None:
                raise ControlledFailure(
                    f"Node type {node.node_type!r} has no demonstration adapter."
                )
            staged = manifest.handler(
                NodeContext(
                    node_id=node_id,
                    node_type=node.node_type,
                    parameters=dict(node.parameters),
                    inputs=ctx_inputs,
                    staging_dir=staging,
                    cancel=self._cancel_flag(project, fresh.id),
                )
            )
            if self._cancel_flag(project, fresh.id).is_set():
                raise CancelledByUser(f"Node {node_id!r} cancelled during execution.")
            artifacts = self._promote(project, fresh.id, node, staging, staged)
            if self._cancel_flag(project, fresh.id).is_set():
                # Cancellation won after promotion: remove the unreferenced
                # attempt directory (no record points to it yet) and cancel.
                self._remove_promoted(project, fresh.id, node_id, attempt)
                self._quarantine(staging, project, fresh.id, node_id, attempt)
                raise CancelledByUser(f"Node {node_id!r} cancelled during execution.")
            self._finish_node(project, fresh.id, node_id, attempt, artifacts)
        except ReviewPauseRequested as exc:
            self._pause_for_review(project, fresh.id, node_id, attempt, str(exc))
        except CancelledByUser:
            if staging is not None:
                self._quarantine(staging, project, fresh.id, node_id, attempt)
            current = self.runs.get_run(project, fresh.id)
            self.runs.save_run(project, _cancel_record(current, utc_now_iso()))
        except ControlledFailure as exc:
            if staging is not None:
                self._quarantine(staging, project, fresh.id, node_id, attempt)
            self._fail_node(
                project,
                fresh.id,
                node_id,
                attempt,
                FailureRecord(
                    schema_version="1.0",
                    code="node_failed" if node.node_type in DEMO_NODES else "unsupported_node_type",
                    message=str(exc),
                    node_run_id=node_id,
                    at=utc_now_iso(),
                ),
            )
        except BaseException as exc:
            log_fault(
                summary=f"{type(exc).__name__}: {exc}",
                method="WORKER",
                path=f"{project} :: {fresh.id}/{node_id}",
                exc=exc,
            )
            # A crash after promotion but before record persistence must not
            # leave an unreferenced attempt under final artifacts.
            self._quarantine_promoted(project, fresh.id, node_id, attempt)
            if staging is not None:
                self._quarantine(staging, project, fresh.id, node_id, attempt)
            self._fail_node(
                project,
                fresh.id,
                node_id,
                attempt,
                FailureRecord(
                    schema_version="1.0",
                    code="adapter_crash",
                    message=f"Adapter raised {type(exc).__name__}: {exc}",
                    node_run_id=node_id,
                    at=utc_now_iso(),
                ),
            )

    # -- node transitions --------------------------------------------------
    def _run_dir(self, project: str, run_id: str) -> Path:
        from brainlearn_server.project_store import canonicalize_project_path

        root = self.runs.projects.require_allowed(canonicalize_project_path(project))
        if root.is_symlink():
            raise ControlledFailure(f"Project root {str(root)!r} is a symlink; refusing it.")
        runs_dir = root / "runs"
        if runs_dir.is_symlink() or (runs_dir.exists() and not runs_dir.is_dir()):
            raise ControlledFailure(
                f"Run directory {str(runs_dir)!r} is not a real directory; refusing it."
            )
        run_dir = runs_dir / run_id
        if run_dir.is_symlink():
            raise ControlledFailure(
                f"Run directory {str(run_dir)!r} is a symlink; refusing to follow it."
            )
        return run_dir

    def _resolve_inputs(
        self, project: str, record: RunRecord, node: NodeRunRecord
    ) -> dict[str, Path]:
        sources = node.settings.get("input_sources", {})
        if not isinstance(sources, dict):
            return {}
        by_content = {item.content_identity: item for item in record.node_runs}
        resolved: dict[str, Path] = {}
        project_root = self._run_dir(project, record.id).parent.parent
        for target_port, source in sources.items():
            if not isinstance(source, dict):
                continue
            upstream = by_content.get(node.inputs.get(target_port, ""), None)
            if upstream is None:
                continue
            for artifact in upstream.artifacts:
                if artifact.port_id == source.get("port"):
                    candidate = _contained_child(project_root, artifact.path)
                    if candidate is not None and candidate.is_file():
                        resolved[target_port] = candidate
        return resolved

    def _promote(
        self,
        project: str,
        run_id: str,
        node: NodeRunRecord,
        staging: Path,
        staged: list[Any],
    ) -> list[ArtifactRecord]:
        """Preflight every output, then commit the attempt atomically.

        Nothing is read or moved until all outputs validate: canonical
        relative paths inside the staging root, ports declared both by the
        workflow node and the adapter manifest, unique ports and
        destinations, existing non-symlink sources, every required manifest
        output emitted, and a free attempt-scoped destination. Validated files
        are staged into a ``complete/`` directory that is renamed into place
        with one atomic ``os.replace``, so a later failure can never leave a
        partial attempt behind. Returns validated records; raises
        ``ControlledFailure`` describing the first problem found.
        """

        import hashlib
        import os
        import shutil

        node_id = node.id
        run_dir = self._run_dir(project, run_id)
        manifest = DEMO_NODES.get(node.node_type)
        manifest_outputs: dict[str, bool] = manifest.outputs if manifest is not None else {}
        raw_declared = node.settings.get("declared_outputs", [])
        if not isinstance(raw_declared, list) or not all(
            isinstance(item, str) for item in raw_declared
        ):
            raise ControlledFailure(f"Node {node_id!r} has no declared workflow output ports.")
        declared_set = set(raw_declared)
        seen_ports: set[str] = set()
        seen_destinations: set[str] = set()
        planned: list[tuple[Path, str, Any]] = []
        for output in staged:
            port = output.port_id
            if not port:
                raise ControlledFailure(f"Node {node_id!r} emitted an unnamed output port.")
            if port not in declared_set:
                raise ControlledFailure(
                    f"Node {node_id!r} emitted undeclared output port {port!r} "
                    "not declared by its workflow node."
                )
            if port not in manifest_outputs:
                raise ControlledFailure(
                    f"Node {node_id!r} emitted undeclared output port {port!r} "
                    "outside its adapter manifest."
                )
            if port in seen_ports:
                raise ControlledFailure(f"Node {node_id!r} emitted duplicate output port {port!r}.")
            seen_ports.add(port)
            canonical = _canonical_relative(output.relative_path)
            if canonical is None:
                raise ControlledFailure(
                    f"Node {node_id!r} emitted an unsafe output path {output.relative_path!r}."
                )
            if canonical in seen_destinations:
                raise ControlledFailure(
                    f"Node {node_id!r} emitted duplicate output path {canonical!r}."
                )
            if canonical.split("/")[0] == "complete":
                raise ControlledFailure(
                    f"Node {node_id!r} emitted reserved output path {canonical!r}."
                )
            seen_destinations.add(canonical)
            source = _contained_child(staging, canonical)
            if source is None or not source.is_file() or source.is_symlink():
                raise ControlledFailure(f"Node {node_id!r} is missing staged output {canonical!r}.")
            planned.append((source, canonical, output))
        if manifest is not None:
            for port_id, required in manifest.outputs.items():
                if required and port_id not in seen_ports:
                    raise ControlledFailure(
                        f"Node {node_id!r} omitted required output port {port_id!r} "
                        f"for adapter {node.node_type!r}."
                    )
        attempt_dir = staging.name
        if not planned:
            _remove_tree(staging.parent, node_id, "staging")
            return []
        artifacts_root = _owned_child(run_dir, node_id, "artifacts", ARTIFACTS_DIRNAME)
        node_dest = artifacts_root / node_id
        if node_dest.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} artifact directory is a symlink; refusing it."
            )
        if node_dest.exists() and not node_dest.is_dir():
            raise ControlledFailure(
                f"Node {node_id!r} artifact path is not a directory; refusing it."
            )
        destination_root = node_dest / attempt_dir
        if destination_root.exists() or destination_root.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} already has a promoted attempt directory; "
                "prior successful history is never overwritten."
            )
        if staging.is_symlink() or not staging.is_dir():
            raise ControlledFailure(
                f"Node {node_id!r} staging directory is not a real directory; refusing it."
            )
        complete_dir = staging / "complete"
        if complete_dir.exists() or complete_dir.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} must not precreate the assembly directory; refusing it."
            )
        complete_dir.mkdir(parents=True, exist_ok=True)
        if complete_dir.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} assembly directory changed underfoot; refusing it."
            )
        records: list[ArtifactRecord] = []
        for source, canonical, output in planned:
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            (complete_dir / canonical).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, complete_dir / canonical)
            records.append(
                ArtifactRecord(
                    schema_version="1.0",
                    artifact_id=content_identity(
                        "artifact",
                        {
                            "schema_version": "1.0",
                            "run_id": run_id,
                            "node": node_id,
                            "port": output.port_id,
                            "sha256": digest,
                        },
                    ),
                    path=(f"runs/{run_id}/{ARTIFACTS_DIRNAME}/{node_id}/{attempt_dir}/{canonical}"),
                    media_type=output.media_type,
                    byte_size=len(data),
                    sha256=digest,
                    produced_by_node=node_id,
                    port_id=output.port_id,
                )
            )
        destination_root.parent.mkdir(parents=True, exist_ok=True)
        if destination_root.parent.is_symlink():
            raise ControlledFailure(
                f"Node {node_id!r} artifact parent changed underfoot; refusing it."
            )
        os.replace(complete_dir, destination_root)
        _remove_tree(staging.parent, node_id, "staging")
        return records

    def _remove_promoted(self, project: str, run_id: str, node_id: str, attempt: int) -> None:
        """Remove an attempt directory promoted just before cancellation won.

        Contained removal only: refuses symlinks, escapes, and unsafe parent
        chains. An emptied node directory is removed too so no unreferenced
        directory remains in the final artifact tree.
        """

        try:
            run_dir = self._run_dir(project, run_id)
        except ControlledFailure as exc:
            log_fault(
                summary=f"ControlledFailure: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        target = run_dir / ARTIFACTS_DIRNAME / node_id / f"attempt-{attempt}"
        if target.is_symlink():
            log_fault(
                summary=f"OSError: Refusing to remove symlinked attempt directory {target}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        if not _safe_remove_dir(target, run_dir):
            log_fault(
                summary=f"OSError: Refusing unsafe removal of promoted attempt {target}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )

    def _quarantine_promoted(self, project: str, run_id: str, node_id: str, attempt: int) -> None:
        """Quarantine a promoted attempt left unreferenced by a later failure.

        Uses the first free ``*-uncommitted`` quarantine name so a planted
        symlink or earlier quarantine is never overwritten. When quarantine
        cannot complete, the unreferenced worker-owned attempt is removed by
        a contained non-symlink operation before the caller persists terminal
        failure, so a terminal failed run never keeps an unreferenced
        artifact. External symlink targets are never modified.
        """

        import shutil

        try:
            run_dir = self._run_dir(project, run_id)
        except ControlledFailure as exc:
            log_fault(
                summary=f"ControlledFailure: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        source = run_dir / ARTIFACTS_DIRNAME / node_id / f"attempt-{attempt}"
        if source.is_symlink():
            log_fault(
                summary=f"OSError: Refusing to quarantine symlinked attempt {source}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        if not source.exists():
            return
        if not _is_contained(source, run_dir) or _parent_chain_has_symlink(source, run_dir):
            log_fault(
                summary=f"OSError: Refusing unsafe promoted source {source}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        try:
            quarantine_root = _owned_child(run_dir, node_id, "quarantine", QUARANTINE_DIRNAME)
        except ControlledFailure as exc:
            log_fault(
                summary=f"ControlledFailure: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            if not _safe_remove_dir(source, run_dir):
                log_fault(
                    summary=(f"OSError: could not quarantine or remove {source}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}/{node_id}",
                )
            return
        target = _free_quarantine_target(
            quarantine_root, f"{node_id}-attempt-{attempt}-uncommitted"
        )
        if target is None:
            log_fault(
                summary=(f"OSError: no free quarantine name for {source}"),
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            if not _safe_remove_dir(source, run_dir):
                log_fault(
                    summary=(f"OSError: could not quarantine or remove {source}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}/{node_id}",
                )
            return
        try:
            shutil.move(str(source), str(target))
            try:
                source.parent.rmdir()
            except OSError:
                pass
        except (OSError, ControlledFailure) as exc:
            log_fault(
                summary=f"{type(exc).__name__}: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
                exc=exc if isinstance(exc, OSError) else None,
            )
            if not _safe_remove_dir(source, run_dir):
                log_fault(
                    summary=(f"OSError: could not quarantine or remove {source}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}/{node_id}",
                )

    def _quarantine(
        self, staging: Path, project: str, run_id: str, node_id: str, attempt: int
    ) -> None:
        """Quarantine worker-owned staging, falling back to safe removal."""

        import shutil

        try:
            run_dir = self._run_dir(project, run_id)
        except ControlledFailure as exc:
            log_fault(
                summary=f"ControlledFailure: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        node_staging = staging.parent
        if node_staging.is_symlink():
            log_fault(
                summary=f"OSError: Refusing to quarantine symlinked staging {node_staging}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        if not node_staging.exists():
            return
        if not _is_contained(node_staging, run_dir) or _parent_chain_has_symlink(
            node_staging, run_dir
        ):
            log_fault(
                summary=f"OSError: Refusing unsafe staging source {node_staging}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            return
        try:
            quarantine_root = _owned_child(run_dir, node_id, "quarantine", QUARANTINE_DIRNAME)
        except (OSError, ControlledFailure) as exc:
            log_fault(
                summary=f"{type(exc).__name__}: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
                exc=exc if isinstance(exc, OSError) else None,
            )
            if not _safe_remove_dir(node_staging, run_dir):
                log_fault(
                    summary=(f"OSError: could not quarantine or remove {node_staging}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}/{node_id}",
                )
            return
        target = _free_quarantine_target(quarantine_root, f"{node_id}-attempt-{attempt}")
        if target is None:
            log_fault(
                summary=(f"OSError: no free quarantine name for {node_staging}"),
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
            )
            if not _safe_remove_dir(node_staging, run_dir):
                log_fault(
                    summary=(f"OSError: could not quarantine or remove {node_staging}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}/{node_id}",
                )
            return
        try:
            shutil.move(str(node_staging), str(target))
        except (OSError, ControlledFailure) as exc:
            log_fault(
                summary=f"{type(exc).__name__}: {exc}",
                method="WORKER",
                path=f"{project} :: {run_id}/{node_id}",
                exc=exc if isinstance(exc, OSError) else None,
            )
            if not _safe_remove_dir(node_staging, run_dir):
                log_fault(
                    summary=(f"OSError: could not quarantine or remove {node_staging}"),
                    method="WORKER",
                    path=f"{project} :: {run_id}/{node_id}",
                )

    def _finish_node(
        self,
        project: str,
        run_id: str,
        node_id: str,
        attempt: int,
        artifacts: list[ArtifactRecord],
    ) -> None:
        timestamp = utc_now_iso()
        record = self.runs.get_run(project, run_id)
        nodes = [
            item.model_copy(
                update={
                    "state": NodeRunState.SUCCEEDED,
                    "finished_at": timestamp,
                    "artifacts": list(artifacts),
                }
            )
            if item.id == node_id
            else item
            for item in record.node_runs
        ]
        events = [
            *record.events,
            RunEvent(
                schema_version="1.0",
                seq=len(record.events),
                at=timestamp,
                kind=RunEventKind.NODE_SUCCEEDED,
                node_run_id=node_id,
                attempt=attempt,
                message="Node succeeded.",
            ),
        ]
        self.runs.save_run(
            project,
            RunRecord.model_validate(
                record.model_copy(update={"node_runs": nodes, "events": events}).model_dump(
                    mode="json"
                )
            ),
        )

    def _pause_for_review(
        self, project: str, run_id: str, node_id: str, attempt: int, reason: str
    ) -> None:
        timestamp = utc_now_iso()
        record = self.runs.get_run(project, run_id)
        node = next(item for item in record.node_runs if item.id == node_id)
        pause = ReviewPauseRecord(
            schema_version="1.0",
            id=f"review-{node_id}-{attempt}",
            node_run_id=node_id,
            input_identity=node.content_identity,
            requested_at=timestamp,
            decided_at=None,
            decision=None,
            note=reason,
        )
        nodes = [
            item.model_copy(
                update={"state": NodeRunState.WAITING_FOR_REVIEW, "review_pause": pause}
            )
            if item.id == node_id
            else item
            for item in record.node_runs
        ]
        events = [
            *record.events,
            RunEvent(
                schema_version="1.0",
                seq=len(record.events),
                at=timestamp,
                kind=RunEventKind.REVIEW_REQUESTED,
                node_run_id=node_id,
                attempt=attempt,
                message=reason,
            ),
        ]
        state = (
            RunState.WAITING_FOR_REVIEW
            if record.state != RunState.WAITING_FOR_REVIEW
            else record.state
        )
        self.runs.save_run(
            project,
            RunRecord.model_validate(
                record.model_copy(
                    update={"state": state, "node_runs": nodes, "events": events}
                ).model_dump(mode="json")
            ),
        )

    def _fail_node(
        self, project: str, run_id: str, node_id: str, attempt: int, failure: FailureRecord
    ) -> None:
        record = self.runs.get_run(project, run_id)
        self.runs.save_run(project, _fail_run(record, node_id, failure))


def _fail_run(
    record: RunRecord,
    node_id: str,
    failure: FailureRecord,
    timestamp: str | None = None,
    review: ReviewPauseRecord | None = None,
) -> RunRecord:
    """Mark one node failed, skip its downstream closure, and fail the run."""

    at = timestamp or failure.at
    dependencies = {node.id: list(node.dependencies) for node in record.node_runs}
    skipped = downstream_ids({node_id}, dependencies)
    nodes: list[NodeRunRecord] = []
    events = list(record.events)

    def _append(kind: RunEventKind, target: str | None, attempt: int | None, message: str) -> None:
        events.append(
            RunEvent(
                schema_version="1.0",
                seq=len(events),
                at=at,
                kind=kind,
                node_run_id=target,
                attempt=attempt,
                message=message,
            )
        )

    for node in record.node_runs:
        if node.id == node_id:
            failed_node = node.model_copy(
                update={
                    "state": NodeRunState.FAILED,
                    "finished_at": at,
                    "failure": failure,
                }
            )
            if review is not None:
                failed_node = failed_node.model_copy(update={"review_pause": review})
            nodes.append(failed_node)
            _append(RunEventKind.NODE_FAILED, node_id, node.attempt, failure.message)
        elif node.id in skipped and node.state not in NODE_TERMINAL_STATES:
            nodes.append(
                node.model_copy(
                    update={
                        "state": NodeRunState.DEPENDENCY_SKIPPED,
                        "started_at": None,
                        "finished_at": None,
                        "attempt": 0,
                        "review_pause": None,
                    }
                )
            )
            _append(
                RunEventKind.NODE_SKIPPED,
                node.id,
                None,
                f"Skipped because prerequisite {node_id!r} failed.",
            )
        else:
            nodes.append(node)
    run_failure = FailureRecord(
        schema_version="1.0",
        code=failure.code,
        message=failure.message,
        node_run_id=node_id,
        at=at,
    )
    _append(RunEventKind.RUN_FAILED, None, None, failure.message)
    payload = record.model_dump(mode="json")
    payload["node_runs"] = [node.model_dump(mode="json") for node in nodes]
    payload["events"] = [event.model_dump(mode="json") for event in events]
    payload["state"] = RunState.FAILED.value
    payload["finished_at"] = at
    payload["failure"] = run_failure.model_dump(mode="json")
    return RunRecord.model_validate(payload)


def _cancel_record(record: RunRecord, timestamp: str) -> RunRecord:
    """Transition every nonterminal node and the run to cancelled.

    Nodes cancelled before starting keep attempt 0 and no start timestamp;
    nodes cancelled during execution keep their positive attempt and start
    timestamp. Repeated cancellation of a terminal record is a no-op.
    """

    if record.state in RUN_TERMINAL_STATES:
        return record
    nodes: list[NodeRunRecord] = []
    events = list(record.events)

    def _append(kind: RunEventKind, target: str | None, attempt: int | None, message: str) -> None:
        events.append(
            RunEvent(
                schema_version="1.0",
                seq=len(events),
                at=timestamp,
                kind=kind,
                node_run_id=target,
                attempt=attempt,
                message=message,
            )
        )

    for node in record.node_runs:
        if node.state in NODE_TERMINAL_STATES:
            nodes.append(node)
            continue
        if node.state == NodeRunState.QUEUED:
            nodes.append(
                node.model_copy(
                    update={
                        "state": NodeRunState.CANCELLED,
                        "finished_at": timestamp,
                        "attempt": 0,
                    }
                )
            )
            _append(RunEventKind.NODE_CANCELLED, node.id, 0, "Node cancelled before starting.")
            continue
        attempt = node.attempt if node.attempt > 0 else next_attempt(record, node.id)
        nodes.append(
            node.model_copy(
                update={
                    "state": NodeRunState.CANCELLED,
                    "finished_at": timestamp,
                    "attempt": attempt,
                }
            )
        )
        _append(RunEventKind.NODE_CANCELLED, node.id, attempt, "Node cancelled.")
    _append(RunEventKind.RUN_CANCELLED, None, None, "Run cancelled.")
    payload = record.model_dump(mode="json")
    payload["node_runs"] = [node.model_dump(mode="json") for node in nodes]
    payload["events"] = [event.model_dump(mode="json") for event in events]
    payload["state"] = RunState.CANCELLED.value
    payload["finished_at"] = timestamp
    return RunRecord.model_validate(payload)
