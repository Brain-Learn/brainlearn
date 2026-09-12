"""Versioned execution records for deterministic demonstration runs.

These contracts describe runs without executing anything. Later slices will
persist them inside authorized projects, schedule workers, and cache outputs;
this slice only fixes the persisted shapes, state meanings, and validation
rules so every later slice and test agrees on them.

Large data is never embedded: artifacts are project-relative path references
plus content identities. Session tokens are never recorded here.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brainlearn_core.identity import environment_identity as _compute_environment_identity
from brainlearn_core.identity import node_content_identity as _compute_node_identity

EXECUTION_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SUPPORTED_EXECUTION_VERSIONS: tuple[str, ...] = ("1.0",)

_IDENTITY_PATTERN = r"^brainlearn-v1:[a-z]+:[0-9a-f]{64}$"
_NODE_IDENTITY_PATTERN = r"^brainlearn-v1:node:[0-9a-f]{64}$"
_ENVIRONMENT_IDENTITY_PATTERN = r"^brainlearn-v1:environment:[0-9a-f]{64}$"
_WORKFLOW_IDENTITY_PATTERN = r"^brainlearn-v1:workflow:[0-9a-f]{64}$"
_ARTIFACT_IDENTITY_PATTERN = r"^brainlearn-v1:artifact:[0-9a-f]{64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_DRIVE_QUALIFIED_PATTERN = r"^[A-Za-z]:"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEPENDENCY_SKIPPED = "dependency_skipped"
    CACHE_REUSED = "cache_reused"


RUN_TERMINAL_STATES = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED})
NODE_TERMINAL_STATES = frozenset(
    {
        NodeRunState.SUCCEEDED,
        NodeRunState.FAILED,
        NodeRunState.CANCELLED,
        NodeRunState.DEPENDENCY_SKIPPED,
        NodeRunState.CACHE_REUSED,
    }
)
# States that consumed an execution attempt. Queued work never started, and
# dependency-skipped or cache-reused work never executed.
NODE_EXECUTED_STATES = frozenset(
    {
        NodeRunState.RUNNING,
        NodeRunState.WAITING_FOR_REVIEW,
        NodeRunState.SUCCEEDED,
        NodeRunState.FAILED,
        NodeRunState.CANCELLED,
    }
)


def counts_as_execution(state: NodeRunState) -> bool:
    """Return True when the node run consumed an execution attempt."""

    return state in NODE_EXECUTED_STATES


def _ensure_iso_timestamp(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} must include a timezone offset, got {value!r}. "
            "Naive timestamps cannot be ordered reliably across platforms."
        )
    return value


def _require_chronology(
    earlier: str | None, later: str | None, earlier_name: str, later_name: str
) -> None:
    """Reject chronologically impossible timestamp pairs; equal instants pass."""

    if earlier is None or later is None:
        return
    if datetime.fromisoformat(later) < datetime.fromisoformat(earlier):
        raise ValueError(f"{later_name} ({later}) must not precede {earlier_name} ({earlier}).")


class EnvironmentRecord(BaseModel):
    """Facts needed to replay or explain where a run executed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    operating_system: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    packages: dict[str, str] = Field(default_factory=dict)
    accelerator: str = Field(min_length=1, default="cpu")


class ArtifactRecord(BaseModel):
    """Reference to one output file; the bytes live outside the record."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    artifact_id: str = Field(min_length=1, pattern=_ARTIFACT_IDENTITY_PATTERN)
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1, default="application/octet-stream")
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    produced_by_node: str = Field(min_length=1)
    port_id: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _path_must_be_project_relative(cls, value: str) -> str:
        # Normalize Windows separators first so one canonical forward-slash
        # representation is stored regardless of the author's OS.
        canonical = value.replace("\\", "/")
        if (
            not canonical
            or canonical.startswith("/")
            or re.match(_DRIVE_QUALIFIED_PATTERN, canonical) is not None
        ):
            raise ValueError(
                "Artifact path must be project-relative with no drive or "
                f"root qualifier, got {value!r}."
            )
        segments = canonical.split("/")
        if ".." in segments or "." in segments or "" in segments:
            raise ValueError(
                f"Artifact path must not contain empty, '.', or '..' segments, got {value!r}."
            )
        return canonical


class FailureRecord(BaseModel):
    """Actionable description of why a run or node run failed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    node_run_id: str | None = None
    at: str = Field(min_length=1)

    @field_validator("at")
    @classmethod
    def _at_must_be_iso(cls, value: str) -> str:
        return _ensure_iso_timestamp(value, "at") or value


