"""Project-scoped run-record persistence and restart recovery.

Run records live inside the authorized project that owns them::

    <project-root>/runs/<run-id>/run.json

All paths resolve through the shared :class:`ProjectStore`, so runs inherit
the session-token, Host/Origin, and explicit-root protections. Writes are
atomic. This slice performs no execution: it stores records, answers which
runs are resumable, and repairs interrupted runs after a service restart.
Workers, streaming, cancellation, and caching belong to later slices.
"""

import errno
import hashlib
import json
import os
import re
import stat
import threading
from dataclasses import dataclass, field
from datetime import datetime
from io import BufferedReader
from pathlib import Path
from typing import BinaryIO

from brainlearn_core import (
    RUN_TERMINAL_STATES,
    ArtifactRecord,
    NodeRunRecord,
    NodeRunState,
    RunEvent,
    RunEventKind,
    RunRecord,
    RunState,
    migrate_run_dict,
    validate_media_type,
)
from brainlearn_core.projects import (
    PROJECT_MANIFEST_FILENAME,
    WORKFLOW_FILENAME,
    utc_now_iso,
)
from brainlearn_core.scheduler import topological_order

from brainlearn_server.project_store import ProjectStore, atomic_write_json

RUNS_DIRNAME = "runs"
RUN_FILENAME = "run.json"
_RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
# Largest single read when verifying or serving an artifact, so artifacts of
# any recorded size stream through bounded memory.
_ARTIFACT_CHUNK_SIZE = 1024 * 1024

# Process-wide writer serialization, keyed by canonical (project, run ID).
# Every mutation (create, save, recovery repair) holds its run's lock across
# the read-check-write sequence, so overlapping requests and future worker
# threads sharing this module cannot interleave a stale write over terminal
# history. This serializes threads within this service process only; it is
# not a cross-process lock. Step 4C workers must perform all writes through
# RunStore methods so they share these locks.
_RUN_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_RUN_LOCKS_GUARD = threading.Lock()


def _run_lock(project: Path, run_id: str) -> threading.Lock:
    key = (str(project), run_id)
    with _RUN_LOCKS_GUARD:
        existing = _RUN_LOCKS.get(key)
        if existing is None:
            existing = threading.Lock()
            _RUN_LOCKS[key] = existing
        return existing


def _verified_project_path(projects: ProjectStore, project: Path, relative: str) -> Path:
    """Resolve a recorded project-relative path with per-component checks.

    Rejects empty, absolute, and dot/parent segments lexically, proves the
    lexical path stays inside the project, refuses a symlink in every path
    component without any depth cap, then proves the fully resolved path is
    still contained in the project. Returns the resolved path so the final
    open/read stays tied to the verified location.
    """

    parts = relative.replace("\\", "/").split("/")
    if not relative or relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Artifact path {relative!r} escapes its project.")
    candidate = project
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"Artifact path {relative!r} must not contain symlinks.")
    resolved = projects.require_allowed(candidate.resolve(strict=False))
    try:
        resolved.relative_to(project.resolve(strict=False))
    except ValueError:
        raise ValueError(f"Artifact path {relative!r} escapes its project.") from None
    return resolved


