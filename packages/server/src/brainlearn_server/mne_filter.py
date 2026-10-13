"""Safe BIDS and run-store orchestration for the optional MNE adapter."""

from __future__ import annotations

import json
import math

from brainlearn_server.demo_nodes import (
    CancelledByUser,
    ControlledFailure,
    NodeContext,
    StagedOutput,
    _require_cancel,
)


def _parameter(ctx: NodeContext, name: str, default: float) -> float:
    value = ctx.parameters.get(name, default)
    if isinstance(value, bool):
        raise ControlledFailure(f"Filter parameter {name!r} must be a finite number in Hz.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ControlledFailure(
            f"Filter parameter {name!r} must be a finite number in Hz."
        ) from None
    if not math.isfinite(result):
        raise ControlledFailure(f"Filter parameter {name!r} must be finite.")
    return result


def _notch_frequencies(ctx: NodeContext) -> list[float]:
    value = ctx.parameters.get("notch_hz", "")
    if not isinstance(value, str):
        raise ControlledFailure("Notch frequencies must be comma-separated numbers in Hz.")
    if not value.strip():
        return []
    if len(value) > 128:
        raise ControlledFailure("Enter at most 16 notch frequencies.")
    pieces = [piece.strip() for piece in value.split(",")]
    if len(pieces) > 16 or any(not piece for piece in pieces):
        raise ControlledFailure("Enter up to 16 comma-separated notch frequencies in Hz.")
    try:
        frequencies = [float(piece) for piece in pieces]
    except ValueError:
        raise ControlledFailure("Notch frequencies must be finite numbers in Hz.") from None
    if any(not math.isfinite(frequency) for frequency in frequencies):
        raise ControlledFailure("Notch frequencies must be finite numbers in Hz.")
    if frequencies != sorted(set(frequencies)):
        raise ControlledFailure("Notch frequencies must be unique and listed in ascending order.")
    return frequencies


def filter_bids_eeg(ctx: NodeContext) -> list[StagedOutput]:
    """Resolve and verify an inspected BIDS source, then call the MNE plugin lazily."""

    from brainlearn_server.bids_eeg import (
        BidsDatasetPathError,
        BidsEegDiscoveryService,
        BidsSignalInspectionError,
    )

    _require_cancel(ctx)
    if ctx.project_path is None or ctx.project_store is None:
        raise ControlledFailure("Band-pass filtering requires an open BrainLearn project.")
    try:
        source_reference = json.loads(ctx.inputs["raw"].read_text(encoding="utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledFailure("The upstream inspected EEG reference is invalid.") from exc
    if (
        not isinstance(source_reference, dict)
        or source_reference.get("schema_version") != "1.0"
        or not isinstance(source_reference.get("dataset_path"), str)
        or not isinstance(source_reference.get("recording_path"), str)
        or not isinstance(source_reference.get("source_content_identity"), str)
    ):
        raise ControlledFailure("The upstream inspected EEG reference is invalid.")

    low_hz = _parameter(ctx, "low_hz", 1.0)
    high_hz = _parameter(ctx, "high_hz", 40.0)
    notch_frequencies = _notch_frequencies(ctx)
    notch_width_hz = _parameter(ctx, "notch_width_hz", 1.0)
    dataset_path = source_reference["dataset_path"]
    recording_path = source_reference["recording_path"]
    expected_identity = source_reference["source_content_identity"]
    service = BidsEegDiscoveryService(ctx.project_store)
    try:
        identity_before = service.identify(ctx.project_path, dataset_path, recording_path)
        if (
            identity_before.status != "ready"
            or identity_before.content_identity != expected_identity
        ):
            raise ControlledFailure(
                "The source recording changed after inspection. Run Inspect Signal again "
                "before filtering."
            )
        root, recording_relative, recording = service._resolve_signal_recording(
            ctx.project_path, dataset_path, recording_path
        )
        raw = service._open_signal_raw(root, recording_relative, recording.format or "EEG")
    except ControlledFailure:
        raise
    except (BidsDatasetPathError, BidsSignalInspectionError) as exc:
        raise ControlledFailure(str(exc)) from exc

    try:
        _require_cancel(ctx)
        from brainlearn_mne.filtering import FILTER_PREVIEW_MEDIA_TYPE, MneFilterError
        from brainlearn_mne.filtering import filter_raw_eeg as apply_filter

        try:
            result = apply_filter(
                raw,
                low_hz=low_hz,
                high_hz=high_hz,
                notch_frequencies_hz=notch_frequencies,
                notch_width_hz=notch_width_hz,
                staging_dir=ctx.staging_dir,
                source_identity=expected_identity,
                recording_path=recording_relative,
                source_files=[item.model_dump(mode="json") for item in identity_before.files],
                cancel_check=lambda: _require_cancel(ctx),
            )
        except MneFilterError as exc:
            raise ControlledFailure(str(exc)) from exc
        except CancelledByUser:
            raise
        except Exception as exc:
            raise ControlledFailure(f"MNE could not filter this EEG recording: {exc}") from exc

        identity_after = service.identify(ctx.project_path, dataset_path, recording_path)
        if identity_after.status != "ready" or identity_after.content_identity != expected_identity:
            raise ControlledFailure(
                "The source recording changed during filtering; the result was discarded."
            )
        _require_cancel(ctx)
        return [
            StagedOutput(
                port_id="filtered",
                relative_path=result.filtered_path.relative_to(ctx.staging_dir).as_posix(),
                media_type="application/octet-stream",
            ),
            StagedOutput(
                port_id="preview",
                relative_path=result.preview_path.relative_to(ctx.staging_dir).as_posix(),
                media_type=FILTER_PREVIEW_MEDIA_TYPE,
            ),
            StagedOutput(
                port_id="provenance",
                relative_path=result.provenance_path.relative_to(ctx.staging_dir).as_posix(),
                media_type="application/json",
            ),
        ]
    finally:
        raw.close()
