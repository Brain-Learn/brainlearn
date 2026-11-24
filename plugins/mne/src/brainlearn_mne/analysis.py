"""Descriptive event-locked EEG analyses implemented with MNE-Python.

All method settings are supplied by the workflow. The values carried in this
module are validation bounds only; there are no scientific defaults here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any


class MneAnalysisError(ValueError):
    """An event, epoch, ERP, or PSD request cannot be executed as specified."""


@dataclass(frozen=True)
class EpochResult:
    epochs: Any
    diagnostics: dict[str, Any]


def create_epochs(
    raw: Any,
    *,
    event_label: str,
    tmin_seconds: float,
    tmax_seconds: float,
    baseline_enabled: bool,
    baseline_start_seconds: float,
    baseline_end_seconds: float,
    reject_eeg_peak_to_peak_uv: float,
    reject_by_annotation: bool,
    cancel_check: Callable[[], None],
) -> EpochResult:
    """Select one exact annotation label and create explicitly configured epochs."""

    if not isinstance(event_label, str) or not event_label.strip():
        raise MneAnalysisError("Choose an exact, non-empty event label from Event Diagnostics.")
    if tmin_seconds >= tmax_seconds:
        raise MneAnalysisError("Epoch start time must be earlier than the epoch end time.")
    if baseline_enabled and not (
        tmin_seconds <= baseline_start_seconds <= baseline_end_seconds <= tmax_seconds
    ):
        raise MneAnalysisError(
            "Baseline bounds must be ordered and lie inside the requested epoch window."
        )
    if reject_eeg_peak_to_peak_uv < 0:
        raise MneAnalysisError("EEG peak-to-peak rejection must be zero or a positive value in µV.")
    if not raw.annotations:
        raise MneAnalysisError(
            "The recording has no annotations to select. Add or correct BIDS events.tsv, then "
            "reinspect the recording."
        )

    mne = import_module("mne")
    annotation_counts = Counter(str(item) for item in raw.annotations.description)
    available = sorted(annotation_counts)
    if event_label not in annotation_counts:
        preview = ", ".join(repr(item) for item in available[:12]) or "none"
        suffix = f" (and {len(available) - 12} more)" if len(available) > 12 else ""
        raise MneAnalysisError(
            f"Event label {event_label!r} was not found. Available labels: {preview}{suffix}. "
            "Choose one exact label from Event Diagnostics."
        )

    cancel_check()
    events, event_id = mne.events_from_annotations(
        raw, event_id={event_label: 1}, regexp=None, verbose="ERROR"
    )
    if event_label not in event_id or len(events) == 0:
        raise MneAnalysisError(
            f"Event label {event_label!r} has no usable event onsets. Check the source event "
            "timing and annotation descriptions."
        )

    reject = {"eeg": reject_eeg_peak_to_peak_uv * 1e-6} if reject_eeg_peak_to_peak_uv > 0 else None
    baseline = (baseline_start_seconds, baseline_end_seconds) if baseline_enabled else None
    try:
        epochs = mne.Epochs(
            raw,
            events,
            event_id={event_label: event_id[event_label]},
            tmin=tmin_seconds,
            tmax=tmax_seconds,
            baseline=baseline,
            picks="eeg",
            preload=True,
            reject=reject,
            reject_by_annotation=reject_by_annotation,
            event_repeated="error",
            on_missing="raise",
            verbose="ERROR",
        )
    except Exception as exc:
        raise MneAnalysisError(
            f"MNE could not create epochs for {event_label!r}: {type(exc).__name__}: {exc}"
        ) from exc
    cancel_check()

    drop_counts: Counter[str] = Counter()
    for entry in epochs.drop_log:
        if entry:
            drop_counts.update(entry)
    if len(epochs) == 0:
        drop_summary = dict(sorted(drop_counts.items())) or {
            "no matching event in epoch bounds": len(events)
        }
        raise MneAnalysisError(
            f"Zero epochs were retained for {event_label!r}; MNE drop reasons: "
            f"{drop_summary}. "
            "Review event timing, epoch bounds, annotation rejection, and the explicit EEG "
            "peak-to-peak threshold."
        )

    diagnostics = {
        "schema_version": "1.0",
        "operation": "event_selection_and_epoching",
        "annotation_count": len(raw.annotations),
        "available_event_counts": dict(sorted(annotation_counts.items())),
        "selected_event_label": event_label,
        "selected_event_count": int(len(events)),
        "retained_epoch_count": int(len(epochs)),
        "dropped_epoch_count": int(len(events) - len(epochs)),
        "drop_reasons": dict(sorted(drop_counts.items())),
        "tmin_seconds": float(tmin_seconds),
        "tmax_seconds": float(tmax_seconds),
        "baseline_enabled": baseline_enabled,
        "baseline_seconds": list(baseline) if baseline is not None else None,
        "reject_eeg_peak_to_peak_uv": float(reject_eeg_peak_to_peak_uv),
        "amplitude_rejection_enabled": reject is not None,
        "reject_by_annotation": reject_by_annotation,
        "mne_version": import_module("mne").__version__,
    }
    return EpochResult(epochs=epochs, diagnostics=diagnostics)


def erp_summary(epochs: Any, *, cancel_check: Callable[[], None]) -> dict[str, Any]:
    """Return participant-level ERP mean and across-epoch SD in microvolts."""

    np = import_module("numpy")
    cancel_check()
    data = np.asarray(epochs.get_data(picks="eeg", units="uV", verbose="ERROR"), dtype=float)
    if data.ndim != 3 or data.shape[0] < 1 or not np.isfinite(data).all():
        raise MneAnalysisError("The retained epochs do not contain finite EEG samples for ERP.")
    mean = data.mean(axis=0)
    sd = data.std(axis=0, ddof=1) if data.shape[0] > 1 else np.zeros_like(mean)
    times = np.asarray(epochs.times, dtype=float)
    cancel_check()
    return {
        "schema_version": "1.0",
        "operation": "erp_average",
        "channel_names": list(epochs.ch_names),
        "times_seconds": [float(value) for value in times],
        "mean_uv": mean.tolist(),
        "within_participant_epoch_sd_uv": sd.tolist(),
        "epoch_count": int(data.shape[0]),
        "uncertainty_definition": (
            "sample standard deviation across retained epochs at each channel and time point; "
            "zero when one epoch is retained"
        ),
        "mne_version": import_module("mne").__version__,
    }


def psd_summary(
    epochs: Any,
    *,
    fmin_hz: float,
    fmax_hz: float,
    n_fft: int,
    n_per_seg: int,
    n_overlap: int,
    window: str,
    cancel_check: Callable[[], None],
) -> dict[str, Any]:
    """Return participant-level Welch power and across-epoch SD in µV²/Hz."""

    if fmin_hz < 0 or fmin_hz >= fmax_hz:
        raise MneAnalysisError("PSD frequency bounds must satisfy 0 ≤ fmin < fmax.")
    if n_fft < 2 or n_per_seg < 2 or n_overlap < 0 or n_overlap >= n_per_seg:
        raise MneAnalysisError("PSD requires n_fft ≥ 2 and 0 ≤ n_overlap < n_per_seg.")
    if n_fft < n_per_seg:
        raise MneAnalysisError("PSD n_fft must be greater than or equal to n_per_seg.")
    if window not in {"hamming", "hann", "boxcar"}:
        raise MneAnalysisError("PSD window must be one of hamming, hann, or boxcar.")

    np = import_module("numpy")
    sfreq = float(epochs.info["sfreq"])
    if fmax_hz > sfreq / 2:
        raise MneAnalysisError(
            f"PSD upper frequency {fmax_hz:g} Hz exceeds Nyquist ({sfreq / 2:g} Hz). "
            "Choose a lower upper bound or use a recording with a higher sampling rate; "
            "BrainLearn does not resample recordings."
        )
    if n_per_seg > len(epochs.times):
        raise MneAnalysisError(
            f"PSD n_per_seg ({n_per_seg}) exceeds the epoch length ({len(epochs.times)} samples)."
        )
    cancel_check()
    try:
        spectrum = epochs.compute_psd(
            method="welch",
            fmin=fmin_hz,
            fmax=fmax_hz,
            picks="eeg",
            n_fft=n_fft,
            n_per_seg=n_per_seg,
            n_overlap=n_overlap,
            window=window,
            average="mean",
            remove_dc=True,
            verbose="ERROR",
        )
    except Exception as exc:
        raise MneAnalysisError(f"MNE could not estimate PSD: {type(exc).__name__}: {exc}") from exc
    data_v2 = np.asarray(spectrum.get_data(), dtype=float)
    frequencies = np.asarray(spectrum.freqs, dtype=float)
    if data_v2.ndim != 3 or data_v2.shape[0] == 0 or not np.isfinite(data_v2).all():
        raise MneAnalysisError("MNE returned invalid epoch-level PSD values.")
    data_uv2 = data_v2 * 1e12
    mean = data_uv2.mean(axis=0)
    sd = data_uv2.std(axis=0, ddof=1) if data_uv2.shape[0] > 1 else np.zeros_like(mean)
    cancel_check()
    return {
        "schema_version": "1.0",
        "operation": "welch_psd",
        "channel_names": list(epochs.ch_names),
        "frequencies_hz": [float(value) for value in frequencies],
        "mean_power_uv2_per_hz": mean.tolist(),
        "within_participant_epoch_sd_uv2_per_hz": sd.tolist(),
        "epoch_count": int(data_uv2.shape[0]),
        "units": "µV²/Hz",
        "estimator": {
            "method": "Welch",
            "window": window,
            "n_fft": n_fft,
            "n_per_seg": n_per_seg,
            "n_overlap": n_overlap,
            "within_epoch_segment_average": "arithmetic mean",
            "across_epochs": "arithmetic mean; sample SD also reported",
            "remove_dc": True,
            "annotation_rejection": (
                "Epoch rejection was applied upstream; PSD uses retained epochs."
            ),
        },
        "mne_version": import_module("mne").__version__,
    }