class ReviewPauseRecord(BaseModel):
    """Persisted human-review checkpoint linked to the reviewed data version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    id: str = Field(min_length=1)
    node_run_id: str = Field(min_length=1)
    input_identity: str = Field(min_length=1, pattern=_IDENTITY_PATTERN)
    requested_at: str = Field(min_length=1)
    decided_at: str | None = None
    decision: Literal["approved", "rejected"] | None = None
    note: str = ""

    @field_validator("requested_at", "decided_at")
    @classmethod
    def _timestamps_must_be_iso(cls, value: str | None) -> str | None:
        return _ensure_iso_timestamp(value, "review timestamp")

    @model_validator(mode="after")
    def _decision_requires_timestamp(self) -> "ReviewPauseRecord":
        if self.decision is None and self.decided_at is not None:
            raise ValueError("A review without a decision must not set decided_at.")
        if self.decision is not None and self.decided_at is None:
            raise ValueError("A decided review must record decided_at.")
        _require_chronology(self.requested_at, self.decided_at, "requested_at", "decided_at")
        return self


class RunEventKind(StrEnum):
    RUN_QUEUED = "run_queued"
    RUN_STARTED = "run_started"
    NODE_QUEUED = "node_queued"
    NODE_STARTED = "node_started"
    NODE_SUCCEEDED = "node_succeeded"
    NODE_FAILED = "node_failed"
    NODE_SKIPPED = "node_skipped"
    CACHE_REUSED = "cache_reused"
    REVIEW_REQUESTED = "review_requested"
    REVIEW_DECIDED = "review_decided"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


class RunEvent(BaseModel):
    """One ordered, timestamped fact in a run's event stream."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    seq: int = Field(ge=0)
    at: str = Field(min_length=1)
    kind: RunEventKind
    node_run_id: str | None = None
    message: str = ""

    @field_validator("at")
    @classmethod
    def _at_must_be_iso(cls, value: str) -> str:
        return _ensure_iso_timestamp(value, "at") or value


