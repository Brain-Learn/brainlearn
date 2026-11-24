"""Sequential cohort execution over the allowlisted per-recording workflow."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brainlearn_core import (
    RUN_TERMINAL_STATES,
    CitationMetadata,
    CohortInputFile,
    CohortOutputReference,
    CohortRecordingRun,
    CohortRecordingState,
    CohortRunRecord,
    CohortRunState,
    NodeRunState,
    RunState,
    Workflow,
    migrate_cohort_run_dict,
)

from brainlearn_server.bids_eeg import BidsEegDiscoveryService, BidsEegRecording
from brainlearn_server.project_store import atomic_write_json
from brainlearn_server.registry import NODE_REGISTRY_BY_ID
from brainlearn_server.run_store import _verified_project_path
from brainlearn_server.worker import WorkerService, build_run_record, workflow_identity

COHORTS_DIRNAME = "cohorts"
COHORT_FILENAME = "cohort.json"
COHORT_ID_RE = re.compile(r"^cohort-[A-Za-z0-9][A-Za-z0-9._-]*$")
POLL_INTERVAL_SECONDS = 0.25


class CohortExecutionError(ValueError):
    """A cohort selection is invalid or cannot be safely executed."""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class CohortStore:
    """Project-scoped storage for cohort manifests and aggregate result artifacts."""

    def __init__(self, workers: WorkerService) -> None:
        self.workers = workers
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._guard = threading.Lock()

    def _lock(self, project: Path, cohort_id: str) -> threading.RLock:
        key = (str(project), cohort_id)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def _cohort_dir(self, project_path: str, cohort_id: str) -> tuple[Path, Path]:
        if COHORT_ID_RE.fullmatch(cohort_id) is None:
            raise ValueError("Cohort ID has an invalid format.")
        project = self.workers.runs._project_dir(project_path)
        cohorts_dir = project / COHORTS_DIRNAME
        target_dir = cohorts_dir / cohort_id
        for component in (cohorts_dir, target_dir):
            if component.is_symlink():
                raise OSError("Cohort storage contains a symlink and is blocked.")
            self.workers.runs.projects.require_allowed(component.resolve(strict=False))
        try:
            target_dir.resolve(strict=False).relative_to(project.resolve(strict=False))
        except ValueError:
            raise OSError("Cohort storage escapes its project root.") from None
        return project, target_dir

    def _cohort_file(self, project_path: str, cohort_id: str) -> tuple[Path, Path]:
        project, directory = self._cohort_dir(project_path, cohort_id)
        target = directory / COHORT_FILENAME
        if target.is_symlink():
            raise OSError("Cohort record path is a symlink and is blocked.")
        self.workers.runs.projects.require_allowed(target.resolve(strict=False))
        return project, target

    def create(self, project_path: str, record: CohortRunRecord) -> CohortRunRecord:
        project, target = self._cohort_file(project_path, record.id)
        with self._lock(project, record.id):
            if target.exists():
                raise FileExistsError(f"Cohort run {record.id!r} already exists.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.parent.is_symlink():
                raise OSError("Cohort record directory changed into a symlink.")
            atomic_write_json(target, record.model_dump(mode="json"))
        return record

    def get(self, project_path: str, cohort_id: str) -> CohortRunRecord:
        project, target = self._cohort_file(project_path, cohort_id)
        if target.is_symlink() or not target.is_file():
            raise FileNotFoundError(f"Cohort run {cohort_id!r} was not found.")
        self.workers.runs.projects.require_allowed(target.resolve(strict=True))
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            record = CohortRunRecord.model_validate(migrate_cohort_run_dict(payload))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cohort record {cohort_id!r} is invalid: {exc}") from exc
        if record.id != target.parent.name:
            raise ValueError("Cohort record ID does not match its storage directory.")
        if target.parent.parent != project / COHORTS_DIRNAME:
            raise ValueError("Cohort record path is outside the project cohort store.")
        return record

    def list_records(self, project_path: str) -> list[CohortRunRecord]:
        project = self.workers.runs._project_dir(project_path)
        directory = project / COHORTS_DIRNAME
        if directory.is_symlink():
            raise OSError("Cohort storage contains a symlink and is blocked.")
        self.workers.runs.projects.require_allowed(directory.resolve(strict=False))
        if not directory.exists():
            return []
        if not directory.is_dir():
            raise OSError("Cohort storage path is not a directory.")
        records = []
        for entry in sorted(directory.iterdir()):
            if (
                not entry.is_dir()
                or entry.is_symlink()
                or COHORT_ID_RE.fullmatch(entry.name) is None
            ):
                continue
            records.append(self.get(project_path, entry.name))
        return records

    def save(self, project_path: str, record: CohortRunRecord) -> CohortRunRecord:
        project, target = self._cohort_file(project_path, record.id)
        with self._lock(project, record.id):
            current = self.get(project_path, record.id)
            if current.finished_at is not None:
                raise ValueError("Terminal cohort records are immutable.")
            atomic_write_json(target, record.model_dump(mode="json"))
        return record

    def write_results(
        self, project_path: str, cohort_id: str, summary: dict[str, Any], report_html: str
    ) -> tuple[str, str, str, str]:
        project, directory = self._cohort_dir(project_path, cohort_id)
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OSError("Cohort artifact directory is a symlink and is blocked.")
        prefix = f"{COHORTS_DIRNAME}/{cohort_id}"
        summary_path = f"{prefix}/summary.json"
        report_path = f"{prefix}/report.html"
        summary_bytes = (
            json.dumps(summary, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            + "\n"
        ).encode()
        report_bytes = report_html.encode("utf-8")
        self._atomic_write_bytes(project, summary_path, summary_bytes)
        self._atomic_write_bytes(project, report_path, report_bytes)
        return (
            summary_path,
            hashlib.sha256(summary_bytes).hexdigest(),
            report_path,
            hashlib.sha256(report_bytes).hexdigest(),
        )

    def read_result(self, project_path: str, cohort_id: str, kind: str) -> tuple[bytes, str]:
        record = self.get(project_path, cohort_id)
        if kind == "report":
            relative, expected_sha = record.report_path, record.report_sha256
            content_type = "text/html; charset=utf-8"
        elif kind == "summary":
            relative, expected_sha = record.summary_path, record.summary_sha256
            content_type = "application/json"
        else:
            raise ValueError("Result kind must be report or summary.")
        if relative is None or expected_sha is None:
            raise FileNotFoundError(f"Cohort {kind} is not available yet.")
        project = self.workers.runs._project_dir(project_path)
        target = _verified_project_path(self.workers.runs.projects, project, relative)
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise OSError(f"The saved cohort {kind} no longer matches its recorded checksum.")
        return data, content_type

    def _atomic_write_bytes(self, project: Path, relative: str, data: bytes) -> None:
        target = _verified_project_path(self.workers.runs.projects, project, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        _verified_project_path(self.workers.runs.projects, project, relative)
        fd, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


class CohortRunService:
    """Create and drive a cohort as sequential, reviewable single-record runs."""

    REQUIRED_NODE_TYPES = (
        "input.bids_eeg",
        "eeg.inspect",
        "eeg.bad_channel_review",
        "eeg.filter",
        "eeg.ica_fit",
        "eeg.ica_review",
        "eeg.epochs",
        "eeg.erp_average",
        "eeg.psd",
        "output.report",
    )

    def __init__(self, workers: WorkerService, bids: BidsEegDiscoveryService) -> None:
        self.workers = workers
        self.bids = bids
        self.store = CohortStore(workers)
        self._guard = threading.RLock()
        self._threads: dict[tuple[str, str], threading.Thread] = {}
        self._cancel: dict[tuple[str, str], threading.Event] = {}
        self._active_children: dict[tuple[str, str], str] = {}

    def start(
        self,
        project_path: str,
        workflow: Workflow,
        recording_paths: list[str],
        *,
        seed: int = 0,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
    ) -> CohortRunRecord:
        if not recording_paths:
            raise CohortExecutionError("Select at least one discovered BIDS EEG recording.")
        if len(set(recording_paths)) != len(recording_paths):
            raise CohortExecutionError("Selected recording paths must be unique.")
        if seed < 0:
            raise CohortExecutionError("Cohort seed must be a non-negative integer.")
        self._validate_template(workflow, recording_paths[0])
        input_node = next(node for node in workflow.nodes if node.type == "input.bids_eeg")
        dataset_path = self._parameter(input_node, "root")
        try:
            discovery = self.bids.discover(project_path, dataset_path)
        except (OSError, ValueError) as exc:
            raise CohortExecutionError(
                f"Unable to inspect the selected BIDS dataset: {exc}"
            ) from exc
        if discovery.status != "ready":
            raise CohortExecutionError(
                "The selected folder is not a ready BIDS EEG dataset. Correct its BIDS metadata "
                "and scan it again before creating a cohort run."
            )
        by_path = {item.path: item for item in discovery.recordings}
        missing = [path for path in recording_paths if path not in by_path]
        if missing:
            raise CohortExecutionError(
                f"Recording {missing[0]!r} is not in the current discovery result. Scan again "
                "and select one of the listed recordings."
            )

        selected = [by_path[path] for path in recording_paths]
        expected_metadata = next((item for item in selected if item.status == "ready"), None)
        recordings: list[CohortRecordingRun] = []
        for recording in selected:
            reason = self._metadata_incompatibility(recording, expected_metadata)
            identity = None
            identity_files: list[CohortInputFile] = []
            if reason is None:
                try:
                    resolved = self.bids.identify(project_path, dataset_path, recording.path)
                except Exception as exc:
                    reason = f"preflight: Unable to verify the recording files: {exc}"
                else:
                    if resolved.status != "ready" or resolved.content_identity is None:
                        reason = (
                            f"preflight: {resolved.message or 'Source identity is unavailable.'}"
                        )
                    else:
                        identity = resolved.content_identity
                        identity_files = [
                            CohortInputFile(
                                path=item.path,
                                byte_size=item.byte_size,
                                sha256=item.sha256,
                            )
                            for item in resolved.files
                        ]
            if reason is not None:
                recordings.append(
                    self._recording_record(
                        recording,
                        state=CohortRecordingState.EXCLUDED,
                        failure_reason=reason
                        if reason.startswith("preflight:")
                        else f"preflight: {reason}",
                    )
                )
            else:
                recordings.append(
                    self._recording_record(
                        recording,
                        state=CohortRecordingState.SELECTED,
                        input_identity=identity,
                        input_files=identity_files,
                    )
                )

        cohort_id = f"cohort-{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        citations = self._citations(workflow)
        if dataset_id is not None and dataset_id == "ds005284":
            citations.append(
                CitationMetadata(
                    title="26 By Biosemi",
                    doi="10.18112/openneuro.ds005284.v1.0.0",
                    url="https://openneuro.org/datasets/ds005284/versions/1.0.0",
                )
            )
        record = CohortRunRecord(
            schema_version="1.2",
            id=cohort_id,
            state=CohortRunState.RUNNING,
            dataset_path=dataset_path,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            workflow_id=workflow.id,
            workflow_identity=workflow_identity(workflow),
            workflow=workflow,
            created_at=now,
            started_at=now,
            seed=seed,
            parameters=self._parameters(workflow),
            recordings=recordings,
            selected_count=len(recordings),
            processed_count=0,
            excluded_count=sum(item.state == CohortRecordingState.EXCLUDED for item in recordings),
            failed_count=0,
            cancelled_count=0,
            processed_participant_count=0,
            citations=self._deduplicate_citations(citations),
        )
        self.store.create(project_path, record)
        key = (str(Path(project_path).resolve()), cohort_id)
        cancel_flag = threading.Event()
        thread = threading.Thread(
            target=self._drive,
            args=(project_path, workflow, cohort_id, cancel_flag),
            name=f"brainlearn-{cohort_id}",
            daemon=True,
        )
        with self._guard:
            self._cancel[key] = cancel_flag
            self._threads[key] = thread
        thread.start()
        return self.store.get(project_path, cohort_id)

    def get(self, project_path: str, cohort_id: str) -> CohortRunRecord:
        return self.store.get(project_path, cohort_id)

    def recover_project(self, project_path: str) -> list[CohortRunRecord]:
        """Resume persisted cohort drivers after reconciling their child runs."""

        self.workers.recover_project(project_path)
        recovered = []
        for record in self.store.list_records(project_path):
            if record.finished_at is not None:
                recovered.append(record)
                continue
            if (
                record.workflow is None
                or workflow_identity(record.workflow) != record.workflow_identity
            ):
                failed_items = [
                    item.model_copy(
                        update={
                            "state": CohortRecordingState.FAILED,
                            "failure_reason": (
                                "Cannot recover this cohort because its persisted workflow "
                                "snapshot is missing or has changed."
                            ),
                        }
                    )
                    if item.state
                    in {
                        CohortRecordingState.SELECTED,
                        CohortRecordingState.RUNNING,
                        CohortRecordingState.WAITING_FOR_REVIEW,
                    }
                    else item
                    for item in record.recordings
                ]
                failed = self._copy_counts(
                    record.model_copy(
                        update={
                            "recordings": failed_items,
                            "state": CohortRunState.FAILED,
                            "finished_at": utc_now_iso(),
                            "failure_reason": (
                                "Cannot resume the cohort: its workflow snapshot is missing "
                                "or does not match the recorded workflow identity."
                            ),
                        }
                    )
                )
                saved = self.store.save(
                    project_path, CohortRunRecord.model_validate(failed.model_dump(mode="json"))
                )
                recovered.append(saved)
                continue
            key = (str(Path(project_path).resolve()), record.id)
            with self._guard:
                existing = self._threads.get(key)
                if existing is not None and existing.is_alive():
                    recovered.append(self.store.get(project_path, record.id))
                    continue
                cancel_flag = threading.Event()
                thread = threading.Thread(
                    target=self._drive,
                    args=(project_path, record.workflow, record.id, cancel_flag),
                    name=f"brainlearn-{record.id}",
                    daemon=True,
                )
                self._cancel[key] = cancel_flag
                self._threads[key] = thread
                thread.start()
            recovered.append(self.store.get(project_path, record.id))
        return recovered

    def cancel(self, project_path: str, cohort_id: str) -> CohortRunRecord:
        record = self.store.get(project_path, cohort_id)
        if record.finished_at is not None:
            return record
        key = (str(Path(project_path).resolve()), cohort_id)
        with self._guard:
            flag = self._cancel.setdefault(key, threading.Event())
            flag.set()
            active = self._active_children.get(key)
        if active:
            try:
                self.workers.cancel_run(project_path, active)
            except (FileNotFoundError, ValueError):
                pass
        return self.store.get(project_path, cohort_id)

    @staticmethod
    def _parameter(node: Any, parameter_id: str) -> str:
        parameter = next((item for item in node.parameters if item.id == parameter_id), None)
        if parameter is None or not isinstance(parameter.value, str) or not parameter.value.strip():
            raise CohortExecutionError(
                f"Set {node.label}'s {parameter_id!r} parameter to a project-relative BIDS path."
            )
        return parameter.value

    def _validate_template(self, workflow: Workflow, example_recording_path: str) -> None:
        from brainlearn_core import validate_workflow

        validation = validate_workflow(workflow)
        if not validation.valid:
            details = "; ".join(issue.message for issue in validation.issues)
            raise CohortExecutionError(f"The cohort workflow is invalid: {details}")
        by_type: dict[str, list[Any]] = {}
        for node in workflow.nodes:
            by_type.setdefault(node.type, []).append(node)
        missing = [node_type for node_type in self.REQUIRED_NODE_TYPES if node_type not in by_type]
        duplicate = [
            node_type
            for node_type in self.REQUIRED_NODE_TYPES
            if len(by_type.get(node_type, [])) > 1
        ]
        if missing or duplicate:
            missing_details: list[str] = []
            if missing:
                missing_details.append("missing " + ", ".join(missing))
            if duplicate:
                missing_details.append("multiple " + ", ".join(duplicate))
            raise CohortExecutionError(
                "Cohort execution requires one complete descriptive EEG graph ("
                + "; ".join(missing_details)
                + ")."
            )
        node_ids = {node.type: node.id for node in workflow.nodes}
        required_edges = {
            (node_ids["input.bids_eeg"], "dataset", node_ids["eeg.inspect"], "dataset"),
            (node_ids["eeg.inspect"], "raw", node_ids["eeg.bad_channel_review"], "raw"),
            (node_ids["eeg.bad_channel_review"], "reviewed", node_ids["eeg.filter"], "raw"),
            (node_ids["eeg.filter"], "filtered", node_ids["eeg.ica_fit"], "raw"),
            (node_ids["eeg.filter"], "filtered", node_ids["eeg.ica_review"], "raw"),
            (node_ids["eeg.ica_fit"], "ica", node_ids["eeg.ica_review"], "fit"),
            (node_ids["eeg.ica_review"], "reviewed", node_ids["eeg.epochs"], "reviewed"),
            (node_ids["eeg.epochs"], "epochs", node_ids["eeg.erp_average"], "epochs"),
            (node_ids["eeg.epochs"], "epochs", node_ids["eeg.psd"], "epochs"),
            (node_ids["eeg.epochs"], "diagnostics", node_ids["output.report"], "diagnostics"),
            (node_ids["eeg.erp_average"], "evoked", node_ids["output.report"], "evoked"),
            (node_ids["eeg.psd"], "spectrum", node_ids["output.report"], "spectrum"),
        }
        actual_edges = {
            (edge.source.node_id, edge.source.port_id, edge.target.node_id, edge.target.port_id)
            for edge in workflow.edges
        }
        missing_edges = required_edges - actual_edges
        if missing_edges:
            raise CohortExecutionError(
                "The cohort graph is missing required scientific workflow connections. "
                "Restore the BIDS → inspection → channel review → filter → ICA review → "
                "epochs → ERP/PSD → report topology."
            )
        from brainlearn_server.worker import _validate_node_parameters

        configured = self._workflow_for_recording(workflow, example_recording_path)
        for node in configured.nodes:
            _validate_node_parameters(node)

    @staticmethod
    def _metadata_incompatibility(
        recording: BidsEegRecording, reference: BidsEegRecording | None
    ) -> str | None:
        if recording.status != "ready":
            details = "; ".join(issue.message for issue in recording.issues)
            return f"Recording is {recording.status}; correct its BIDS metadata. {details}".strip()
        if recording.recording_type == "epoched":
            return (
                "RecordingType is 'epoched'. This cohort template requires continuous EEG for its "
                "filter and ICA nodes. Use a continuous recording or an explicitly designed "
                "epoched-data workflow; BrainLearn will not reinterpret existing trials."
            )
        if recording.recording_type == "discontinuous":
            return (
                "RecordingType is 'discontinuous'. This cohort template does not preserve "
                "acquisition segment boundaries during filtering and ICA. Select continuous EEG "
                "or use a workflow designed for discontinuous data."
            )
        if reference is None:
            return None
        if recording.recording_type != reference.recording_type:
            return (
                f"RecordingType {recording.recording_type!r} differs from "
                f"{reference.recording_type!r}. Select recordings with matching BIDS recording "
                "types and analyze them separately."
            )
        if recording.sampling_frequency_hz != reference.sampling_frequency_hz:
            return (
                f"Sampling frequency {recording.sampling_frequency_hz} Hz differs from "
                f"{reference.sampling_frequency_hz} Hz. Select a compatible cohort or analyze "
                "different sampling rates separately; BrainLearn does not resample."
            )
        if recording.channel_names != reference.channel_names:
            return (
                "Channel names or ordering differ from the first compatible recording. Select "
                "recordings with matching channel metadata or analyze them separately; "
                "BrainLearn does not interpolate channels."
            )
        return None

    @staticmethod
    def _recording_record(
        recording: BidsEegRecording,
        *,
        state: CohortRecordingState,
        input_identity: str | None = None,
        input_files: list[CohortInputFile] | None = None,
        failure_reason: str | None = None,
    ) -> CohortRecordingRun:
        entities = dict(
            item.split("-", 1)
            for item in Path(recording.path).name.removesuffix("_eeg.").split("_")
            if "-" in item
        )
        return CohortRecordingRun(
            recording_path=recording.path,
            participant=f"sub-{recording.subject}",
            session=f"ses-{recording.session}" if recording.session else None,
            run=entities.get("run"),
            state=state,
            input_identity=input_identity,
            input_files=input_files or [],
            failure_reason=failure_reason,
        )

    @staticmethod
    def _parameters(workflow: Workflow) -> dict[str, Any]:
        return {
            node.id: {parameter.id: parameter.value for parameter in node.parameters}
            for node in workflow.nodes
        }

    @staticmethod
    def _citations(workflow: Workflow) -> list[CitationMetadata]:
        citations: list[CitationMetadata] = []
        for node in workflow.nodes:
            manifest = NODE_REGISTRY_BY_ID.get(node.type)
            if manifest is None:
                continue
            citations.extend(manifest.citations)
            for dependency in manifest.software_dependencies:
                citations.extend(dependency.citations)
        return CohortRunService._deduplicate_citations(citations)

    @staticmethod
    def _deduplicate_citations(citations: list[CitationMetadata]) -> list[CitationMetadata]:
        unique: dict[str, CitationMetadata] = {}
        for citation in citations:
            unique[citation.doi or citation.url or citation.title] = citation
        return [unique[key] for key in sorted(unique)]

    def _drive(
        self, project_path: str, workflow: Workflow, cohort_id: str, cancel_flag: threading.Event
    ) -> None:
        key = (str(Path(project_path).resolve()), cohort_id)
        for index, initial in enumerate(self.store.get(project_path, cohort_id).recordings):
            if initial.state in {
                CohortRecordingState.PROCESSED,
                CohortRecordingState.EXCLUDED,
                CohortRecordingState.FAILED,
                CohortRecordingState.CANCELLED,
            }:
                continue
            if cancel_flag.is_set():
                self._finish_cancelled(project_path, cohort_id)
                return
            recording_workflow = self._workflow_for_recording(workflow, initial.recording_path)
            child_run_id: str | None = None
            try:
                if initial.child_run_id is not None:
                    child_run_id = initial.child_run_id
                    child = self.workers.runs.get_run(project_path, child_run_id)
                else:
                    child = self.workers.runs.create_run(
                        project_path,
                        build_run_record(
                            recording_workflow,
                            seed=(self.store.get(project_path, cohort_id).seed + index) % (2**32),
                        ),
                    )
                    child_run_id = child.id
                with self._guard:
                    self._active_children[key] = child.id
                current = self.store.get(project_path, cohort_id)
                if initial.child_run_id is None:
                    self._replace_recording(
                        project_path,
                        current,
                        initial.recording_path,
                        state=CohortRecordingState.RUNNING,
                        child_run_id=child.id,
                        parameters=self._parameters(recording_workflow),
                    )
                if child.state not in RUN_TERMINAL_STATES:
                    self.workers.start_existing(project_path, child.id)
            except Exception as exc:
                current = self.store.get(project_path, cohort_id)
                self._replace_recording(
                    project_path,
                    current,
                    initial.recording_path,
                    state=CohortRecordingState.FAILED,
                    child_run_id=child_run_id,
                    failure_reason=(
                        f"Unable to start the per-recording workflow: {type(exc).__name__}: {exc}"
                    ),
                    qc_review_ids=(
                        [
                            item.id
                            for item in self.workers.runs.list_qc_reviews(
                                project_path, child_run_id
                            )
                        ]
                        if child_run_id is not None
                        else []
                    ),
                )
                self._clear_active(key)
                continue

            while not cancel_flag.is_set():
                try:
                    child_record = self.workers.runs.get_run(project_path, child.id)
                except Exception as exc:
                    current = self.store.get(project_path, cohort_id)
                    self._replace_recording(
                        project_path,
                        current,
                        initial.recording_path,
                        state=CohortRecordingState.FAILED,
                        child_run_id=child.id,
                        failure_reason=(
                            f"Unable to read child run state: {type(exc).__name__}: {exc}"
                        ),
                        qc_review_ids=[
                            item.id
                            for item in self.workers.runs.list_qc_reviews(project_path, child.id)
                        ],
                    )
                    break
                if child_record.state in RUN_TERMINAL_STATES:
                    current = self.store.get(project_path, cohort_id)
                    if child_record.state == RunState.SUCCEEDED:
                        identity_failure = self._child_source_identity_failure(
                            project_path, initial, child_record
                        )
                        if identity_failure is not None:
                            self._replace_recording(
                                project_path,
                                current,
                                initial.recording_path,
                                state=CohortRecordingState.FAILED,
                                child_run_id=child.id,
                                failure_reason=identity_failure,
                                qc_review_ids=[
                                    item.id
                                    for item in self.workers.runs.list_qc_reviews(
                                        project_path, child.id
                                    )
                                ],
                            )
                        else:
                            reviews = self.workers.runs.list_qc_reviews(project_path, child.id)
                            outputs = [
                                CohortOutputReference(
                                    node_id=node.node_id,
                                    node_type=node.node_type,
                                    port_id=artifact.port_id,
                                    artifact_id=artifact.artifact_id,
                                    sha256=artifact.sha256,
                                )
                                for node in child_record.node_runs
                                for artifact in node.artifacts
                            ]
                            self._replace_recording(
                                project_path,
                                current,
                                initial.recording_path,
                                state=CohortRecordingState.PROCESSED,
                                child_run_id=child.id,
                                qc_review_ids=[item.id for item in reviews],
                                outputs=outputs,
                            )
                    elif (
                        child_record.failure is not None
                        and child_record.failure.code == "review_rejected"
                    ):
                        self._replace_recording(
                            project_path,
                            current,
                            initial.recording_path,
                            state=CohortRecordingState.EXCLUDED,
                            child_run_id=child.id,
                            failure_reason=child_record.failure.message
                            or "The researcher rejected this recording during QC.",
                            qc_review_ids=[
                                item.id
                                for item in self.workers.runs.list_qc_reviews(
                                    project_path, child.id
                                )
                            ],
                        )
                    else:
                        reason = (
                            child_record.failure.message
                            if child_record.failure is not None
                            else f"The child workflow ended in state {child_record.state.value}."
                        )
                        self._replace_recording(
                            project_path,
                            current,
                            initial.recording_path,
                            state=CohortRecordingState.FAILED,
                            child_run_id=child.id,
                            failure_reason=reason,
                            qc_review_ids=[
                                item.id
                                for item in self.workers.runs.list_qc_reviews(
                                    project_path, child.id
                                )
                            ],
                        )
                    break
                next_state = (
                    CohortRecordingState.WAITING_FOR_REVIEW
                    if child_record.state == RunState.WAITING_FOR_REVIEW
                    else CohortRecordingState.RUNNING
                )
                current = self.store.get(project_path, cohort_id)
                self._replace_recording(
                    project_path,
                    current,
                    initial.recording_path,
                    state=next_state,
                    child_run_id=child.id,
                )
                time.sleep(POLL_INTERVAL_SECONDS)
            self._clear_active(key)
            if cancel_flag.is_set():
                self._finish_cancelled(project_path, cohort_id)
                return

        current = self.store.get(project_path, cohort_id)
        try:
            summary, report = self._aggregate(project_path, current)
            summary_path, summary_sha, report_path, report_sha = self.store.write_results(
                project_path, cohort_id, summary, report
            )
            current = self.store.get(project_path, cohort_id)
            done = self._copy_counts(
                current.model_copy(
                    update={
                        "state": CohortRunState.SUCCEEDED,
                        "finished_at": utc_now_iso(),
                        "processed_participant_count": summary["denominators"][
                            "processed_participants"
                        ],
                        "report_path": report_path,
                        "report_sha256": report_sha,
                        "summary_path": summary_path,
                        "summary_sha256": summary_sha,
                    }
                )
            )
            self.store.save(
                project_path, CohortRunRecord.model_validate(done.model_dump(mode="json"))
            )
        except Exception as exc:
            current = self.store.get(project_path, cohort_id)
            if current.finished_at is None:
                failed = self._copy_counts(
                    current.model_copy(
                        update={
                            "state": CohortRunState.FAILED,
                            "finished_at": utc_now_iso(),
                            "failure_reason": (
                                f"Cohort aggregation failed: {type(exc).__name__}: {exc}"
                            ),
                        }
                    )
                )
                self.store.save(
                    project_path, CohortRunRecord.model_validate(failed.model_dump(mode="json"))
                )
        finally:
            with self._guard:
                self._threads.pop(key, None)
                self._cancel.pop(key, None)

    def _child_source_identity_failure(
        self, project_path: str, selected: CohortRecordingRun, child_record: Any
    ) -> str | None:
        """Bind completed child outputs to the identity captured at cohort selection."""

        inspect_runs = [
            item
            for item in child_record.node_runs
            if item.node_type == "eeg.inspect"
            and item.state in {NodeRunState.SUCCEEDED, NodeRunState.CACHE_REUSED}
        ]
        if len(inspect_runs) != 1:
            return (
                "Could not verify the source identity used by this recording's child workflow. "
                "Rescan the cohort and run the recording again."
            )
        reference = next(
            (item for item in inspect_runs[0].artifacts if item.port_id == "raw"), None
        )
        if reference is None:
            return (
                "Could not verify the source identity used by this recording's child workflow. "
                "Rescan the cohort and run the recording again."
            )
        try:
            with self.workers.runs.open_artifact_stream(
                project_path, child_record.id, reference.artifact_id
            )[0] as stream:
                payload = json.load(stream)
        except (OSError, ValueError, FileNotFoundError, json.JSONDecodeError):
            return (
                "Could not verify the source identity used by this recording's child workflow. "
                "Rescan the cohort and run the recording again."
            )
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "1.0"
            or payload.get("recording_path") != selected.recording_path
            or payload.get("source_content_identity") != selected.input_identity
        ):
            return (
                "The source recording changed after cohort selection; this child workflow used "
                "content different from the identity recorded for the selected recording. "
                "Rescan the cohort and run the recording again."
            )
        return None

    def _workflow_for_recording(self, workflow: Workflow, recording_path: str) -> Workflow:
        from brainlearn_core.schema import ParameterDefinition

        nodes = []
        updated = False
        for node in workflow.nodes:
            if node.type != "eeg.inspect":
                nodes.append(node)
                continue
            params = []
            found = False
            for parameter in node.parameters:
                if parameter.id == "recording_path":
                    params.append(parameter.model_copy(update={"value": recording_path}))
                    found = True
                else:
                    params.append(parameter)
            if not found:
                params.append(
                    ParameterDefinition(
                        id="recording_path",
                        label="Recording path",
                        value=recording_path,
                        required=True,
                    )
                )
            nodes.append(node.model_copy(update={"parameters": params}))
            updated = True
        if not updated:
            raise CohortExecutionError("The workflow needs one Inspect Signal node.")
        return workflow.model_copy(deep=True, update={"nodes": nodes})

    def _replace_recording(
        self,
        project_path: str,
        record: CohortRunRecord,
        recording_path: str,
        **updates: Any,
    ) -> CohortRunRecord:
        items = [
            item.model_copy(update=updates) if item.recording_path == recording_path else item
            for item in record.recordings
        ]
        updated = self._copy_counts(
            record.model_copy(
                update={
                    "recordings": items,
                    "state": (
                        CohortRunState.WAITING_FOR_REVIEW
                        if any(
                            item.state == CohortRecordingState.WAITING_FOR_REVIEW for item in items
                        )
                        else CohortRunState.RUNNING
                    ),
                }
            )
        )
        validated = CohortRunRecord.model_validate(updated.model_dump(mode="json"))
        return self.store.save(project_path, validated)

    @staticmethod
    def _copy_counts(record: CohortRunRecord) -> CohortRunRecord:
        return record.model_copy(
            update={
                "processed_count": sum(
                    item.state == CohortRecordingState.PROCESSED for item in record.recordings
                ),
                "excluded_count": sum(
                    item.state == CohortRecordingState.EXCLUDED for item in record.recordings
                ),
                "failed_count": sum(
                    item.state == CohortRecordingState.FAILED for item in record.recordings
                ),
                "cancelled_count": sum(
                    item.state == CohortRecordingState.CANCELLED for item in record.recordings
                ),
            }
        )

    def _finish_cancelled(self, project_path: str, cohort_id: str) -> None:
        current = self.store.get(project_path, cohort_id)
        active_child = None
        key = (str(Path(project_path).resolve()), cohort_id)
        with self._guard:
            active_child = self._active_children.get(key)
        if active_child:
            try:
                self.workers.cancel_run(project_path, active_child)
            except Exception:
                pass
        if current.finished_at is None:
            cancelled_items = [
                item.model_copy(
                    update={
                        "state": CohortRecordingState.CANCELLED,
                        "failure_reason": ("Cohort cancelled before this recording completed."),
                    }
                )
                if item.state
                in {
                    CohortRecordingState.SELECTED,
                    CohortRecordingState.RUNNING,
                    CohortRecordingState.WAITING_FOR_REVIEW,
                }
                else item
                for item in current.recordings
            ]
            cancelled = self._copy_counts(
                current.model_copy(
                    update={
                        "recordings": cancelled_items,
                        "state": CohortRunState.CANCELLED,
                        "finished_at": utc_now_iso(),
                    }
                )
            )
            self.store.save(
                project_path, CohortRunRecord.model_validate(cancelled.model_dump(mode="json"))
            )

    def _clear_active(self, key: tuple[str, str]) -> None:
        with self._guard:
            self._active_children.pop(key, None)

    def _aggregate(self, project_path: str, cohort: CohortRunRecord) -> tuple[dict[str, Any], str]:
        np = __import__("numpy")
        processed = [
            item for item in cohort.recordings if item.state == CohortRecordingState.PROCESSED
        ]
        if not processed:
            raise CohortExecutionError(
                "No recording succeeded; no cohort summary can be calculated. Review the "
                "per-recording failure and exclusion reasons."
            )
        records: list[dict[str, Any]] = []
        all_run_records = {}
        for item in processed:
            assert item.child_run_id is not None
            child = self.workers.runs.get_run(project_path, item.child_run_id)
            all_run_records[item.child_run_id] = child
            evoked = self._find_json_output(project_path, child, "eeg.erp_average", "evoked")
            spectrum = self._find_json_output(project_path, child, "eeg.psd", "spectrum")
            records.append({"selection": item, "run": child, "erp": evoked, "psd": spectrum})

        channel_order = records[0]["erp"]["channel_names"]
        common_channels = [
            name
            for name in channel_order
            if all(
                name in row["erp"]["channel_names"] and name in row["psd"]["channel_names"]
                for row in records
            )
        ]
        if not common_channels:
            raise CohortExecutionError(
                "Processed recordings have no common good EEG channels after the explicit QC "
                "decisions. Review compatible recordings or analyze them as separate cohorts; "
                "BrainLearn does not interpolate channels."
            )
        erp_axes = records[0]["erp"]["times_seconds"]
        psd_axes = records[0]["psd"]["frequencies_hz"]
        for row in records[1:]:
            if not np.allclose(row["erp"]["times_seconds"], erp_axes, rtol=0, atol=1e-12):
                raise CohortExecutionError("ERP time axes differ; use compatible epoch settings.")
            if not np.allclose(row["psd"]["frequencies_hz"], psd_axes, rtol=0, atol=1e-12):
                raise CohortExecutionError(
                    "PSD frequency axes differ; use compatible sample rates and settings."
                )

        by_participant: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            by_participant.setdefault(row["selection"].participant, []).append(row)
        participants = sorted(by_participant)
        erp_subject: list[Any] = []
        psd_subject: list[Any] = []
        subject_records = []
        for participant in participants:
            rows = by_participant[participant]
            erp_runs = []
            psd_runs = []
            for row in rows:
                erp_channels = row["erp"]["channel_names"]
                psd_channels = row["psd"]["channel_names"]
                erp_index = [erp_channels.index(channel) for channel in common_channels]
                psd_index = [psd_channels.index(channel) for channel in common_channels]
                erp_runs.append(np.asarray(row["erp"]["mean_uv"], dtype=float)[erp_index])
                psd_runs.append(
                    np.asarray(row["psd"]["mean_power_uv2_per_hz"], dtype=float)[psd_index]
                )
            # Multiple sessions/runs are first averaged within each participant;
            # the cohort mean and SD then use one equally weighted value per person.
            erp_subject.append(np.mean(np.stack(erp_runs), axis=0))
            psd_subject.append(np.mean(np.stack(psd_runs), axis=0))
            subject_records.append(
                {
                    "participant": participant,
                    "recording_paths": [row["selection"].recording_path for row in rows],
                    "recording_count": len(rows),
                    "epoch_count_by_recording": [row["erp"]["epoch_count"] for row in rows],
                }
            )
        erp_array = np.stack(erp_subject)
        psd_array = np.stack(psd_subject)
        erp_mean = erp_array.mean(axis=0)
        psd_mean = psd_array.mean(axis=0)
        erp_sd = erp_array.std(axis=0, ddof=1) if len(participants) > 1 else None
        psd_sd = psd_array.std(axis=0, ddof=1) if len(participants) > 1 else None

        excluded = [
            item for item in cohort.recordings if item.state == CohortRecordingState.EXCLUDED
        ]
        failed = [item for item in cohort.recordings if item.state == CohortRecordingState.FAILED]
        summary = {
            "schema_version": "1.0",
            "cohort_run_id": cohort.id,
            "scope": "descriptive EEG summaries; no hypothesis tests or clinical claims",
            "dataset": {
                "path": cohort.dataset_path,
                "dataset_id": cohort.dataset_id,
                "version": cohort.dataset_version,
            },
            "denominators": {
                "selected_recordings": cohort.selected_count,
                "processed_recordings": len(processed),
                "excluded_recordings": len(excluded),
                "failed_recordings": len(failed),
                "cancelled_recordings": sum(
                    item.state == CohortRecordingState.CANCELLED for item in cohort.recordings
                ),
                "processed_participants": len(participants),
            },
            "participants": subject_records,
            "excluded": [
                {
                    "recording_path": item.recording_path,
                    "participant": item.participant,
                    "reason": item.failure_reason,
                }
                for item in excluded
            ],
            "failed": [
                {
                    "recording_path": item.recording_path,
                    "participant": item.participant,
                    "reason": item.failure_reason,
                }
                for item in failed
            ],
            "analysis": {
                "channel_names": common_channels,
                "channels_not_in_every_processed_recording": sorted(
                    {channel for row in records for channel in row["erp"]["channel_names"]}
                    - set(common_channels)
                ),
                "erp": {
                    "times_seconds": erp_axes,
                    "mean_uv_across_participants": erp_mean.tolist(),
                    "sample_sd_uv_across_participants": erp_sd.tolist()
                    if erp_sd is not None
                    else None,
                    "participant_count": len(participants),
                },
                "psd": {
                    "frequencies_hz": psd_axes,
                    "mean_power_uv2_per_hz_across_participants": psd_mean.tolist(),
                    "sample_sd_uv2_per_hz_across_participants": psd_sd.tolist()
                    if psd_sd is not None
                    else None,
                    "participant_count": len(participants),
                    "estimator": records[0]["psd"]["estimator"],
                },
            },
            "methods": {
                "workflow_id": cohort.workflow_id,
                "workflow_identity": cohort.workflow_identity,
                "parameters_by_node": cohort.parameters,
                "parameters_by_recording": {
                    item.recording_path: item.parameters for item in cohort.recordings
                },
                "run_ids": [item.child_run_id for item in processed],
                "qc_review_ids_by_recording": {
                    item.recording_path: item.qc_review_ids for item in processed
                },
                "software_environments": {
                    run_id: run.environment.model_dump(mode="json")
                    for run_id, run in all_run_records.items()
                },
                "seed_by_recording": {
                    item.recording_path: all_run_records[item.child_run_id].seed
                    for item in processed
                    if item.child_run_id is not None
                },
                "human_review_placeholders": [
                    "Add an independent methods reviewer, date, and decision.",
                    "Confirm event-label interpretation and provisional method settings.",
                    "Document additional exclusions and the intended descriptive scope.",
                ],
            },
            "citations": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self._executed_citations(cohort, all_run_records)
            ],
            "source_identities": {
                item.recording_path: {
                    "content_identity": item.input_identity,
                    "files": [file.model_dump(mode="json") for file in item.input_files],
                }
                for item in cohort.recordings
            },
        }
        report = self._render_cohort_report(summary)
        return summary, report

    @staticmethod
    def _executed_citations(
        cohort: CohortRunRecord, run_records: dict[str, Any]
    ) -> list[CitationMetadata]:
        node_types = {
            node.node_type
            for run in run_records.values()
            for node in run.node_runs
            if node.state in {NodeRunState.SUCCEEDED, NodeRunState.CACHE_REUSED}
        }
        citations: list[CitationMetadata] = []
        for node_type in sorted(node_types):
            manifest = NODE_REGISTRY_BY_ID.get(node_type)
            if manifest is None:
                continue
            citations.extend(manifest.citations)
            for dependency in manifest.software_dependencies:
                citations.extend(dependency.citations)
        if cohort.dataset_id == "ds005284":
            citations.append(
                CitationMetadata(
                    title="26 By Biosemi",
                    doi="10.18112/openneuro.ds005284.v1.0.0",
                    url="https://openneuro.org/datasets/ds005284/versions/1.0.0",
                )
            )
        return CohortRunService._deduplicate_citations(citations)

    def _find_json_output(
        self,
        project_path: str,
        run: Any,
        node_type: str,
        port_id: str,
        *,
        decode_json: bool = True,
    ) -> Any:
        for node in run.node_runs:
            if node.node_type != node_type or node.state != NodeRunState.SUCCEEDED:
                continue
            artifact = next((item for item in node.artifacts if item.port_id == port_id), None)
            if artifact is None:
                continue
            with self.workers.runs.open_artifact_stream(project_path, run.id, artifact.artifact_id)[
                0
            ] as stream:
                data = stream.read()
            if not decode_json:
                return data.decode("utf-8", errors="replace")
            try:
                value = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CohortExecutionError(
                    f"The {node_type} output {port_id!r} is not valid JSON."
                ) from exc
            if not isinstance(value, dict):
                raise CohortExecutionError(
                    f"The {node_type} output {port_id!r} has an invalid structure."
                )
            return value
        raise CohortExecutionError(
            f"The successful recording run has no {node_type}.{port_id} artifact."
        )

    def _render_cohort_report(self, summary: dict[str, Any]) -> str:
        safe = html.escape
        denominators = summary["denominators"]
        failed_rows = (
            "".join(
                f"<li>{safe(item['participant'])} · {safe(item['recording_path'])}: "
                f"{safe(item['reason'] or '')}</li>"
                for item in summary["failed"]
            )
            or "<li>None</li>"
        )
        excluded_rows = (
            "".join(
                f"<li>{safe(item['participant'])} · {safe(item['recording_path'])}: "
                f"{safe(item['reason'] or '')}</li>"
                for item in summary["excluded"]
            )
            or "<li>None</li>"
        )
        participant_rows = "".join(
            f"<li>{safe(item['participant'])}: {item['recording_count']} recording(s), "
            f"epoch counts {safe(', '.join(map(str, item['epoch_count_by_recording'])))}.</li>"
            for item in summary["participants"]
        )
        citation_rows = (
            "".join(
                "<li>"
                + safe(item.get("title", ""))
                + (f" · DOI: {safe(item['doi'])}" if item.get("doi") else "")
                + (f' · <a href="{safe(item["url"])}">source</a>' if item.get("url") else "")
                + "</li>"
                for item in summary["citations"]
            )
            or "<li>No citations were recorded for this run.</li>"
        )
        methods_json = safe(
            json.dumps(summary["methods"], ensure_ascii=False, sort_keys=True, indent=2)
        )
        parameters_json = safe(
            json.dumps(summary["analysis"]["psd"]["estimator"], sort_keys=True, indent=2)
        )
        return "\n".join(
            [
                "<!doctype html>",
                '<html lang="en"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                "<title>BrainLearn EEG cohort report</title>",
                "<style>body{font:16px/1.55 system-ui,sans-serif;max-width:1100px;",
                "margin:2rem auto;padding:0 1rem;color:#18232b}pre{overflow:auto;",
                "background:#f4f7f8;padding:1rem;border-radius:.5rem}.notice{",
                "background:#fff4d8;padding:1rem;border-left:4px solid #bd8400}</style>",
                "</head><body>",
                "<h1>Descriptive EEG cohort report</h1>",
                '<p class="notice">Research output only. Provisional settings; no independent '
                "EEG methods approval. No clinical or hypothesis-testing claims.</p>",
                "<h2>Run denominators</h2>",
                f"<p>Selected recordings: {denominators['selected_recordings']}; "
                f"processed: {denominators['processed_recordings']}; "
                f"excluded: {denominators['excluded_recordings']}; "
                f"failed: {denominators['failed_recordings']}; "
                f"processed participants: {denominators['processed_participants']}.</p>",
                f"<h2>Processed participants</h2><ul>{participant_rows}</ul>",
                f"<h2>Exclusions</h2><ul>{excluded_rows}</ul>",
                f"<h2>Failures</h2><ul>{failed_rows}</ul>",
                "<h2>Descriptive summaries</h2>",
                "<p>ERP and Welch PSD means and sample standard deviations are in the summary "
                "JSON. Each participant contributes one equally weighted value after averaging "
                "multiple sessions/runs within participant. A one-participant standard deviation "
                "is undefined and recorded as null. Channels absent from a processed recording "
                "were omitted; no interpolation was performed.</p>",
                "<p>PSD settings actually used:</p>",
                f"<pre>{parameters_json}</pre>",
                f"<h2>Methods and provenance</h2><pre>{methods_json}</pre>",
                "<h2>Human review placeholders</h2><ul>",
                "<li>Add independent methods reviewer, date, and decision.</li>",
                "<li>Confirm event-label meaning and provisional settings.</li>",
                "<li>Document intended use and limitations before sharing.</li></ul>",
                f"<h2>Citations for executed methods</h2><ul>{citation_rows}</ul>",
                "</body></html>",
            ]
        )
