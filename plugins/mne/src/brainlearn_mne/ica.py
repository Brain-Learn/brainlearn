"""MNE-Python implementation for the experimental EEG ICA review workflow."""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

ICA_DIAGNOSTICS_MEDIA_TYPE = "application/vnd.brainlearn.eeg-ica-diagnostics+json"
ICA_PROVENANCE_SCHEMA = "1.1"
ICA_MAX_ITERATIONS = 1000
MAX_ICA_CHANNELS = 256
MAX_ICA_CHANNEL_SAMPLES = 25_000_000
MAX_COMPONENTS = 256
ICA_PREVIEW_SECONDS = 4.0
ICA_TRACE_BINS = 160
ICA_N_FFT = 512


class MneIcaError(ValueError):
    """The ICA fit or reviewed application could not safely complete."""


@dataclass(frozen=True)
class MneIcaFitResult:
    model_path: Path
    diagnostics_path: Path
    provenance_path: Path
    component_count: int


@dataclass(frozen=True)
class MneIcaApplyResult:
    reviewed_path: Path
    provenance_path: Path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise MneIcaError(f"ICA output directory {path.parent.name!r} is unsafe.")
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(
                json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
                + "\n"
            )
    except FileExistsError as exc:
        raise MneIcaError(
            f"ICA output {path.name!r} already exists; refusing to overwrite it."
        ) from exc
    except OSError as exc:
        raise MneIcaError(f"ICA output {path.name!r} could not be written safely.") from exc


def _raw_bounds(raw: Any) -> tuple[list[str], float, int]:
    bad_channels = set(raw.info.get("bads", ()))
    channels = [
        name
        for name, kind in zip(raw.ch_names, raw.get_channel_types(), strict=True)
        if kind == "eeg" and name not in bad_channels
    ]
    sampling_frequency = float(raw.info["sfreq"])
    if not channels:
        raise MneIcaError("ICA fitting requires at least one EEG channel.")
    if len(channels) > MAX_ICA_CHANNELS:
        raise MneIcaError(f"ICA fitting supports at most {MAX_ICA_CHANNELS} EEG channels.")
    if len(channels) < 2:
        raise MneIcaError("ICA fitting requires at least two EEG channels.")
    if raw.n_times < 8 or len(channels) * raw.n_times > MAX_ICA_CHANNEL_SAMPLES:
        raise MneIcaError("The EEG recording is outside the supported ICA sample limits.")
    if sampling_frequency <= 0:
        raise MneIcaError("The EEG sampling frequency must be positive.")
    return channels, sampling_frequency, int(raw.n_times)


def _bad_eeg_channels(raw: Any) -> list[str]:
    bad_channels = set(raw.info.get("bads", ()))
    return [
        name
        for name, kind in zip(raw.ch_names, raw.get_channel_types(), strict=True)
        if kind == "eeg" and name in bad_channels
    ]


def _locations(raw: Any, channel_names: list[str]) -> dict[str, list[float]]:
    montage = raw.get_montage()
    if montage is None:
        return {}
    positions = montage.get_positions().get("ch_pos", {})
    result: dict[str, list[float]] = {}
    for name in channel_names:
        position = positions.get(name)
        if position is not None:
            result[name] = [float(value) for value in position]
    return result