class NodeRunRecord(BaseModel):
    """Replay-complete record of one node's execution attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    node_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    dependencies: list[str] = Field(default_factory=list)
    # attempt counts executions: states that never executed (queued,
    # dependency-skipped, cache-reused) record 0; states that started or
    # finished execution record a positive attempt number.
    attempt: int = Field(ge=0, default=0)
    state: NodeRunState = NodeRunState.QUEUED
    started_at: str | None = None
    finished_at: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    environment_identity: str = Field(min_length=1, pattern=_ENVIRONMENT_IDENTITY_PATTERN)
    seed: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    content_identity: str = Field(min_length=1, pattern=_NODE_IDENTITY_PATTERN)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    failure: FailureRecord | None = None
    review_pause: ReviewPauseRecord | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def _timestamps_must_be_iso(cls, value: str | None) -> str | None:
        return _ensure_iso_timestamp(value, "node timestamp")

    @field_validator("inputs")
    @classmethod
    def _inputs_must_be_identities(cls, value: dict[str, str]) -> dict[str, str]:
        for port, identity in value.items():
            if re.match(_IDENTITY_PATTERN, identity) is None:
                raise ValueError(
                    f"Input {port!r} must be a brainlearn-v1 identity, got {identity!r}."
                )
        return value

    @model_validator(mode="after")
    def _state_must_match_lifecycle(self) -> "NodeRunRecord":
        state = self.state
        if state == NodeRunState.QUEUED:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("A queued node run must not carry timestamps.")
        elif state in {NodeRunState.RUNNING, NodeRunState.WAITING_FOR_REVIEW}:
            if self.started_at is None:
                raise ValueError(f"A {state.value} node run must record started_at.")
            if self.finished_at is not None:
                raise ValueError(f"A {state.value} node run must not record finished_at.")
        elif state in {NodeRunState.SUCCEEDED, NodeRunState.FAILED}:
            if self.started_at is None or self.finished_at is None:
                raise ValueError(f"A {state.value} node run must record started_at.")
        elif state == NodeRunState.CANCELLED:
            if self.finished_at is None:
                raise ValueError("A cancelled node run must record finished_at.")
        else:  # DEPENDENCY_SKIPPED, CACHE_REUSED: never executed.
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError(
                    f"A {state.value} node run never executed and must not carry timestamps."
                )
        if state in {
            NodeRunState.QUEUED,
            NodeRunState.DEPENDENCY_SKIPPED,
            NodeRunState.CACHE_REUSED,
        }:
            if self.attempt != 0:
                raise ValueError(
                    f"A {state.value} node run consumed no execution attempt "
                    "and must record attempt 0."
                )
        elif self.attempt < 1:
            raise ValueError(
                f"A {state.value} node run consumed an execution attempt "
                "and must record a positive attempt number."
            )
        _require_chronology(self.started_at, self.finished_at, "started_at", "finished_at")
        if state == NodeRunState.FAILED and self.failure is None:
            raise ValueError("A failed node run must record failure details.")
        if state != NodeRunState.FAILED and self.failure is not None:
            raise ValueError("Only a failed node run may record failure details.")
        if (
            state
            in {
                NodeRunState.FAILED,
                NodeRunState.CANCELLED,
                NodeRunState.DEPENDENCY_SKIPPED,
            }
            and self.artifacts
        ):
            raise ValueError(
                "Failed, cancelled, or skipped work must not list successful artifacts."
            )
        if state == NodeRunState.WAITING_FOR_REVIEW:
            if self.review_pause is None or self.review_pause.decided_at is not None:
                raise ValueError("A waiting node run must reference a pending review.")
        if (
            state == NodeRunState.SUCCEEDED
            and self.review_pause is not None
            and (self.review_pause.decision != "approved" or self.review_pause.decided_at is None)
        ):
            raise ValueError("A succeeded review must record an approval decision.")
        for artifact in self.artifacts:
            if artifact.produced_by_node != self.id:
                raise ValueError(
                    f"Artifact {artifact.artifact_id} claims producer "
                    f"{artifact.produced_by_node!r} but is stored in node run {self.id!r}."
                )
        if self.failure is not None and self.failure.node_run_id not in (None, self.id):
            raise ValueError(
                f"Failure references node run {self.failure.node_run_id!r} "
                f"but is stored in node run {self.id!r}."
            )
        if self.review_pause is not None and self.review_pause.node_run_id != self.id:
            raise ValueError(
                f"Review {self.review_pause.id!r} references node run "
                f"{self.review_pause.node_run_id!r} but is stored in node run {self.id!r}."
            )
        try:
            expected_identity = _compute_node_identity(
                node_type=self.node_type,
                node_version=self.node_version,
                inputs=self.inputs,
                parameters=self.parameters,
                environment_identity=self.environment_identity,
                seed=self.seed,
                settings=self.settings,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Node run {self.id!r} has replay fields that cannot be hashed: {exc}"
            ) from exc
        if expected_identity != self.content_identity:
            raise ValueError(
                f"Node run {self.id!r} content_identity does not match its "
                "node type/version, inputs, parameters, environment identity, "
                "seed, and settings. Recompute the identity instead of reusing "
                "a stale one."
            )
        return self


class RunRecord(BaseModel):
    """Replay-complete record of one workflow run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = EXECUTION_SCHEMA_VERSION
    id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_schema_version: Literal["1.0"] = "1.0"
    workflow_identity: str = Field(min_length=1, pattern=_WORKFLOW_IDENTITY_PATTERN)
    state: RunState = RunState.QUEUED
    created_at: str = Field(min_length=1)
    started_at: str | None = None
    finished_at: str | None = None
    environment: EnvironmentRecord
    seed: int | None = None
    node_runs: list[NodeRunRecord] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)
    failure: FailureRecord | None = None

    @field_validator("created_at", "started_at", "finished_at")
    @classmethod
    def _timestamps_must_be_iso(cls, value: str | None) -> str | None:
        return _ensure_iso_timestamp(value, "run timestamp")

    @staticmethod
    def _dependency_graph(node_runs: list[NodeRunRecord]) -> dict[str, list[str]]:
        return {node.id: list(node.dependencies) for node in node_runs}

    @model_validator(mode="after")
    def _state_must_match_lifecycle(self) -> "RunRecord":
        state = self.state
        if state == RunState.QUEUED:
            if self.started_at is not None or self.finished_at is not None:
                raise ValueError("A queued run must not carry timestamps.")
        elif state in {RunState.RUNNING, RunState.WAITING_FOR_REVIEW}:
            if self.started_at is None:
                raise ValueError(f"A {state.value} run must record started_at.")
            if self.finished_at is not None:
                raise ValueError(f"A {state.value} run must not record finished_at.")
        elif state in {RunState.SUCCEEDED, RunState.FAILED}:
            if self.started_at is None or self.finished_at is None:
                raise ValueError(f"A {state.value} run must record started_at.")
        elif state == RunState.CANCELLED and self.finished_at is None:
            raise ValueError("A cancelled run must record finished_at.")
        _require_chronology(self.created_at, self.started_at, "created_at", "started_at")
        _require_chronology(self.started_at, self.finished_at, "started_at", "finished_at")
        _require_chronology(self.created_at, self.finished_at, "created_at", "finished_at")
        if state == RunState.FAILED and self.failure is None:
            raise ValueError("A failed run must record failure details.")
        if state != RunState.FAILED and self.failure is not None:
            raise ValueError("Only a failed run may record failure details.")
        expected_seq = list(range(len(self.events)))
        actual_seq = [event.seq for event in self.events]
        if actual_seq != expected_seq:
            raise ValueError("Run events must be ordered with seq 0..n-1 without gaps.")
        self._check_referential_integrity()
        self._check_aggregate_consistency()
        return self

    def _check_referential_integrity(self) -> None:
        node_ids = [node.id for node in self.node_runs]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("Node-run IDs must be unique within a run.")
        known = set(node_ids)
        for node in self.node_runs:
            if len(set(node.dependencies)) != len(node.dependencies):
                raise ValueError(f"Node run {node.id!r} lists duplicate dependencies.")
            for dependency in node.dependencies:
                if dependency not in known:
                    raise ValueError(
                        f"Node run {node.id!r} depends on unknown node run {dependency!r}."
                    )
                if dependency == node.id:
                    raise ValueError(f"Node run {node.id!r} must not depend on itself.")
        graph = self._dependency_graph(self.node_runs)
        visiting: set[str] = set()
        visited: set[str] = set()

        def _visit(node_id: str, chain: list[str]) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError(
                    "Node-run dependencies must not contain a cycle: "
                    + " -> ".join([*chain, node_id])
                )
            visiting.add(node_id)
            for dependency in graph[node_id]:
                _visit(dependency, [*chain, node_id])
            visiting.discard(node_id)
            visited.add(node_id)

        for node_id in graph:
            _visit(node_id, [])
        for event in self.events:
            if event.node_run_id is not None and event.node_run_id not in known:
                raise ValueError(
                    f"Event {event.seq} references unknown node run {event.node_run_id!r}."
                )
        if self.failure is not None and self.failure.node_run_id not in (None, *known):
            raise ValueError(
                f"Run failure references unknown node run {self.failure.node_run_id!r}."
            )
        expected_environment = _compute_environment_identity(
            self.environment.model_dump(mode="json")
        )
        for node in self.node_runs:
            if node.environment_identity != expected_environment:
                raise ValueError(
                    f"Node run {node.id!r} references environment "
                    f"{node.environment_identity!r}, which does not match the "
                    "run environment. Persist per-node environment records "
                    "before using per-node environments."
                )

    def _check_aggregate_consistency(self) -> None:
        states = {node.state for node in self.node_runs}
        if self.state == RunState.SUCCEEDED and not states <= {
            NodeRunState.SUCCEEDED,
            NodeRunState.CACHE_REUSED,
        }:
            raise ValueError("A succeeded run must only contain succeeded or cache-reused nodes.")
        if self.state == RunState.QUEUED and any(state != NodeRunState.QUEUED for state in states):
            raise ValueError("A queued run must not contain work that has started or terminated.")
        if self.state in RUN_TERMINAL_STATES and any(
            state not in NODE_TERMINAL_STATES for state in states
        ):
            raise ValueError("A terminal run must not contain nonterminal node runs.")


