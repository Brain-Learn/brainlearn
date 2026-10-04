"""Allowlisted demonstration-node adapters for the Step 4C worker.

Only these internal functions may execute. The worker never accepts shell
commands, module paths, or arbitrary Python from API payloads: an unknown
node type fails deterministically with ``unsupported_node_type`` instead of
executing anything.

Adapters receive a :class:`NodeContext` scoped to one node attempt and either
return staged outputs or raise one of the control exceptions. Staging lives
beneath the run directory; promotion to successful artifacts happens only in
the worker after success.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from brainlearn_server.project_store import ProjectStore


class ControlledFailure(Exception):
    """A demonstration node failed in a controlled, reportable way."""


class CancelledByUser(Exception):
    """Cooperative cancellation observed by an adapter."""


class ReviewPauseRequested(Exception):
    """A demonstration node requests human review before completing."""


@dataclass
class StagedOutput:
    port_id: str
    relative_path: str
    media_type: str = "application/octet-stream"


@dataclass
class NodeContext:
    node_id: str
    node_type: str
    parameters: dict[str, Any]
    inputs: dict[str, Path]
    staging_dir: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    project_path: str | None = None
    project_store: ProjectStore | None = None


DemoAdapter = Callable[[NodeContext], list[StagedOutput]]

MAX_DELAY_SECONDS = 30.0


def _require_cancel(ctx: NodeContext) -> None:
    if ctx.cancel.is_set():
        raise CancelledByUser(f"Node {ctx.node_id!r} observed cancellation.")


def delay_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Wait cooperatively; produces no artifacts."""

    spec = DEMO_NODES["demo.delay"].parameters["seconds"]
    raw = ctx.parameters.get("seconds", spec.default)
    lower = spec.minimum if spec.minimum is not None else 0.0
    upper = spec.maximum if spec.maximum is not None else MAX_DELAY_SECONDS
    total = max(lower, min(float(raw), upper))
    deadline = time.monotonic() + total
    while time.monotonic() < deadline:
        _require_cancel(ctx)
        time.sleep(0.02)
    _require_cancel(ctx)
    return []


def copy_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Write a small canned text output into staging."""

    _require_cancel(ctx)
    text = str(ctx.parameters.get("text", DEMO_NODES["demo.copy"].parameters["text"].default))
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "output.txt").write_text(text, encoding="utf-8")
    return [StagedOutput(port_id="output", relative_path="output.txt", media_type="text/plain")]


def fail_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Fail deterministically with a controlled message."""

    _require_cancel(ctx)
    raise ControlledFailure(
        str(ctx.parameters.get("message", DEMO_NODES["demo.fail"].parameters["message"].default))
    )


def review_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Pause for an explicit researcher decision."""

    _require_cancel(ctx)
    raise ReviewPauseRequested(f"Node {ctx.node_id!r} awaits review.")


def relay_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Forward one input's bytes, recording the exact source path consumed."""

    _require_cancel(ctx)
    try:
        source = ctx.inputs["in"]
    except KeyError:
        raise ControlledFailure(
            f"Node {ctx.node_id!r} requires input port 'in' but none resolved."
        ) from None
    data = source.read_bytes()
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "output.txt").write_text(
        f"{source}\n{data.decode('utf-8', errors='replace')}", encoding="utf-8"
    )
    return [StagedOutput(port_id="output", relative_path="output.txt", media_type="text/plain")]


