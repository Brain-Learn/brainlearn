"""Safe orchestration for the optional MNE ICA fit and review adapters."""

from __future__ import annotations

import json
import shutil
from importlib import import_module
from pathlib import Path
from typing import Any, TypeGuard

from brainlearn_server.demo_nodes import (
    ControlledFailure,
    NodeContext,
    ReviewPauseRequested,
    StagedOutput,
    _require_cancel,
)

ICA_DIAGNOSTICS_MEDIA_TYPE = "application/vnd.brainlearn.eeg-ica-diagnostics+json"
ICA_FIT_PROVENANCE_MEDIA_TYPE = "application/vnd.brainlearn.eeg-ica-fit-provenance+json"
ICA_REVIEW_PROVENANCE_MEDIA_TYPE = "application/vnd.brainlearn.eeg-ica-review-provenance+json"
ICA_MODEL_MEDIA_TYPE = "application/vnd.brainlearn.eeg-ica-model+fif"
_REFERENCE_MEDIA_TYPE = "application/vnd.brainlearn.artifact-reference+json"
ICA_FIT_PROVENANCE_SCHEMA = "1.1"
ICA_FIT_MAX_ITERATIONS = 1000


def is_current_converged_fit_provenance(
    payload: object, raw_artifact_id: str
) -> TypeGuard[dict[str, Any]]:
    """Require complete current-schema evidence before ICA decisions or application."""

    if not isinstance(payload, dict):
        return False
    channels = payload.get("channels")
    excluded_bad_channels = payload.get("excluded_bad_channels")
    fitted_components = payload.get("fitted_components")
    iterations = payload.get("iterations")
    if (
        payload.get("schema_version") != ICA_FIT_PROVENANCE_SCHEMA
        or payload.get("operation") != "fit_ica"
        or payload.get("input_artifact_id") != raw_artifact_id
        or payload.get("method") != "fastica"
        or payload.get("converged") is not True
        or type(payload.get("max_iter")) is not int
        or payload.get("max_iter") != ICA_FIT_MAX_ITERATIONS
        or type(fitted_components) is not int
        or fitted_components < 1
        or not isinstance(channels, list)
        or not channels
        or not all(isinstance(item, str) and item for item in channels)
        or len(set(channels)) != len(channels)
        or fitted_components > len(channels)
        or payload.get("picks") != channels
        or not isinstance(excluded_bad_channels, list)
        or not all(isinstance(item, str) and item for item in excluded_bad_channels)
        or len(set(excluded_bad_channels)) != len(excluded_bad_channels)
        or any(item in channels for item in excluded_bad_channels)
        or type(iterations) is not int
        or not 1 <= iterations < ICA_FIT_MAX_ITERATIONS
    ):
        return False
    return True


def _verified_copy(ctx: NodeContext, port: str, target: Path) -> Any:
    if ctx.project_path is None or ctx.run_id is None or ctx.run_store is None:
        raise ControlledFailure("ICA processing requires a persisted run and project.")
    artifact = ctx.input_artifacts.get(port)
    if artifact is None:
        raise ControlledFailure(f"The ICA {port!r} input has no persisted artifact identity.")
    try:
        relative = target.relative_to(ctx.staging_dir)
    except ValueError as exc:
        raise ControlledFailure("The ICA input copy target must stay inside run staging.") from exc
    if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
        raise ControlledFailure("The ICA input copy target is unsafe.")
    current = ctx.staging_dir
    if current.is_symlink() or not current.is_dir():
        raise ControlledFailure("The ICA staging directory is unsafe or symlinked.")
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise ControlledFailure("The ICA input copy path contains an unsafe directory.")
    if target.is_symlink() or target.exists():
        raise ControlledFailure("The ICA input copy target already exists or is unsafe.")
    try:
        with (
            ctx.run_store.open_artifact_stream(ctx.project_path, ctx.run_id, artifact.artifact_id)[
                0
            ] as source,
            target.open("xb") as destination,
        ):
            shutil.copyfileobj(source, destination, 1024 * 1024)
    except (OSError, ValueError, FileNotFoundError) as exc:
        raise ControlledFailure(
            f"The ICA {port!r} input artifact changed or could not be verified."
        ) from exc
    _require_cancel(ctx)
    return artifact