def _check_version(data: dict[str, Any], field: str, record: str) -> dict[str, Any]:
    version = data.get(field)
    if version not in SUPPORTED_EXECUTION_VERSIONS:
        raise ValueError(
            f"Unsupported {field} {version!r} for {record}. "
            f"Supported versions: {', '.join(SUPPORTED_EXECUTION_VERSIONS)}. "
            "Rerun with a compatible BrainLearn release instead of coercing "
            "the stored record."
        )
    return data


def migrate_run_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw run record dict to the current execution schema."""

    return _check_version(data, "schema_version", "run records")


def migrate_node_run_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw node-run record dict to the current execution schema."""

    return _check_version(data, "schema_version", "node-run records")


def migrate_artifact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw artifact record dict to the current execution schema."""

    return _check_version(data, "schema_version", "artifact records")


def migrate_environment_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw environment record dict to the current execution schema."""

    return _check_version(data, "schema_version", "environment records")


def migrate_event_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw run-event dict to the current execution schema."""

    return _check_version(data, "schema_version", "run-event records")


def migrate_failure_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw failure record dict to the current execution schema."""

    return _check_version(data, "schema_version", "failure records")


def migrate_review_pause_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw review-pause record dict to the current execution schema."""

    return _check_version(data, "schema_version", "review-pause records")