def bids_input_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Resolve a project-relative BIDS dataset and emit a path-only reference."""

    from brainlearn_server.bids_eeg import BidsDatasetPathError, BidsEegDiscoveryService

    _require_cancel(ctx)
    if ctx.project_path is None or ctx.project_store is None:
        raise ControlledFailure("The BIDS EEG input requires an open BrainLearn project.")
    dataset_path = str(ctx.parameters.get("root", ""))
    if not dataset_path:
        raise ControlledFailure("Choose a project-relative BIDS EEG dataset path.")
    service = BidsEegDiscoveryService(ctx.project_store)
    try:
        discovery = service.discover(ctx.project_path, dataset_path)
    except BidsDatasetPathError as exc:
        raise ControlledFailure(str(exc)) from exc
    if discovery.status != "ready":
        message = "; ".join(issue.message for issue in discovery.issues)
        raise ControlledFailure(message or "The selected BIDS EEG dataset has no ready recording.")
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    (ctx.staging_dir / "dataset.json").write_text(
        json.dumps(
            {"schema_version": "1.0", "dataset_path": dataset_path},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return [
        StagedOutput(port_id="dataset", relative_path="dataset.json", media_type="application/json")
    ]


def eeg_inspection_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Inspect a selected BIDS EEG recording and persist summary metadata only."""

    from brainlearn_server.bids_eeg import (
        BidsDatasetPathError,
        BidsEegDiscoveryService,
        BidsSignalInspectionError,
    )

    _require_cancel(ctx)
    if ctx.project_path is None or ctx.project_store is None:
        raise ControlledFailure("Signal inspection requires an open BrainLearn project.")
    try:
        dataset_reference = json.loads(ctx.inputs["dataset"].read_text(encoding="utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledFailure("The upstream BIDS dataset reference is invalid.") from exc
    if (
        not isinstance(dataset_reference, dict)
        or dataset_reference.get("schema_version") != "1.0"
        or not isinstance(dataset_reference.get("dataset_path"), str)
    ):
        raise ControlledFailure("The upstream BIDS dataset reference is invalid.")
    dataset_path = dataset_reference["dataset_path"]
    recording_path = str(ctx.parameters.get("recording_path", ""))
    if not recording_path:
        raise ControlledFailure("Choose a discovered recording path to inspect.")
    _require_cancel(ctx)
    service = BidsEegDiscoveryService(ctx.project_store)
    try:
        identity = service.identify(ctx.project_path, dataset_path, recording_path)
        if identity.status != "ready" or identity.content_identity is None:
            raise ControlledFailure(
                identity.message or "The recording could not be identified within limits."
            )
        inspection = service.inspect_signal(ctx.project_path, dataset_path, recording_path)
        identity_after_inspection = service.identify(ctx.project_path, dataset_path, recording_path)
    except (BidsDatasetPathError, BidsSignalInspectionError) as exc:
        raise ControlledFailure(str(exc)) from exc
    if (
        identity_after_inspection.status != "ready"
        or identity_after_inspection.content_identity != identity.content_identity
    ):
        raise ControlledFailure(
            "The recording changed during signal inspection; refusing to publish a stale report."
        )
    _require_cancel(ctx)
    ctx.staging_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        ctx.staging_dir / "recording-reference.json",
        {
            "schema_version": "1.0",
            "dataset_path": dataset_path,
            "recording_path": recording_path,
            "source_content_identity": identity.content_identity,
        },
    )
    _write_json(
        ctx.staging_dir / "inspection-report.json",
        {
            "schema_version": "1.0",
            "source_content_identity": identity.content_identity,
            "source_files": [item.model_dump(mode="json") for item in identity.files],
            "inspection": inspection.model_dump(mode="json"),
        },
    )
    return [
        StagedOutput(
            port_id="raw",
            relative_path="recording-reference.json",
            media_type="application/vnd.brainlearn.artifact-reference+json",
        ),
        StagedOutput(
            port_id="report", relative_path="inspection-report.json", media_type="application/json"
        ),
    ]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


@dataclass(frozen=True)
class DemoParameter:
    """One execution-relevant parameter of a demonstration node.

    The single contract for a parameter's name, type, default, and bounds:
    handlers interpret values through it and the registry derives its UI
    parameter schemas from it, so the two cannot drift apart. Parameters
    affect content identity and execution; they are not display metadata.
    """

    id: str
    label: str
    value_type: Literal["string", "number"]
    default: Any
    required: bool = False
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class DemoNodeManifest:
    """One authoritative execution manifest per demonstration node type.

    ``inputs`` and ``outputs`` map port IDs to required flags: True means the
    port is required (must be declared, and for inputs must be connected via
    an edge; for outputs must be emitted on success), False means optional
    (may be declared/emitted but is never required). ``parameters`` carries
    the execution-relevant parameter contract shared with the registry.
    """

    version: str
    inputs: dict[str, bool]
    outputs: dict[str, bool]
    handler: DemoAdapter
    parameters: dict[str, DemoParameter] = field(default_factory=dict)
    cacheable: bool = True


DEMO_NODES: dict[str, DemoNodeManifest] = {
    "demo.delay": DemoNodeManifest(
        version="0.1.0",
        inputs={},
        outputs={"out": False},
        handler=delay_adapter,
        parameters={
            "seconds": DemoParameter(
                id="seconds",
                label="Delay (seconds)",
                value_type="number",
                default=0.1,
                description="Cooperative wait before completing. Clamped to a safe maximum.",
                minimum=0.0,
                maximum=MAX_DELAY_SECONDS,
            )
        },
    ),
    "demo.copy": DemoNodeManifest(
        version="0.1.0",
        inputs={"in": False},
        outputs={"output": True},
        handler=copy_adapter,
        parameters={
            "text": DemoParameter(
                id="text",
                label="Text",
                value_type="string",
                default="brainlearn-demo",
                description="Canned text written to the output file.",
            )
        },
    ),
    "demo.fail": DemoNodeManifest(
        version="0.1.0",
        inputs={},
        outputs={"out": False},
        handler=fail_adapter,
        parameters={
            "message": DemoParameter(
                id="message",
                label="Failure message",
                value_type="string",
                default="demonstration failure",
                description="Message reported when this node fails on purpose.",
            )
        },
    ),
    "demo.review": DemoNodeManifest(
        version="0.1.0", inputs={}, outputs={"out": False}, handler=review_adapter
    ),
    "demo.relay": DemoNodeManifest(
        version="0.1.0",
        inputs={"in": True},
        outputs={"output": True},
        handler=relay_adapter,
    ),
}

EEG_NODES: dict[str, DemoNodeManifest] = {
    "input.bids_eeg": DemoNodeManifest(
        version="0.2.0",
        inputs={},
        outputs={"dataset": True},
        handler=bids_input_adapter,
        parameters={
            "root": DemoParameter(
                id="root",
                label="Dataset root",
                value_type="string",
                default="synthetic/example-bids",
                required=True,
                description="Project-relative path to a discovered BIDS EEG dataset.",
            )
        },
        cacheable=False,
    ),
    "eeg.inspect": DemoNodeManifest(
        version="0.2.0",
        inputs={"dataset": True},
        outputs={"raw": True, "report": True},
        handler=eeg_inspection_adapter,
        parameters={
            "recording_path": DemoParameter(
                id="recording_path",
                label="Recording path",
                value_type="string",
                default="",
                required=True,
                description="Path to one discovered recording, relative to its dataset root.",
            )
        },
        cacheable=False,
    ),
}

WORKER_NODES = {**DEMO_NODES, **EEG_NODES}