@dataclass
class RunStore:
    projects: ProjectStore = field(default_factory=ProjectStore)

    # -- paths ---------------------------------------------------------
    def _run_file(self, project: Path, run_id: str) -> Path:
        if re.fullmatch(_RUN_ID_PATTERN, run_id) is None:
            raise ValueError(
                f"Run ID {run_id!r} must start with a letter or digit and contain "
                "only letters, digits, dots, underscores, and dashes."
            )
        candidate = project / RUNS_DIRNAME / run_id / RUN_FILENAME
        return self.projects.require_allowed(candidate.resolve(strict=False))

    def _project_dir(self, raw_path: str) -> Path:
        from brainlearn_server.project_store import canonicalize_project_path

        candidate = canonicalize_project_path(raw_path)
        if candidate not in self.projects.allowed_roots:
            # Preserve the outside-roots 403 before rejecting authorized
            # non-root paths with a 400.
            self.projects.require_allowed(candidate)
            raise ValueError(
                f"Project path '{candidate}' is not an explicitly authorized "
                "project root. Open the project itself instead of a "
                "subdirectory so run history stays in one place."
            )
        if (
            not (candidate / PROJECT_MANIFEST_FILENAME).is_file()
            or not (candidate / WORKFLOW_FILENAME).is_file()
        ):
            raise FileNotFoundError(
                f"'{candidate}' is no longer a complete project directory. "
                "Restore its manifest and workflow files before managing runs."
            )
        return candidate

    # -- CRUD ----------------------------------------------------------
    def create_run(self, raw_path: str, record: RunRecord) -> RunRecord:
        project = self._project_dir(raw_path)
        target = self._run_file(project, record.id)
        with _run_lock(project, record.id):
            if target.exists() or target.is_symlink():
                raise FileExistsError(
                    f"Run {record.id!r} already exists in project '{project}'. "
                    "Open it or choose a different run ID."
                )
            atomic_write_json(target, record.model_dump(mode="json"))
        return record

    def get_run(self, raw_path: str, run_id: str) -> RunRecord:
        project = self._project_dir(raw_path)
        target = self._run_file(project, run_id)
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(f"Run {run_id!r} was not found in project '{project}'.")
        record = RunRecord.model_validate(migrate_run_dict(_read_json(target)))
        if record.id != run_id:
            raise ValueError(
                f"Run file '{target}' contains record {record.id!r}, not the requested {run_id!r}."
            )
        return record

    def save_run(self, raw_path: str, record: RunRecord) -> RunRecord:
        """Persist an update to a nonterminal run.

        Terminal runs (succeeded, failed, cancelled) are immutable history:
        every update to one is rejected before anything is written, so the
        stored file is preserved byte-for-byte. Lifecycle transitions beyond
        that rule (who may move a run between nonterminal states and when)
        belong to the worker slice.
        """

        project = self._project_dir(raw_path)
        target = self._run_file(project, record.id)
        with _run_lock(project, record.id):
            if not target.is_file() or target.is_symlink():
                raise FileNotFoundError(
                    f"Run {record.id!r} was not found in project '{project}'. "
                    "Create the run before saving it."
                )
            stored = RunRecord.model_validate(migrate_run_dict(_read_json(target)))
            if stored.state in RUN_TERMINAL_STATES:
                raise ValueError(
                    f"Run {record.id!r} is {stored.state.value}; terminal run "
                    "history is immutable and cannot be saved over."
                )
            atomic_write_json(target, record.model_dump(mode="json"))
        return record

    def list_runs(self, raw_path: str) -> list[RunRecord]:
        """List every persisted run, surfacing corrupt run files.

        Discovery never follows symlinks: symlinked directories and run
        files are ignored, as are entries without a ``run.json`` or with a
        directory name outside the run-ID grammar. A present ``run.json``
        whose directory name is valid but whose record fails to parse,
        migrate, validate, or match its directory is surfaced as a
        ``ValueError`` naming the path and cause instead of silently
        disappearing from history and restart recovery.
        """

        project = self._project_dir(raw_path)
        runs_dir = project / RUNS_DIRNAME
        if not runs_dir.is_dir():
            return []
        records: list[RunRecord] = []
        for child in sorted(runs_dir.iterdir()):
            candidate = child / RUN_FILENAME
            if child.is_symlink() or not child.is_dir():
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if re.fullmatch(_RUN_ID_PATTERN, child.name) is None:
                continue
            # Resolve and authorize the discovered candidate before reading,
            # keeping discovery inside the explicit-root boundary.
            authorized = self.projects.require_allowed(candidate.resolve(strict=False))
            try:
                payload = _read_json(authorized)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Run file '{candidate}' cannot be read: {exc}") from exc
            try:
                record = RunRecord.model_validate(migrate_run_dict(payload))
            except ValueError as exc:
                raise ValueError(f"Run file '{candidate}' is invalid: {exc}") from exc
            if record.id != child.name:
                raise ValueError(
                    f"Run file '{candidate}' contains record {record.id!r}, "
                    f"not the directory's {child.name!r}."
                )
            records.append(record)
        return records

    # -- artifact access ---------------------------------------------
    def open_artifact_stream(
        self, raw_path: str, run_id: str, artifact_id: str
    ) -> tuple[BinaryIO, ArtifactRecord]:
        """Open one recorded artifact for bounded streaming reads.

        Only an artifact ID present in the requested persisted run resolves;
        nothing else on disk is addressable. Project authorization,
        containment, symlink refusal, size, and SHA-256 are rechecked on
        every access so a file swapped, linked, or moved after the run no
        longer matches its record. Verification reads bounded chunks through
        the no-follow-opened descriptor, rewinds that same descriptor, and
        hands it to the caller, which must close it; at most one chunk ever
        resides in memory regardless of artifact size.
        """

        project = self._project_dir(raw_path)
        record = self.get_run(str(project), run_id)
        artifact: ArtifactRecord | None = None
        for node in record.node_runs:
            for item in node.artifacts:
                if item.artifact_id == artifact_id:
                    artifact = item
        if artifact is None:
            raise FileNotFoundError(f"Artifact {artifact_id!r} is not recorded on run {run_id!r}.")
        try:
            validate_media_type(artifact.media_type)
        except ValueError as exc:
            raise ValueError(
                f"Artifact {artifact.path!r} records an unsafe media type: {exc}"
            ) from None
        resolved = _verified_project_path(self.projects, project, artifact.path)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(resolved, flags)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Artifact {artifact.path!r} is no longer present in project '{project}'."
            ) from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"Artifact {artifact.path!r} must not contain symlinks.") from None
            raise FileNotFoundError(
                f"Artifact {artifact.path!r} is no longer present in project '{project}'."
            ) from None
        try:
            file_stat = os.fstat(fd)
        except OSError as exc:
            os.close(fd)
            raise ValueError(f"Artifact {artifact.path!r} could not be inspected: {exc}.") from None
        if not stat.S_ISREG(file_stat.st_mode):
            os.close(fd)
            raise ValueError(f"Artifact {artifact.path!r} is not a regular file.")
        try:
            handle: BufferedReader = os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise
        try:
            digest = hashlib.sha256()
            total = 0
            while True:
                try:
                    chunk = os.read(fd, _ARTIFACT_CHUNK_SIZE)
                except OSError as exc:
                    raise ValueError(
                        f"Artifact {artifact.path!r} could not be read: {exc}."
                    ) from None
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
            if total != artifact.byte_size or digest.hexdigest() != artifact.sha256:
                raise ValueError(
                    f"Artifact {artifact.path!r} no longer matches its recorded "
                    "size and SHA-256; the file may have been modified after the run."
                )
            os.lseek(fd, 0, os.SEEK_SET)
        except BaseException:
            handle.close()
            raise
        return handle, artifact

    # -- restart recovery ----------------------------------------------
    def find_nonterminal_runs(self, raw_path: str) -> list[RunRecord]:
        return [
            record for record in self.list_runs(raw_path) if record.state not in RUN_TERMINAL_STATES
        ]

    def recover_run(self, raw_path: str, run_id: str, now: str | None = None) -> RunRecord | None:
        """Reconcile one nonterminal run; return it only when changed."""

        timestamp = now or utc_now_iso()
        project = self._project_dir(raw_path)
        with _run_lock(project, run_id):
            # Re-read under the lock: a concurrent writer may have
            # finished the run after discovery listed it.
            current = self.get_run(str(project), run_id)
            if current.state in RUN_TERMINAL_STATES:
                return None
            repaired = _recover_record(current, timestamp)
            if repaired is None:
                return None
            target = self._run_file(project, repaired.id)
            atomic_write_json(target, repaired.model_dump(mode="json"))
            return repaired

    def recover_runs(self, raw_path: str, now: str | None = None) -> list[RunRecord]:
        """Repair runs interrupted by a service restart.

        Every nonterminal run is reconciled, whether or not a node was
        executing at shutdown:

        - ``running`` nodes are voided back to ``queued`` with ``attempt``
          reset to 0; the voided attempt number is preserved structurally in
          the appended event, and the next execution assigns a fresh positive
          attempt number.
        - A ``waiting_for_review`` node survives only while none of its
          prerequisites were interrupted; otherwise its stale review is
          voided and it is requeued like an interrupted node.
        - The run state is rederived from its nodes: waiting wins, then
          succeeded once every node completed, then queued when nothing ever
          completed, otherwise running. A run that completes during recovery
          records a ``run_succeeded`` event and finish time.
        - Terminal history is never touched. Returns the runs that changed.
        """

        timestamp = now or utc_now_iso()
        project = self._project_dir(raw_path)
        recovered: list[RunRecord] = []
        for record in self.find_nonterminal_runs(raw_path):
            repaired = self.recover_run(str(project), record.id, now=timestamp)
            if repaired is None:
                continue
            recovered.append(repaired)
        return recovered


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Run file '{path}' must contain a JSON object.")
    return payload


