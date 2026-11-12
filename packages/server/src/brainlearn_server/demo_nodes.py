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

from brainlearn_core import ArtifactRecord, QCReviewRecord

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
    input_artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    cancel: threading.Event = field(default_factory=threading.Event)
    project_path: str | None = None
    project_store: ProjectStore | None = None
    run_store: Any = None
    run_id: str | None = None
    node_run_id: str | None = None
    run_seed: int | None = None
    qc_reviews: list[QCReviewRecord] = field(default_factory=list)


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

    from brainlearn_server.bids_eeg import (
        BidsDatasetPathError,
        BidsEegDiscoveryService,
        bids_eeg_discovery_error,
    )

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
        raise ControlledFailure(bids_eeg_discovery_error(discovery))
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


def eeg_bad_channel_review_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Pause for an explicit channel decision bound to a fresh BIDS recording."""

    from brainlearn_server.bids_eeg import (
        BidsDatasetPathError,
        BidsEegDiscoveryService,
        BidsSignalInspectionError,
    )

    _require_cancel(ctx)
    if ctx.project_path is None or ctx.project_store is None:
        raise ControlledFailure("Bad-channel review requires an open BrainLearn project.")
    try:
        reference = json.loads(ctx.inputs["raw"].read_text(encoding="utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledFailure("The upstream inspected EEG reference is invalid.") from exc
    if (
        not isinstance(reference, dict)
        or reference.get("schema_version") != "1.0"
        or not isinstance(reference.get("dataset_path"), str)
        or not isinstance(reference.get("recording_path"), str)
        or not isinstance(reference.get("source_content_identity"), str)
    ):
        raise ControlledFailure("The upstream inspected EEG reference is invalid.")
    service = BidsEegDiscoveryService(ctx.project_store)
    try:
        identity = service.identify(
            ctx.project_path, reference["dataset_path"], reference["recording_path"]
        )
    except BidsDatasetPathError as exc:
        raise ControlledFailure(str(exc)) from exc
    except BidsSignalInspectionError as exc:
        raise ControlledFailure(str(exc)) from exc
    if (
        identity.status != "ready"
        or identity.content_identity != reference["source_content_identity"]
    ):
        raise ControlledFailure(
            "The EEG recording changed after inspection. Run Inspect Signal again before review."
        )
    saved_decision = next(
        (item for item in ctx.qc_reviews if item.node_run_id == ctx.node_run_id), None
    )
    if saved_decision is None:
        raise ReviewPauseRequested(
            "Review this recording's EEG channel list and explicitly record channels to exclude. "
            "The saved decision will be passed to filtering and ICA for this recording only."
        )
    if saved_decision.decision != "approved":
        raise ControlledFailure("The bad-channel review was not approved for this recording.")
    _require_cancel(ctx)
    reviewed_reference = {
        **reference,
        "bad_channel_review": {
            "node_id": saved_decision.node_id,
            "review_id": saved_decision.id,
            "decision": saved_decision.decision,
            "reviewer": saved_decision.reviewer,
            "excluded_channel_names": list(saved_decision.excluded_channel_names),
            "source_content_identity": saved_decision.source_content_identity,
        },
    }
    _write_json(ctx.staging_dir / "bad-channel-reviewed-reference.json", reviewed_reference)
    return [
        StagedOutput(
            "reviewed",
            "bad-channel-reviewed-reference.json",
            "application/vnd.brainlearn.artifact-reference+json",
        )
    ]


def eeg_filter_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Apply the allowlisted MNE EEG band-pass and optional notch to inspected BIDS data."""

    from brainlearn_server.mne_filter import filter_bids_eeg

    return filter_bids_eeg(ctx)


def eeg_ica_fit_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Fit an ICA model and publish diagnostics without excluding components."""

    from brainlearn_server.mne_ica import fit_ica_eeg

    return fit_ica_eeg(ctx)


def eeg_ica_review_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Pause for a decision, then apply its saved ICA component exclusions."""

    from brainlearn_server.mne_ica import apply_reviewed_ica_eeg

    return apply_reviewed_ica_eeg(ctx)