def _component_diagnostics(
    raw: Any,
    ica: Any,
    np: Any,
    *,
    fit_iterations: int,
    excluded_bad_channels: list[str],
) -> dict[str, Any]:
    channel_names = list(ica.ch_names)
    sfreq = float(raw.info["sfreq"])
    sample_count = min(raw.n_times, max(8, int(ICA_PREVIEW_SECONDS * sfreq)))
    sources = ica.get_sources(raw.copy()).get_data(start=0, stop=sample_count, verbose="ERROR")
    maps = np.asarray(ica.get_components(), dtype=float)
    if (
        sources.shape != (int(ica.n_components_), sample_count)
        or maps.shape != (len(channel_names), int(ica.n_components_))
        or not np.isfinite(sources).all()
        or not np.isfinite(maps).all()
    ):
        raise MneIcaError("MNE returned invalid component maps or sources.")

    bins = min(ICA_TRACE_BINS, sample_count)
    time_courses: list[dict[str, Any]] = []
    for component_index, values in enumerate(sources):
        points: list[dict[str, float]] = []
        for bin_index in range(bins):
            first = bin_index * sample_count // bins
            last = max(first + 1, (bin_index + 1) * sample_count // bins)
            segment = values[first:last]
            points.append(
                {
                    "time_seconds": (first + last - 1) / (2 * sfreq),
                    "minimum": float(segment.min()),
                    "maximum": float(segment.max()),
                }
            )
        time_courses.append({"component": component_index, "points": points})

    n_per_seg = min(ICA_N_FFT, sample_count)
    spectrum = ica.get_sources(raw.copy()).compute_psd(
        method="welch",
        picks="all",
        fmin=0.0,
        fmax=min(100.0, sfreq / 2),
        tmin=float(raw.first_time),
        tmax=float(raw.first_time) + (sample_count - 1) / sfreq,
        n_fft=ICA_N_FFT,
        n_per_seg=n_per_seg,
        n_overlap=n_per_seg // 2,
        window="hamming",
        average="mean",
        remove_dc=True,
        verbose="ERROR",
    )
    powers, frequencies = spectrum.get_data(return_freqs=True)
    powers = np.asarray(powers, dtype=float)
    frequencies = np.asarray(frequencies, dtype=float)
    if (
        powers.shape != (int(ica.n_components_), len(frequencies))
        or not np.isfinite(powers).all()
        or not np.isfinite(frequencies).all()
    ):
        raise MneIcaError("MNE returned an invalid component spectrum.")

    return {
        "schema_version": "1.1",
        "sampling_frequency_hz": sfreq,
        "channels": channel_names,
        "excluded_bad_channels": excluded_bad_channels,
        "fit_converged": True,
        "fit_iterations": fit_iterations,
        "component_count": int(ica.n_components_),
        "component_maps": [
            {
                "component": index,
                "channel_weights": [float(value) for value in maps[:, index]],
            }
            for index in range(maps.shape[1])
        ],
        "channel_locations_m": _locations(raw, channel_names),
        "topography_available": len(_locations(raw, channel_names)) == len(channel_names),
        "time_course_unit": "arbitrary ICA source units",
        "time_courses": time_courses,
        "spectrum_unit": "arbitrary units squared per Hz",
        "spectrum_method": "MNE Welch, Hamming window, mean average, remove DC",
        "spectrum_n_fft": ICA_N_FFT,
        "spectrum_n_per_seg": n_per_seg,
        "spectrum_n_overlap": n_per_seg // 2,
        "frequencies_hz": [float(value) for value in frequencies],
        "spectra": [
            {"component": index, "power": [float(value) for value in row]}
            for index, row in enumerate(powers)
        ],
        "preview_sample_count": sample_count,
        "preview_duration_seconds": sample_count / sfreq,
        "note": (
            "Component diagnostics describe this fitted model. Channel-weight plots are not "
            "sensor topographies unless channel locations are available."
        ),
    }


def fit_ica(
    raw: Any,
    *,
    random_state: int,
    staging_dir: Path,
    source_identity: str,
    input_artifact_id: str,
    cancel_check: Callable[[], None],
) -> MneIcaFitResult:
    """Fit deterministic FastICA and save the unreviewed model and diagnostics."""

    channels, _sfreq, sample_count = _raw_bounds(raw)
    excluded_bad_channels = _bad_eeg_channels(raw)
    if not 0 <= random_state <= 2**32 - 1:
        raise MneIcaError("The run seed must fit MNE's unsigned 32-bit random-state range.")
    mne = import_module("mne")
    np = import_module("numpy")
    if staging_dir.is_symlink():
        raise MneIcaError("ICA staging directory cannot be a symlink.")
    staging_dir.mkdir(parents=True, exist_ok=True)
    if staging_dir.is_symlink() or not staging_dir.is_dir():
        raise MneIcaError("ICA staging directory is unsafe.")
    model_path = staging_dir / "ica-model-ica.fif"
    diagnostics_path = staging_dir / "ica-diagnostics.json"
    provenance_path = staging_dir / "ica-fit-provenance.json"
    for output_path in (model_path, diagnostics_path, provenance_path):
        if output_path.is_symlink() or output_path.exists():
            raise MneIcaError(
                f"ICA output {output_path.name!r} already exists or is unsafe; "
                "refusing to overwrite it."
            )
    cancel_check()
    ica = mne.preprocessing.ICA(
        n_components=None,
        method="fastica",
        random_state=random_state,
        max_iter=ICA_MAX_ITERATIONS,
        fit_params=None,
        allow_ref_meg=False,
        verbose="ERROR",
    )
    from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", ConvergenceWarning)
            ica.fit(
                raw.copy(),
                picks=channels,
                reject_by_annotation=False,
                decim=1,
                verbose="ERROR",
            )
    except Exception as exc:
        raise MneIcaError(f"MNE could not fit ICA to this recording: {exc}") from exc
    if any(issubclass(item.category, ConvergenceWarning) for item in captured):
        raise MneIcaError(
            f"FastICA did not converge within {ICA_MAX_ITERATIONS} iterations. "
            "No reviewable model was saved. Review channel selection, data rank, filtering, "
            "and recording quality before retrying."
        )
    fit_iterations_value = getattr(ica, "n_iter_", None)
    if type(fit_iterations_value) is not int or not 1 <= fit_iterations_value < ICA_MAX_ITERATIONS:
        raise MneIcaError(
            "MNE did not report a converged FastICA iteration count. No reviewable model was saved."
        )
    fit_iterations = fit_iterations_value
    if list(ica.ch_names) != channels:
        raise MneIcaError("MNE fitted a channel set that differs from the recorded ICA inputs.")
    cancel_check()
    component_count = int(ica.n_components_)
    if component_count < 1 or component_count > MAX_COMPONENTS:
        raise MneIcaError(f"MNE produced an unsupported component count ({component_count}).")

    try:
        ica.save(str(model_path), overwrite=False, verbose="ERROR")
        diagnostics = _component_diagnostics(
            raw,
            ica,
            np,
            fit_iterations=fit_iterations,
            excluded_bad_channels=excluded_bad_channels,
        )
    except MneIcaError:
        raise
    except Exception as exc:
        raise MneIcaError(f"MNE could not save ICA diagnostics: {exc}") from exc
    _write_json(diagnostics_path, diagnostics)
    _write_json(
        provenance_path,
        {
            "schema_version": ICA_PROVENANCE_SCHEMA,
            "operation": "fit_ica",
            "source_content_identity": source_identity,
            "input_artifact_id": input_artifact_id,
            "method": "fastica",
            "n_components": None,
            "fitted_components": component_count,
            "random_state": random_state,
            "max_iter": ICA_MAX_ITERATIONS,
            "reject_by_annotation": False,
            "decim": 1,
            "picks": channels,
            "channels": list(ica.ch_names),
            "excluded_bad_channels": excluded_bad_channels,
            "converged": True,
            "iterations": fit_iterations,
            "sample_count": sample_count,
            "excluded_components": [],
            "exclusions_applied": False,
            "mne_version": mne.__version__,
            "mne_reference": "https://doi.org/10.3389/fnins.2013.00267",
        },
    )
    return MneIcaFitResult(model_path, diagnostics_path, provenance_path, component_count)


def apply_ica(
    raw: Any,
    *,
    ica_path: Path,
    excluded_components: list[int],
    staging_dir: Path,
    provenance: dict[str, Any],
    cancel_check: Callable[[], None],
) -> MneIcaApplyResult:
    """Apply only the persisted researcher exclusions to a copy of the raw data."""

    mne = import_module("mne")
    if staging_dir.is_symlink():
        raise MneIcaError("ICA staging directory cannot be a symlink.")
    staging_dir.mkdir(parents=True, exist_ok=True)
    if staging_dir.is_symlink() or not staging_dir.is_dir():
        raise MneIcaError("ICA staging directory is unsafe.")
    cancel_check()
    try:
        ica = mne.preprocessing.read_ica(str(ica_path), verbose="ERROR")
    except Exception as exc:
        raise MneIcaError(f"The fitted ICA artifact could not be read: {exc}") from exc
    component_count = int(ica.n_components_)
    if len(set(excluded_components)) != len(excluded_components) or any(
        type(index) is not int or index < 0 or index >= component_count
        for index in excluded_components
    ):
        raise MneIcaError("One or more selected ICA components are outside the fitted model.")
    reviewed = raw.copy()
    reviewed_path = staging_dir / "reviewed-raw.fif"
    provenance_path = staging_dir / "ica-review-provenance.json"
    for output_path in (reviewed_path, provenance_path):
        if output_path.is_symlink() or output_path.exists():
            raise MneIcaError(
                f"ICA output {output_path.name!r} already exists or is unsafe; "
                "refusing to overwrite it."
            )
    try:
        ica.apply(reviewed, exclude=sorted(excluded_components), verbose="ERROR")
        cancel_check()
        reviewed.save(str(reviewed_path), overwrite=False, verbose="ERROR")
    except Exception as exc:
        raise MneIcaError(f"MNE could not apply the reviewed ICA exclusions: {exc}") from exc
    _write_json(
        provenance_path,
        {
            **provenance,
            "schema_version": ICA_PROVENANCE_SCHEMA,
            "operation": "apply_reviewed_ica",
            "method": "MNE ICA.apply",
            "fitted_components": component_count,
            "excluded_components": sorted(excluded_components),
            "exclusions_applied": True,
            "mne_version": mne.__version__,
            "mne_reference": "https://doi.org/10.3389/fnins.2013.00267",
        },
    )
    return MneIcaApplyResult(reviewed_path, provenance_path)