def _recover_record(record: RunRecord, timestamp: str) -> RunRecord | None:
    by_id = {node.id: node for node in record.node_runs}
    order = topological_order({node.id: list(node.dependencies) for node in record.node_runs})
    # Nodes whose computation never completed: interrupted executions plus
    # waiting reviews whose prerequisites were interrupted (cascading in
    # topological order so downstream reviews invalidate transitively).
    voided: set[str] = {node.id for node in record.node_runs if node.state == NodeRunState.RUNNING}
    for node_id in order:
        node = by_id[node_id]
        if node.state == NodeRunState.WAITING_FOR_REVIEW and any(
            dependency in voided for dependency in node.dependencies
        ):
            voided.add(node_id)
    events = list(record.events)

    def _requeue_event(node: NodeRunRecord, reason: str) -> RunEvent:
        return RunEvent(
            schema_version="1.0",
            seq=len(events),
            at=timestamp,
            kind=RunEventKind.NODE_QUEUED,
            node_run_id=node.id,
            attempt=node.attempt,
            message=reason,
        )

    repaired_nodes: list[NodeRunRecord] = []
    for node in record.node_runs:
        if node.id not in voided:
            repaired_nodes.append(node)
            continue
        repaired_nodes.append(
            node.model_copy(
                update={
                    "state": NodeRunState.QUEUED,
                    "started_at": None,
                    "finished_at": None,
                    # Queued work carries attempt 0; the voided number is
                    # preserved structurally on the event, and the next
                    # execution assigns a fresh positive attempt number.
                    "attempt": 0,
                    "review_pause": None,
                }
            )
        )
        if node.state == NodeRunState.RUNNING:
            reason = (
                f"Requeued after a service restart; interrupted attempt {node.attempt} was voided."
            )
        else:
            reason = (
                "Prior review voided after a service restart; prerequisite "
                f"computation never completed (attempt {node.attempt} was voided)."
            )
        events.append(_requeue_event(node, reason))
    states = {node.state for node in repaired_nodes}
    if NodeRunState.WAITING_FOR_REVIEW in states:
        new_state = RunState.WAITING_FOR_REVIEW
    elif not states or states == {NodeRunState.QUEUED}:
        new_state = RunState.QUEUED
    elif states <= {NodeRunState.SUCCEEDED, NodeRunState.CACHE_REUSED}:
        new_state = RunState.SUCCEEDED
    else:
        new_state = RunState.RUNNING
    changed = bool(voided) or new_state != record.state
    if not changed:
        return None
    payload = record.model_dump(mode="json")
    payload["node_runs"] = [node.model_dump(mode="json") for node in repaired_nodes]
    payload["events"] = [event.model_dump(mode="json") for event in events]
    payload["state"] = new_state.value
    if new_state == RunState.QUEUED:
        payload["started_at"] = None
        events.append(
            RunEvent(
                schema_version="1.0",
                seq=len(events),
                at=timestamp,
                kind=RunEventKind.RUN_QUEUED,
                node_run_id=None,
                message="Run requeued after a service restart.",
            )
        )
        payload["events"] = [event.model_dump(mode="json") for event in events]
    elif new_state == RunState.SUCCEEDED:
        candidates = [timestamp]
        if payload["started_at"] is not None:
            candidates.append(payload["started_at"])
        candidates.extend(node.finished_at for node in repaired_nodes if node.finished_at)
        payload["finished_at"] = max(candidates, key=datetime.fromisoformat)
        events.append(
            RunEvent(
                schema_version="1.0",
                seq=len(events),
                at=timestamp,
                kind=RunEventKind.RUN_SUCCEEDED,
                node_run_id=None,
                message="Run completed during restart recovery; all nodes had finished.",
            )
        )
        payload["events"] = [event.model_dump(mode="json") for event in events]
    elif new_state == RunState.WAITING_FOR_REVIEW and record.state != RunState.WAITING_FOR_REVIEW:
        events.append(
            RunEvent(
                schema_version="1.0",
                seq=len(events),
                at=timestamp,
                kind=RunEventKind.REVIEW_REQUESTED,
                node_run_id=None,
                message="Run awaits a review requested before the service restart.",
            )
        )
        payload["events"] = [event.model_dump(mode="json") for event in events]
    return RunRecord.model_validate(payload)
