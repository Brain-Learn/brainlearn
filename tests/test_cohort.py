"""Cohort schema, compatibility, and partial-result denominator checks."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from brainlearn_core import (
    CohortInputFile,
    CohortRecordingRun,
    CohortRecordingState,
    CohortRunRecord,
    CohortRunState,
    NodeRunState,
    Workflow,
    migrate_cohort_run_dict,
    migrate_workflow_dict,
    validate_workflow,
)
from brainlearn_server.bids_eeg import BidsEegRecording
from brainlearn_server.cohort import CohortExecutionError, CohortRunService
from brainlearn_server.registry import NODE_REGISTRY_BY_ID
from brainlearn_server.worker import build_run_record, workflow_identity

ROOT = Path(__file__).parents[1]
EEG_WORKFLOW_PATH = ROOT / "examples" / "eeg-first-look.workflow.json"
SOURCE_SHA = "a" * 64
SOURCE_ID = f"brainlearn-v1:artifact:{SOURCE_SHA}"


def _workflow() -> Workflow:
    payload = json.loads(EEG_WORKFLOW_PATH.read_text(encoding="utf-8"))
    return Workflow.model_validate(migrate_workflow_dict(payload))


def _recording(
    path: str,
    participant: str,
    state: CohortRecordingState,
    *,
    child_run_id: str | None = None,
    reason: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> CohortRecordingRun:
    has_source = not (state == CohortRecordingState.EXCLUDED and child_run_id is None)
    return CohortRecordingRun(
        recording_path=path,
        participant=participant,
        session=None,
        run=None,
        state=state,
        input_identity=SOURCE_ID if has_source else None,
        input_files=(
            [CohortInputFile(path=f"{path}/recording.edf", byte_size=4, sha256=SOURCE_SHA)]
            if has_source
            else []
        ),
        child_run_id=child_run_id,
        parameters=parameters or {},
        qc_review_ids=[f"qc-{child_run_id}"] if child_run_id else [],
        failure_reason=reason,
    )


def _cohort_record(workflow: Workflow, recordings: list[CohortRecordingRun]) -> CohortRunRecord:
    now = "2026-10-28T08:00:00+00:00"
    return CohortRunRecord(
        schema_version="1.2",
        id="cohort-test-001",
        state=CohortRunState.RUNNING,
        dataset_path="datasets/ds005284",
        dataset_id="ds005284",
        dataset_version="1.0.0",
        workflow_id=workflow.id,
        workflow_identity=workflow_identity(workflow),
        workflow=workflow,
        created_at=now,
        started_at=now,
        seed=7,
        parameters={"epochs": {"event": "target"}},
        recordings=recordings,
        selected_count=len(recordings),
        processed_count=sum(item.state == CohortRecordingState.PROCESSED for item in recordings),
        excluded_count=sum(item.state == CohortRecordingState.EXCLUDED for item in recordings),
        failed_count=sum(item.state == CohortRecordingState.FAILED for item in recordings),
        cancelled_count=sum(item.state == CohortRecordingState.CANCELLED for item in recordings),
        processed_participant_count=0,
        citations=[],
    )


def test_cohort_schema_round_trip_and_v1_0_migration() -> None:
    workflow = _workflow()
    record = _cohort_record(
        workflow,
        [_recording("sub-001/eeg/a", "sub-001", CohortRecordingState.SELECTED)],
    )
    payload = record.model_dump(mode="json")
    assert CohortRunRecord.model_validate(payload) == record

    legacy = dict(payload)
    legacy["schema_version"] = "1.0"
    legacy.pop("cancelled_count")
    legacy.pop("workflow")
    migrated = CohortRunRecord.model_validate(migrate_cohort_run_dict(legacy))
    assert migrated.schema_version == "1.2"
    assert migrated.cancelled_count == 0
    assert migrated.workflow is None


def test_workflow_example_has_explicit_epoch_and_psd_parameters() -> None:
    workflow = _workflow()
    assert validate_workflow(workflow, NODE_REGISTRY_BY_ID).valid
    values = {
        node.type: {item.id: item.value for item in node.parameters} for node in workflow.nodes
    }
    assert values["eeg.epochs"] == {
        "event": "condition 54",
        "tmin": -0.2,
        "tmax": 0.8,
        "baseline_enabled": True,
        "baseline_start": -0.2,
        "baseline_end": 0.0,
        "reject_eeg_peak_to_peak_uv": 0.0,
        "reject_by_annotation": True,
    }
    assert values["eeg.psd"]["window"] == "hamming"


def test_cohort_template_requires_the_qc_and_analysis_connections() -> None:
    workflow = _workflow()
    service = object.__new__(CohortRunService)
    broken = workflow.model_copy(
        update={"edges": [edge for edge in workflow.edges if edge.id != "e3"]}
    )
    with pytest.raises(CohortExecutionError, match="invalid|connections"):
        service._validate_template(broken, "sub-001/eeg/sub-001_task-x_eeg.edf")


def test_metadata_incompatibility_rejects_channel_or_rate_mismatch() -> None:
    reference = BidsEegRecording(
        path="sub-001/eeg/a.edf",
        subject="001",
        task="task",
        status="ready",
        sampling_frequency_hz=500,
        eeg_channel_count=2,
        channel_names=("Cz", "Pz"),
    )
    different_rate = reference.model_copy(
        update={"path": "sub-002/eeg/b.edf", "subject": "002", "sampling_frequency_hz": 250}
    )
    different_channels = reference.model_copy(
        update={"path": "sub-003/eeg/c.edf", "subject": "003", "channel_names": ("Cz", "Fz")}
    )
    epoched = reference.model_copy(update={"recording_type": "epoched"})
    assert "does not resample" in CohortRunService._metadata_incompatibility(
        different_rate, reference
    )
    assert "does not interpolate" in CohortRunService._metadata_incompatibility(
        different_channels, reference
    )
    assert "requires continuous EEG" in CohortRunService._metadata_incompatibility(
        epoched, reference
    )


def test_completed_recording_must_match_its_cohort_source_identity() -> None:
    selected = _recording(
        "sub-001/eeg/sub-001_task-x_eeg.edf",
        "001",
        CohortRecordingState.SELECTED,
    )
    reference_artifact = SimpleNamespace(port_id="raw", artifact_id="artifact-inspect-raw")
    inspect_node = SimpleNamespace(
        node_type="eeg.inspect",
        state=NodeRunState.SUCCEEDED,
        artifacts=[reference_artifact],
    )
    child = SimpleNamespace(id="run-child-001", node_runs=[inspect_node])

    class FakeRuns:
        source_identity = SOURCE_ID

        def open_artifact_stream(self, _project: str, _run_id: str, _artifact_id: str):
            payload = json.dumps(
                {
                    "schema_version": "1.0",
                    "recording_path": selected.recording_path,
                    "source_content_identity": self.source_identity,
                }
            ).encode()
            return BytesIO(payload), reference_artifact

    service = object.__new__(CohortRunService)
    service.workers = SimpleNamespace(runs=FakeRuns())

    assert service._child_source_identity_failure("/project", selected, child) is None
    service.workers.runs.source_identity = "brainlearn-v1:artifact:" + "b" * 64
    reason = service._child_source_identity_failure("/project", selected, child)
    assert reason is not None
    assert "changed after cohort selection" in reason


def test_partial_cohort_summary_has_correct_denominators_and_report_reasons() -> None:
    workflow = _workflow()
    paths = ["sub-001/eeg/run-1", "sub-001/eeg/run-2", "sub-002/eeg/run-1"]
    items = [
        _recording(
            paths[0],
            "sub-001",
            CohortRecordingState.PROCESSED,
            child_run_id="run-1",
            parameters={"epochs": {"event": "target"}},
        ),
        _recording(
            paths[1],
            "sub-001",
            CohortRecordingState.PROCESSED,
            child_run_id="run-2",
            parameters={"epochs": {"event": "target"}},
        ),
        _recording(
            paths[2],
            "sub-002",
            CohortRecordingState.PROCESSED,
            child_run_id="run-3",
            parameters={"epochs": {"event": "target"}},
        ),
        _recording(
            "sub-004/eeg/run-1",
            "sub-004",
            CohortRecordingState.EXCLUDED,
            reason="preflight: incompatible channel metadata",
        ),
        _recording(
            "sub-005/eeg/run-1",
            "sub-005",
            CohortRecordingState.FAILED,
            child_run_id="run-5",
            reason="MNE could not read the recording",
        ),
    ]
    cohort = _cohort_record(workflow, items)
    service_for_workflow = object.__new__(CohortRunService)
    per_recording_workflow = service_for_workflow._workflow_for_recording(workflow, paths[0])
    base_run = build_run_record(per_recording_workflow, seed=2)

    class FakeRuns:
        @staticmethod
        def get_run(_project: str, run_id: str) -> Any:
            return SimpleNamespace(
                id=run_id,
                seed=int(run_id[-1]),
                environment=base_run.environment,
                node_runs=[
                    SimpleNamespace(node_type="eeg.erp_average", state=NodeRunState.SUCCEEDED),
                    SimpleNamespace(node_type="eeg.psd", state=NodeRunState.SUCCEEDED),
                ],
            )

    service = object.__new__(CohortRunService)
    service.workers = SimpleNamespace(runs=FakeRuns())
    service._find_json_output = lambda _project, run, node_type, _port: (
        {
            "channel_names": ["Cz", "Pz"],
            "times_seconds": [-0.1, 0.0],
            "epoch_count": 2,
            "mean_uv": [[float(run.seed), float(run.seed + 1)], [2.0, 3.0]],
        }
        if node_type == "eeg.erp_average"
        else {
            "channel_names": ["Cz", "Pz"],
            "frequencies_hz": [1.0, 2.0],
            "mean_power_uv2_per_hz": [[float(run.seed), float(run.seed + 1)], [2.0, 3.0]],
            "estimator": {"method": "welch", "window": "hamming"},
        }
    )

    summary, report = service._aggregate("/tmp/project", cohort)

    assert summary["denominators"] == {
        "selected_recordings": 5,
        "processed_recordings": 3,
        "excluded_recordings": 1,
        "failed_recordings": 1,
        "cancelled_recordings": 0,
        "processed_participants": 2,
    }
    assert summary["analysis"]["erp"]["participant_count"] == 2
    assert summary["analysis"]["erp"]["mean_uv_across_participants"][0][0] == 2.25
    assert summary["methods"]["parameters_by_recording"][paths[0]]["epochs"]["event"] == "target"
    assert "Selected recordings: 5" in report
    assert "incompatible channel metadata" in report
    assert "MNE could not read the recording" in report
    assert "Methods and provenance" in report
    assert "Human review placeholders" in report
    assert "Citations for executed methods" in report
    assert summary["methods"]["human_review_placeholders"]
    assert len(summary["citations"]) == len(
        {item.get("doi") or item.get("url") or item["title"] for item in summary["citations"]}
    )