def _ica_work_dir(ctx: NodeContext, name: str) -> Path:
    from brainlearn_server.worker import _owned_child

    return _owned_child(ctx.staging_dir, ctx.node_id, "ICA work directory", name)


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlledFailure(f"The {description} is unreadable or invalid JSON.") from exc
    if not isinstance(value, dict):
        raise ControlledFailure(f"The {description} is unreadable or invalid JSON.")
    return value


def _load_raw(ctx: NodeContext, port: str, work_dir: Path) -> tuple[Any, dict[str, Any] | None]:
    artifact = ctx.input_artifacts.get(port)
    if artifact is None:
        raise ControlledFailure(f"The ICA {port!r} input has no artifact identity.")
    target = (
        work_dir / f"{port}-input.json"
        if artifact.media_type == _REFERENCE_MEDIA_TYPE
        else work_dir / f"{port}-input_raw.fif"
    )
    copied = _verified_copy(ctx, port, target)
    if copied.media_type == _REFERENCE_MEDIA_TYPE:
        if ctx.project_store is None or ctx.project_path is None:
            raise ControlledFailure("Reading a BIDS EEG input requires an open project.")
        reference = _read_json(target, "BIDS EEG reference")
        if (
            reference.get("schema_version") != "1.0"
            or not isinstance(reference.get("dataset_path"), str)
            or not isinstance(reference.get("recording_path"), str)
            or not isinstance(reference.get("source_content_identity"), str)
        ):
            raise ControlledFailure("The upstream BIDS EEG reference is invalid.")
        from brainlearn_server.bids_eeg import (
            BidsDatasetPathError,
            BidsEegDiscoveryService,
            BidsSignalInspectionError,
        )

        service = BidsEegDiscoveryService(ctx.project_store)
        try:
            identity = service.identify(
                ctx.project_path, reference["dataset_path"], reference["recording_path"]
            )
            if (
                identity.status != "ready"
                or identity.content_identity != reference["source_content_identity"]
            ):
                raise ControlledFailure(
                    "The EEG recording changed after inspection. Run Inspect Signal again."
                )
            root, recording_relative, recording = service._resolve_signal_recording(
                ctx.project_path, reference["dataset_path"], reference["recording_path"]
            )
            raw = service._open_signal_raw(root, recording_relative, recording.format or "EEG")
        except ControlledFailure:
            raise
        except (BidsDatasetPathError, BidsSignalInspectionError) as exc:
            raise ControlledFailure(str(exc)) from exc
        return raw, reference
    if copied.media_type == "application/octet-stream" and copied.path.lower().endswith(".fif"):
        try:
            mne = import_module("mne")
            return mne.io.read_raw_fif(target, preload=False, verbose="ERROR"), None
        except Exception as exc:
            raise ControlledFailure(
                f"The ICA {port!r} FIF input could not be opened: {exc}"
            ) from exc
    raise ControlledFailure(f"The ICA {port!r} input must be a BIDS reference or MNE FIF artifact.")


def _verify_source_unchanged(ctx: NodeContext, reference: dict[str, Any] | None) -> None:
    if reference is None:
        return
    from brainlearn_server.bids_eeg import (
        BidsDatasetPathError,
        BidsEegDiscoveryService,
        BidsSignalInspectionError,
    )

    assert ctx.project_path is not None and ctx.project_store is not None
    try:
        identity = BidsEegDiscoveryService(ctx.project_store).identify(
            ctx.project_path, reference["dataset_path"], reference["recording_path"]
        )
    except (BidsDatasetPathError, BidsSignalInspectionError) as exc:
        raise ControlledFailure(str(exc)) from exc
    if (
        identity.status != "ready"
        or identity.content_identity != reference["source_content_identity"]
    ):
        raise ControlledFailure(
            "The source recording changed during ICA processing; output discarded."
        )


