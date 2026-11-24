"""Persisted cohort-run records that group replayable per-recording runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brainlearn_core.execution import _ensure_iso_timestamp, _require_chronology
from brainlearn_core.schema import CitationMetadata, Workflow

COHORT_RUN_SCHEMA_VERSION: Literal["1.2"] = "1.2"
SUPPORTED_COHORT_RUN_VERSIONS: tuple[str, ...] = ("1.0", "1.1", "1.2")


class CohortRunState(StrEnum):
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CohortRecordingState(StrEnum):
    SELECTED = "selected"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    PROCESSED = "processed"
    EXCLUDED = "excluded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CohortInputFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CohortOutputReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    port_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CohortRecordingRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_path: str = Field(min_length=1)
    participant: str = Field(min_length=1)
    session: str | None = None
    run: str | None = None
    state: CohortRecordingState = CohortRecordingState.SELECTED
    input_identity: str | None = Field(
        default=None, pattern=r"^brainlearn-v1:artifact:[0-9a-f]{64}$"
    )
    input_files: list[CohortInputFile] = Field(default_factory=list)
    child_run_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    qc_review_ids: list[str] = Field(default_factory=list)
    outputs: list[CohortOutputReference] = Field(default_factory=list)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_record_state(self) -> CohortRecordingRun:
        if self.input_identity is not None and not self.input_files:
            raise ValueError("A cohort input identity requires the source-file hashes.")
        if (
            self.state
            in {
                CohortRecordingState.PROCESSED,
                CohortRecordingState.EXCLUDED,
                CohortRecordingState.FAILED,
                CohortRecordingState.CANCELLED,
            }
            and self.state != CohortRecordingState.PROCESSED
            and not self.failure_reason
        ):
            raise ValueError(f"A {self.state.value} cohort recording requires a reason.")
        if self.state == CohortRecordingState.PROCESSED and self.failure_reason is not None:
            raise ValueError("A processed cohort recording cannot have a failure reason.")
        if (
            self.state
            in {
                CohortRecordingState.RUNNING,
                CohortRecordingState.WAITING_FOR_REVIEW,
                CohortRecordingState.PROCESSED,
            }
            and self.child_run_id is None
        ):
            raise ValueError(f"A {self.state.value} recording requires a child run ID.")
        if self.state != CohortRecordingState.PROCESSED and self.outputs:
            raise ValueError("Only a processed recording may list successful output artifacts.")
        return self


class CohortRunRecord(BaseModel):
    """One selected cohort run, with one durable child workflow run per recording."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.2"] = COHORT_RUN_SCHEMA_VERSION
    id: str = Field(pattern=r"^cohort-[A-Za-z0-9][A-Za-z0-9._-]*$")
    state: CohortRunState
    dataset_path: str = Field(min_length=1)
    dataset_id: str | None = None
    dataset_version: str | None = None
    workflow_id: str = Field(min_length=1)
    workflow_identity: str = Field(pattern=r"^brainlearn-v1:workflow:[0-9a-f]{64}$")
    workflow: Workflow | None = None
    created_at: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    finished_at: str | None = None
    seed: int = Field(ge=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    recordings: list[CohortRecordingRun] = Field(min_length=1)
    selected_count: int = Field(ge=1)
    processed_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    cancelled_count: int = Field(ge=0)
    processed_participant_count: int = Field(ge=0)
    report_path: str | None = None
    report_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    summary_path: str | None = None
    summary_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    citations: list[CitationMetadata] = Field(default_factory=list)
    failure_reason: str | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def timestamps_must_be_iso(cls, value: str | None) -> str | None:
        return _ensure_iso_timestamp(value, "cohort run timestamp")

    @model_validator(mode="after")
    def validate_counts_and_lifecycle(self) -> CohortRunRecord:
        _require_chronology(self.created_at, self.started_at, "created_at", "started_at")
        _require_chronology(self.started_at, self.finished_at, "started_at", "finished_at")
        if self.selected_count != len(self.recordings):
            raise ValueError("Cohort selected_count must equal the recording selection size.")
        state_counts = {
            state: sum(item.state == state for item in self.recordings)
            for state in CohortRecordingState
        }
        if self.processed_count != state_counts[CohortRecordingState.PROCESSED]:
            raise ValueError("Cohort processed_count does not match its processed recordings.")
        if self.excluded_count != state_counts[CohortRecordingState.EXCLUDED]:
            raise ValueError("Cohort excluded_count does not match its excluded recordings.")
        if self.failed_count != state_counts[CohortRecordingState.FAILED]:
            raise ValueError("Cohort failed_count does not match its failed recordings.")
        if self.cancelled_count != state_counts[CohortRecordingState.CANCELLED]:
            raise ValueError("Cohort cancelled_count does not match its cancelled recordings.")
        if (
            self.processed_count + self.excluded_count + self.failed_count + self.cancelled_count
            > self.selected_count
        ):
            raise ValueError("Cohort terminal recording counts exceed selected_count.")
        if self.report_path is not None and self.report_sha256 is None:
            raise ValueError("A cohort report path requires its SHA-256 checksum.")
        if self.summary_path is not None and self.summary_sha256 is None:
            raise ValueError("A cohort summary path requires its SHA-256 checksum.")
        if self.state in {
            CohortRunState.SUCCEEDED,
            CohortRunState.FAILED,
            CohortRunState.CANCELLED,
        }:
            if self.finished_at is None:
                raise ValueError(f"A {self.state.value} cohort run requires finished_at.")
        elif self.finished_at is not None:
            raise ValueError("An active cohort run must not carry finished_at.")
        if self.state == CohortRunState.SUCCEEDED:
            if self.processed_count < 1 or self.report_path is None or self.summary_path is None:
                raise ValueError("A succeeded cohort run requires processed data and a report.")
            if self.failure_reason is not None:
                raise ValueError("A succeeded cohort run cannot have a failure reason.")
        if self.state == CohortRunState.FAILED and not self.failure_reason:
            raise ValueError("A failed cohort run requires a reason.")
        return self


def migrate_cohort_run_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the stored cohort-run schema version before Pydantic parsing."""

    value = data.get("schema_version")
    if value not in SUPPORTED_COHORT_RUN_VERSIONS:
        raise ValueError(
            f"Unsupported cohort run schema version {value!r}; "
            f"supported versions are {', '.join(SUPPORTED_COHORT_RUN_VERSIONS)}."
        )
    if value in {"1.0", "1.1"}:
        migrated = dict(data)
        migrated["schema_version"] = "1.2"
        migrated.setdefault("cancelled_count", 0)
        migrated.setdefault("workflow", None)
        return migrated
    return dict(data)
