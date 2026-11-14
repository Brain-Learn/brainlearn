"""Portable, deterministic exports of completed BrainLearn EEG cohort runs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from importlib.resources import files
from typing import Any

from brainlearn_core import CohortRunRecord, QCReviewRecord, RunRecord


class CohortExportError(ValueError):
    """A cohort cannot be represented safely as a portable project."""


_ABSOLUTE_PATH = re.compile(
    r"(?<![:\w])(?:/Users/|/home/|/private/|/Volumes/|/var/folders/|/tmp/|/mnt/|/media/|"
    r"/workspace/|[A-Za-z]:[\\/])[^\s\"']+"
)
_SECRET = re.compile(
    r"(?i)(?:BRAINLEARN_SESSION_TOKEN\s*[=:]\s*\S+|"
    r"(?:authorization|session[_ -]?token|api[_ -]?key)\s*[=:]\s*\S{12,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})"
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _relative_path(value: str, label: str) -> str:
    from pathlib import PurePosixPath, PureWindowsPath

    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise CohortExportError(
            f"{label} must be a safe project-relative path before export; got {value!r}."
        )
    return posix.as_posix()


def _redact_failure(value: str | None) -> str | None:
    if value is None:
        return None
    return _ABSOLUTE_PATH.sub("[local path]", value)


def _workflow_parameters(cohort: CohortRunRecord) -> dict[str, dict[str, Any]]:
    if cohort.workflow is None:
        raise CohortExportError("This cohort has no saved workflow snapshot to export.")
    result: dict[str, dict[str, Any]] = {}
    for node in cohort.workflow.nodes:
        values = {parameter.id: parameter.value for parameter in node.parameters}
        for key in ("root", "recording_path"):
            item = values.get(key)
            if isinstance(item, str) and item:
                _relative_path(item, f"Workflow {node.type} {key}")
        result[node.id] = values
    return result


def _project_snapshot(
    cohort: CohortRunRecord,
    run_records: dict[str, RunRecord],
    qc_records: dict[str, list[QCReviewRecord]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    workflow = cohort.workflow
    if workflow is None:
        raise CohortExportError("This cohort has no saved workflow snapshot to export.")
    parameters = _workflow_parameters(cohort)
    node_types = {node.id: node.type for node in workflow.nodes}
    recordings: list[dict[str, Any]] = []
    run_provenance: list[dict[str, Any]] = []
    for item in cohort.recordings:
        relative_recording = _relative_path(item.recording_path, "Recording path")
        child = run_records.get(item.child_run_id or "")
        decisions: dict[str, Any] = {}
        for review in qc_records.get(item.child_run_id or "", []):
            node_type = node_types.get(review.node_id, "unknown")
            review_node = (
                next(
                    (node for node in child.node_runs if node.id == review.node_run_id),
                    None,
                )
                if child is not None
                else None
            )
            expected_inputs = dict(review_node.inputs) if review_node is not None else None
            recorded_inputs = {
                entry.port_id: entry.content_identity for entry in review.input_identities
            }
            expected_artifacts: dict[str, str] = {}
            if node_type == "eeg.ica_review" and child is not None and review_node is not None:
                incoming = [
                    edge for edge in workflow.edges if edge.target.node_id == review.node_id
                ]
                for edge in incoming:
                    source_run = next(
                        (node for node in child.node_runs if node.node_id == edge.source.node_id),
                        None,
                    )
                    artifact = (
                        next(
                            (
                                item
                                for item in source_run.artifacts
                                if item.port_id == edge.source.port_id
                            ),
                            None,
                        )
                        if source_run is not None
                        else None
                    )
                    if artifact is not None:
                        expected_artifacts[edge.target.port_id] = artifact.artifact_id
            recorded_artifacts = {
                entry.port_id: entry.artifact_id for entry in review.input_artifact_identities
            }
            if (
                child is None
                or review.run_id != child.id
                or review_node is None
                or review_node.node_id != review.node_id
                or review_node.content_identity != review.node_content_identity
                or expected_inputs != recorded_inputs
                or (node_type == "eeg.ica_review" and expected_artifacts != recorded_artifacts)
                or review.id not in item.qc_review_ids
            ):
                raise CohortExportError(
                    f"A persisted QC record for {item.recording_path} is stale or does not match "
                    "its run; reopen and review that recording."
                )
            if (
                node_type == "eeg.bad_channel_review"
                and review.source_content_identity != item.input_identity
            ):
                raise CohortExportError(
                    f"The bad-channel decision for {item.recording_path} no longer matches its "
                    "source identity; rescan and review it again."
                )
            decision: dict[str, Any] = {
                "review_id": review.id,
                "node_type": node_type,
                "decision": review.decision,
                "reviewer": review.reviewer,
                "note": _redact_failure(review.note) or "",
                "created_at": review.created_at,
                "source_content_identity": review.source_content_identity,
                "excluded_channel_names": list(review.excluded_channel_names),
                "excluded_ica_components": list(review.excluded_ica_components),
                "input_identities": [
                    entry.model_dump(mode="json") for entry in review.input_identities
                ],
                "input_artifact_identities": [
                    entry.model_dump(mode="json") for entry in review.input_artifact_identities
                ],
            }
            decisions[node_type] = decision

        node_settings: dict[str, dict[str, Any]] = {}
        node_seeds: dict[str, int | None] = {}
        executed_nodes: list[dict[str, Any]] = []
        if child is not None:
            for node in child.node_runs:
                safe_parameters = dict(node.parameters)
                for key in ("root", "recording_path"):
                    value = safe_parameters.get(key)
                    if isinstance(value, str) and value:
                        _relative_path(value, f"Run {node.node_type} {key}")
                node_settings[node.node_type] = safe_parameters
                node_seeds[node.node_type] = node.seed
                executed_nodes.append(
                    {
                        "node_id": node.node_id,
                        "node_type": node.node_type,
                        "node_version": node.node_version,
                        "state": node.state.value,
                        "attempt": node.attempt,
                        "dependencies": node.dependencies,
                        "started_at": node.started_at,
                        "finished_at": node.finished_at,
                        "environment_identity": node.environment_identity,
                        "content_identity": node.content_identity,
                        "seed": node.seed,
                        "parameters": safe_parameters,
                        "settings": node.settings,
                        "inputs": node.inputs,
                        "failure": (
                            {
                                "code": node.failure.code,
                                "message": _redact_failure(node.failure.message),
                            }
                            if node.failure
                            else None
                        ),
                        "artifacts": [
                            {
                                "artifact_id": artifact.artifact_id,
                                "port_id": artifact.port_id,
                                "path": _relative_path(artifact.path, "Artifact path"),
                                "media_type": artifact.media_type,
                                "byte_size": artifact.byte_size,
                                "sha256": artifact.sha256,
                            }
                            for artifact in node.artifacts
                        ],
                    }
                )
            run_provenance.append(
                {
                    "run_id": child.id,
                    "state": child.state.value,
                    "created_at": child.created_at,
                    "started_at": child.started_at,
                    "finished_at": child.finished_at,
                    "workflow_identity": child.workflow_identity,
                    "seed": child.seed,
                    "environment": child.environment.model_dump(mode="json"),
                    "failure": (
                        {
                            "code": child.failure.code,
                            "message": _redact_failure(child.failure.message),
                        }
                        if child.failure
                        else None
                    ),
                    "nodes": executed_nodes,
                    "qc_decisions": list(decisions.values()),
                }
            )

        input_files = [file.model_dump(mode="json") for file in item.input_files]
        for source in input_files:
            source["path"] = _relative_path(source["path"], "Input file path")
        recordings.append(
            {
                "recording_path": relative_recording,
                "participant": item.participant,
                "session": item.session,
                "run": item.run,
                "original_state": item.state.value,
                "input_identity": item.input_identity,
                "input_files": input_files,
                "failure_reason": _redact_failure(item.failure_reason),
                "child_run_id": item.child_run_id,
                "parameters": item.parameters,
                "workflow_parameters_by_node": node_settings or parameters,
                "node_seeds": node_seeds,
                "qc_decisions": decisions,
                "outputs": [output.model_dump(mode="json") for output in item.outputs],
            }
        )

    source = {
        "format_version": "1.0",
        "cohort_id": cohort.id,
        "state": cohort.state.value,
        "created_at": cohort.created_at,
        "started_at": cohort.started_at,
        "finished_at": cohort.finished_at,
        "dataset_id": cohort.dataset_id,
        "dataset_version": cohort.dataset_version,
        "dataset_path_in_original_project": _relative_path(cohort.dataset_path, "Dataset path"),
        "workflow_id": cohort.workflow_id,
        "workflow_identity": cohort.workflow_identity,
        "seed": cohort.seed,
        "parameters": cohort.parameters,
        "counts": {
            "selected": cohort.selected_count,
            "processed": cohort.processed_count,
            "excluded": cohort.excluded_count,
            "failed": cohort.failed_count,
            "cancelled": cohort.cancelled_count,
            "processed_participants": cohort.processed_participant_count,
        },
        "failure_reason": _redact_failure(cohort.failure_reason),
        "recordings": recordings,
    }
    provenance = {
        "schema_version": "1.0",
        "source_cohort": source,
        "workflow_snapshot": workflow.model_dump(mode="json"),
        "child_runs": run_provenance,
        "replay_claims": {
            "workflow_parameters_recorded_exactly": True,
            "source_files_verified_by_sha256": True,
            "saved_bad_channel_decisions_are_reused_only_after_explicit_acknowledgement": True,
            "saved_ica_decisions_are_documentary_and_must_be_reviewed_again_after_refitting": True,
            "source_environment_records": (
                "actual run environment fields are preserved per child run"
            ),
            "floating_point_outputs": "tolerance-based; see README.md",
        },
        "citations": [item.model_dump(mode="json") for item in cohort.citations],
    }
    return source, provenance


def _check_no_secrets(files_by_name: dict[str, bytes]) -> None:
    for name, content in files_by_name.items():
        value = content.decode("utf-8", errors="ignore")
        if _SECRET.search(value):
            raise CohortExportError(
                f"The exported {name} appears to contain a secret or session token; "
                "remove it before export."
            )
        if _ABSOLUTE_PATH.search(value):
            raise CohortExportError(
                f"The exported {name} contains an absolute personal path; "
                "replace it with a relative path before export."
            )


def _notebook() -> str:
    return (
        files("brainlearn_server")
        .joinpath("templates", "eeg_cohort", "analysis.ipynb")
        .read_text(encoding="utf-8")
    )


def _workflow_script() -> str:
    return (
        files("brainlearn_server")
        .joinpath("templates", "eeg_cohort", "workflow.py.tmpl")
        .read_text(encoding="utf-8")
    )


def _runner_script() -> str:
    return (
        files("brainlearn_server")
        .joinpath("templates", "eeg_cohort", "replay_brainlearn.py.tmpl")
        .read_text(encoding="utf-8")
    )


def _readme(cohort: CohortRunRecord) -> str:
    template = (
        files("brainlearn_server")
        .joinpath("templates", "eeg_cohort", "README.md")
        .read_text(encoding="utf-8")
    )
    return template.replace("{cohort_id}", cohort.id)


def build_cohort_project_archive(
    cohort: CohortRunRecord,
    run_records: dict[str, RunRecord],
    qc_records: dict[str, list[QCReviewRecord]],
) -> bytes:
    """Build a deterministic project archive without data, artifacts, or credentials."""

    if cohort.state.value in {"running", "waiting_for_review"}:
        raise CohortExportError(
            "Wait for the cohort run to finish before exporting its Python project."
        )
    source, provenance = _project_snapshot(cohort, run_records, qc_records)
    workflow = cohort.workflow
    assert workflow is not None
    workflow_json = _json(workflow.model_dump(mode="json"))
    config = {
        "format_version": "1.0",
        "source_cohort": {
            "cohort_id": cohort.id,
            "state": cohort.state.value,
            "dataset_id": cohort.dataset_id,
            "dataset_version": cohort.dataset_version,
            "dataset_root_hint": "./dataset",
            "seed": cohort.seed,
            "workflow_identity": cohort.workflow_identity,
        },
        "workflow": workflow.model_dump(mode="json"),
        "parameters": _workflow_parameters(cohort),
        "recordings": source["recordings"],
        "citations": [item.model_dump(mode="json") for item in cohort.citations],
        "runner_environment": {
            key: value
            for record in run_records.values()
            for key, value in record.environment.packages.items()
            if key == "brainlearn-server"
        },
    }
    lock = files("brainlearn_server").joinpath("eeg-cohort.lock").read_bytes()
    lock_digest = hashlib.sha256(lock).hexdigest()
    config["direct_replay_environment"] = {
        "python": ">=3.12,<3.13",
        "lockfile": "environment.lock",
        "lockfile_sha256": lock_digest,
    }
    provenance["direct_replay_environment"] = config["direct_replay_environment"]
    export_files: dict[str, bytes] = {
        "README.md": _readme(cohort).encode(),
        "config.json": _json(config).encode(),
        "workflow.json": workflow_json.encode(),
        "workflow.py": _workflow_script().encode(),
        "replay_brainlearn.py": _runner_script().encode(),
        "analysis.ipynb": _notebook().encode(),
        "environment.lock": lock,
        "provenance.json": _json(provenance).encode(),
        "citations.json": _json(config["citations"]).encode(),
        "expected_outputs.md": (
            b"# Expected outputs\n\n"
            b"`workflow.py` creates one JSON result per successfully replayed participant, a "
            b"`summary.json` with processed/excluded/failed denominators and participant-level "
            b"mean/SD, and a self-contained `report.html`. It creates outputs only in a new "
            b"directory and refuses to overwrite an existing one. Original BrainLearn artifact "
            b"identities remain in `provenance.json`; large source and output data are not "
            b"bundled.\n"
        ),
    }
    _check_no_secrets(export_files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(export_files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(
                info,
                export_files[name],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return buffer.getvalue()