def fit_ica_eeg(ctx: NodeContext) -> list[StagedOutput]:
    """Fit ICA to a hash-verified input, without applying any component exclusions."""

    _require_cancel(ctx)
    work_dir = _ica_work_dir(ctx, "ica-work")
    raw = None
    try:
        artifact = ctx.input_artifacts.get("raw")
        if artifact is None:
            raise ControlledFailure("ICA fitting requires a persisted raw EEG artifact.")
        raw, reference = _load_raw(ctx, "raw", work_dir)

        eeg_raw = raw.copy().pick("eeg")
        n_samples = len(eeg_raw.ch_names) * int(eeg_raw.n_times)
        if n_samples > 25_000_000:
            raise ControlledFailure("The EEG data exceeds the bounded ICA input sample limit.")
        eeg_raw.load_data(verbose="ERROR")
        from brainlearn_mne.ica import (
            ICA_DIAGNOSTICS_MEDIA_TYPE as _PLUGIN_DIAGNOSTICS_MEDIA_TYPE,
        )
        from brainlearn_mne.ica import (
            MneIcaError,
            fit_ica,
        )

        try:
            result = fit_ica(
                eeg_raw,
                random_state=int(ctx.run_seed or 0),
                staging_dir=ctx.staging_dir,
                source_identity=(
                    reference["source_content_identity"]
                    if reference is not None
                    else artifact.artifact_id
                ),
                input_artifact_id=artifact.artifact_id,
                cancel_check=lambda: _require_cancel(ctx),
            )
        except MneIcaError as exc:
            raise ControlledFailure(str(exc)) from exc
        _verify_source_unchanged(ctx, reference)
        _require_cancel(ctx)
        return [
            StagedOutput("ica", result.model_path.name, ICA_MODEL_MEDIA_TYPE),
            StagedOutput(
                "diagnostics", result.diagnostics_path.name, _PLUGIN_DIAGNOSTICS_MEDIA_TYPE
            ),
            StagedOutput("provenance", result.provenance_path.name, ICA_FIT_PROVENANCE_MEDIA_TYPE),
        ]
    finally:
        if raw is not None:
            raw.close()
        shutil.rmtree(work_dir, ignore_errors=True)


def _review_record(ctx: NodeContext) -> Any | None:
    if ctx.node_run_id is None or ctx.node_id is None:
        return None
    return next((item for item in ctx.qc_reviews if item.node_run_id == ctx.node_run_id), None)


def _matching_fit_provenance(ctx: NodeContext) -> dict[str, Any]:
    fit_artifact = ctx.input_artifacts.get("fit")
    raw_artifact = ctx.input_artifacts.get("raw")
    if fit_artifact is None or raw_artifact is None:
        raise ControlledFailure(
            "ICA review requires both current raw data and a fitted ICA artifact."
        )
    if fit_artifact.media_type != ICA_MODEL_MEDIA_TYPE:
        raise ControlledFailure("The ICA review input is not a fitted MNE ICA model.")
    run = ctx.run_store.get_run(ctx.project_path, ctx.run_id)
    current_node = next((item for item in run.node_runs if item.id == ctx.node_run_id), None)
    input_sources = current_node.settings.get("input_sources", {}) if current_node else {}
    fit_source_id = (
        input_sources.get("fit", {}).get("node")
        if isinstance(input_sources, dict) and isinstance(input_sources.get("fit"), dict)
        else None
    )
    fit_source = next((item for item in run.node_runs if item.node_id == fit_source_id), None)
    if fit_source is None:
        raise ControlledFailure("The fitted ICA model is no longer present in this run.")
    if not any(item.artifact_id == fit_artifact.artifact_id for item in fit_source.artifacts):
        raise ControlledFailure("The fitted ICA model artifact is no longer current.")
    provenance_artifact = next(
        (item for item in fit_source.artifacts if item.port_id == "provenance"), None
    )
    if provenance_artifact is None:
        raise ControlledFailure("The fitted ICA model has no provenance artifact.")
    try:
        with ctx.run_store.open_artifact_stream(
            ctx.project_path, ctx.run_id, provenance_artifact.artifact_id
        )[0] as source:
            payload = json.load(source)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ControlledFailure("The fitted ICA provenance could not be verified.") from exc
    if not is_current_converged_fit_provenance(payload, raw_artifact.artifact_id):
        raise ControlledFailure(
            "The fitted ICA provenance is legacy, non-converged, or invalid; "
            "refit ICA before review or application."
        )
    return payload