def eeg_epochs_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Create event-locked epochs through the allowlisted MNE analysis adapter."""

    from brainlearn_server.mne_analysis import epoch_adapter

    return epoch_adapter(ctx)


def eeg_erp_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Summarize event-related potentials from the retained epoch artifact."""

    from brainlearn_server.mne_analysis import erp_adapter

    return erp_adapter(ctx)


def eeg_psd_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Estimate descriptive Welch spectra from the retained epoch artifact."""

    from brainlearn_server.mne_analysis import psd_adapter

    return psd_adapter(ctx)


def eeg_report_adapter(ctx: NodeContext) -> list[StagedOutput]:
    """Render the actual persisted run parameters and upstream result summaries."""

    from brainlearn_server.mne_analysis import report_adapter

    return report_adapter(ctx)


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
    value_type: Literal["string", "number", "integer", "boolean"]
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
    "eeg.bad_channel_review": DemoNodeManifest(
        version="0.1.0",
        inputs={"raw": True},
        outputs={"reviewed": False},
        handler=eeg_bad_channel_review_adapter,
        cacheable=False,
    ),
    "eeg.filter": DemoNodeManifest(
        version="1.1.0",
        inputs={"raw": True},
        outputs={"filtered": True, "preview": True, "provenance": True},
        handler=eeg_filter_adapter,
        parameters={
            "low_hz": DemoParameter(
                id="low_hz",
                label="Low cutoff (Hz)",
                value_type="number",
                default=1.0,
                required=True,
                description="High-pass cutoff in Hz; must be above 0 and below the high cutoff.",
                minimum=0.1,
                maximum=100.0,
            ),
            "high_hz": DemoParameter(
                id="high_hz",
                label="High cutoff (Hz)",
                value_type="number",
                default=40.0,
                required=True,
                description=(
                    "Low-pass cutoff in Hz; must be below the recording's Nyquist frequency."
                ),
                minimum=0.5,
                maximum=200.0,
            ),
            "notch_hz": DemoParameter(
                id="notch_hz",
                label="Notch frequencies (Hz)",
                value_type="string",
                default="",
                description=(
                    "Optional comma-separated notch centers in Hz; leave blank to disable. "
                    "Each center must fall within the pass band and below Nyquist."
                ),
            ),
            "notch_width_hz": DemoParameter(
                id="notch_width_hz",
                label="Notch width (Hz)",
                value_type="number",
                default=1.0,
                description="Width passed to MNE's FIR notch filter, in Hz.",
                minimum=0.1,
                maximum=10.0,
            ),
        },
        cacheable=False,
    ),
    "eeg.ica_fit": DemoNodeManifest(
        version="0.2.0",
        inputs={"raw": True},
        outputs={"ica": True, "diagnostics": True, "provenance": True},
        handler=eeg_ica_fit_adapter,
        parameters={
            "method": DemoParameter(
                id="method",
                label="ICA method",
                value_type="string",
                default=None,
                required=True,
                description="Provisional method; FastICA is currently the only supported option.",
            ),
            "component_policy": DemoParameter(
                id="component_policy",
                label="Component count policy",
                value_type="string",
                default=None,
                required=True,
                description=(
                    "Provisional policy: pass n_components=None to MNE, using all selected "
                    "EEG channels."
                ),
            ),
            "max_iterations": DemoParameter(
                id="max_iterations",
                label="Maximum ICA iterations",
                value_type="integer",
                default=None,
                required=True,
                description="Provisional FastICA convergence limit.",
                minimum=10,
                maximum=5000,
            ),
            "reject_by_annotation": DemoParameter(
                id="reject_by_annotation",
                label="Reject BAD annotated spans during ICA fitting",
                value_type="boolean",
                default=None,
                required=True,
                description="Provisional setting. False includes BAD annotated spans in the fit.",
            ),
            "decim": DemoParameter(
                id="decim",
                label="ICA decimation factor",
                value_type="integer",
                default=None,
                required=True,
                description="Provisional sampling factor passed to MNE ICA fitting.",
                minimum=1,
                maximum=16,
            ),
        },
        cacheable=False,
    ),
    "eeg.ica_review": DemoNodeManifest(
        version="0.1.0",
        inputs={"raw": True, "fit": True},
        outputs={"reviewed": True, "provenance": True},
        handler=eeg_ica_review_adapter,
        cacheable=False,
    ),
    "eeg.epochs": DemoNodeManifest(
        version="0.2.0",
        inputs={"reviewed": True},
        outputs={"epochs": True, "diagnostics": True},
        handler=eeg_epochs_adapter,
        parameters={
            "event": DemoParameter(
                id="event",
                label="Exact event label",
                value_type="string",
                default=None,
                required=True,
                description="Choose one exact annotation label shown by Event Diagnostics.",
            ),
            "tmin": DemoParameter(
                id="tmin",
                label="Epoch start (s)",
                value_type="number",
                default=None,
                required=True,
                description="Provisional epoch start relative to the selected event.",
            ),
            "tmax": DemoParameter(
                id="tmax",
                label="Epoch end (s)",
                value_type="number",
                default=None,
                required=True,
                description="Provisional epoch end relative to the selected event.",
            ),
            "baseline_enabled": DemoParameter(
                id="baseline_enabled",
                label="Apply baseline correction",
                value_type="boolean",
                default=None,
                required=True,
                description="Select whether the configured baseline interval is applied.",
            ),
            "baseline_start": DemoParameter(
                id="baseline_start",
                label="Baseline start (s)",
                value_type="number",
                default=None,
                required=True,
                description="Explicit start of the baseline interval relative to the event.",
            ),
            "baseline_end": DemoParameter(
                id="baseline_end",
                label="Baseline end (s)",
                value_type="number",
                default=None,
                required=True,
                description="Explicit end of the baseline interval relative to the event.",
            ),
            "reject_eeg_peak_to_peak_uv": DemoParameter(
                id="reject_eeg_peak_to_peak_uv",
                label="EEG peak-to-peak rejection (µV; 0 disables)",
                value_type="number",
                default=None,
                required=True,
                minimum=0.0,
                description="Explicit MNE epoch rejection threshold in microvolts; 0 disables it.",
            ),
            "reject_by_annotation": DemoParameter(
                id="reject_by_annotation",
                label="Reject annotated bad spans",
                value_type="boolean",
                default=None,
                required=True,
                description="Explicitly choose whether overlapping BAD annotations reject epochs.",
            ),
        },
        cacheable=False,
    ),
    "eeg.erp_average": DemoNodeManifest(
        version="0.2.0",
        inputs={"epochs": True},
        outputs={"evoked": True},
        handler=eeg_erp_adapter,
        cacheable=False,
    ),
    "eeg.psd": DemoNodeManifest(
        version="0.2.0",
        inputs={"epochs": True},
        outputs={"spectrum": True},
        handler=eeg_psd_adapter,
        parameters={
            "fmin_hz": DemoParameter(
                id="fmin_hz",
                label="Minimum frequency (Hz)",
                value_type="number",
                default=None,
                required=True,
                description="Explicit lower Welch frequency bound.",
            ),
            "fmax_hz": DemoParameter(
                id="fmax_hz",
                label="Maximum frequency (Hz)",
                value_type="number",
                default=None,
                required=True,
                description="Explicit upper Welch frequency bound.",
            ),
            "n_fft": DemoParameter(
                id="n_fft",
                label="FFT points",
                value_type="integer",
                default=None,
                required=True,
                description="Welch FFT length; must be at least n_per_seg.",
            ),
            "n_per_seg": DemoParameter(
                id="n_per_seg",
                label="Samples per segment",
                value_type="integer",
                default=None,
                required=True,
                description="Welch segment length in samples.",
            ),
            "n_overlap": DemoParameter(
                id="n_overlap",
                label="Overlap samples",
                value_type="integer",
                default=None,
                required=True,
                description="Welch overlap in samples; smaller than n_per_seg.",
            ),
            "window": DemoParameter(
                id="window",
                label="Welch window",
                value_type="string",
                default=None,
                required=True,
                description="Explicit window: hamming, hann, or boxcar.",
            ),
        },
        cacheable=False,
    ),
    "output.report": DemoNodeManifest(
        version="0.2.0",
        inputs={"evoked": True, "spectrum": True, "diagnostics": True},
        outputs={"report": True},
        handler=eeg_report_adapter,
        cacheable=False,
    ),
}

WORKER_NODES = {**DEMO_NODES, **EEG_NODES}