def apply_reviewed_ica_eeg(ctx: NodeContext) -> list[StagedOutput]:
    """Persist a QC pause on the first pass, then apply its bound decision on resume."""

    _require_cancel(ctx)
    if ctx.project_path is None or ctx.run_store is None or ctx.run_id is None:
        raise ControlledFailure("ICA review requires a persisted run.")
    work_dir = _ica_work_dir(ctx, "ica-review-work")
    raw = None
    try:
        raw, reference = _load_raw(ctx, "raw", work_dir)
        provenance = _matching_fit_provenance(ctx)
        if reference is not None:
            source_identity = reference["source_content_identity"]
        else:
            source_identity = None
        record = _review_record(ctx)
        if record is None:
            _verify_source_unchanged(ctx, reference)
            raise ReviewPauseRequested(
                "Inspect the ICA component maps, time courses, spectra, and fit details. "
                "Record an explicit researcher decision before exclusions are applied."
            )
        expected_artifacts = {
            item.port_id: item.artifact_id for item in record.input_artifact_identities
        }
        actual_artifacts = {port: item.artifact_id for port, item in ctx.input_artifacts.items()}
        if (
            record.decision != "approved"
            or expected_artifacts != actual_artifacts
            or record.source_content_identity != source_identity
        ):
            raise ControlledFailure(
                "The saved ICA decision is stale or does not approve these exact input artifacts. "
                "Review the current fit again."
            )
        fitted_path = work_dir / "ica-fit-ica.fif"
        fit_artifact = _verified_copy(ctx, "fit", fitted_path)
        ica_path = fitted_path
        component_count = int(provenance.get("fitted_components", -1))
        exclusions = list(record.excluded_ica_components)
        if any(index >= component_count for index in exclusions):
            raise ControlledFailure("The saved ICA component choice is outside the fitted model.")
        if len(raw.ch_names) * int(raw.n_times) > 25_000_000:
            raise ControlledFailure(
                "The reviewed recording exceeds the bounded ICA application limit."
            )
        raw.load_data(verbose="ERROR")
        from brainlearn_mne.ica import MneIcaError, apply_ica

        try:
            result = apply_ica(
                raw,
                ica_path=ica_path,
                excluded_components=exclusions,
                staging_dir=ctx.staging_dir,
                provenance={
                    "source_content_identity": source_identity,
                    "reviewer": record.reviewer,
                    "decision": record.decision,
                    "decision_id": record.id,
                    "note": record.note,
                    "fit_artifact_id": fit_artifact.artifact_id,
                    "input_artifact_identities": actual_artifacts,
                    "fitted_components": component_count,
                },
                cancel_check=lambda: _require_cancel(ctx),
            )
        except MneIcaError as exc:
            raise ControlledFailure(str(exc)) from exc
        _verify_source_unchanged(ctx, reference)
        return [
            StagedOutput("reviewed", result.reviewed_path.name, "application/octet-stream"),
            StagedOutput(
                "provenance", result.provenance_path.name, ICA_REVIEW_PROVENANCE_MEDIA_TYPE
            ),
        ]
    finally:
        if raw is not None:
            raw.close()
        shutil.rmtree(work_dir, ignore_errors=True)
