"""MNE-Python implementation for BrainLearn's experimental EEG filter node."""

from __future__ import annotations

import json
import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

FILTER_PREVIEW_MEDIA_TYPE = "application/vnd.brainlearn.eeg-filter-preview+json"
MAX_FILTER_CHANNELS = 256
MAX_FILTER_CHANNEL_SAMPLES = 25_000_000
PREVIEW_DURATION_SECONDS = 4.0
PREVIEW_TRACE_BINS = 240
PREVIEW_CHANNELS = 4
PREVIEW_N_FFT = 1024

FILTER_SETTINGS: dict[str, Any] = {
    "method": "fir",
    "phase": "zero",
    "fir_design": "firwin",
    "fir_window": "hamming",
    "filter_length": "auto",
    "l_trans_bandwidth": "auto",
    "h_trans_bandwidth": "auto",
    "pad": "reflect_limited",
    "skip_by_annotation": ["edge", "bad_acq_skip"],
    "picks": "all EEG channels; non-EEG channels unchanged",
    "notch_operation": "MNE Raw.notch_filter after band-pass; skipped when no centers are selected",
    "notch_method": "fir",
    "notch_phase": "zero",
    "notch_width_unit": "Hz",
    "notch_trans_bandwidth_hz": 1.0,
}


class MneFilterError(ValueError):
    """A filter request is invalid or MNE could not create a safe result."""


@dataclass(frozen=True)
class MneFilterResult:
    filtered_path: Path
    preview_path: Path
    provenance_path: Path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _preview_data(raw: Any, channel_names: list[str], np: Any) -> dict[str, Any]:
    sampling_frequency = float(raw.info["sfreq"])
    stop_sample = min(raw.n_times, max(8, int(PREVIEW_DURATION_SECONDS * sampling_frequency)))
    if stop_sample < 8:
        raise MneFilterError("The recording is too short to build a filter comparison preview.")
    values = np.asarray(
        raw.get_data(
            picks=channel_names,
            start=0,
            stop=stop_sample,
            reject_by_annotation=None,
            units="uV",
            verbose="ERROR",
        ),
        dtype=float,
    )
    if values.shape != (len(channel_names), stop_sample) or not np.isfinite(values).all():
        raise MneFilterError("MNE returned invalid samples for the filter comparison preview.")

    bucket_count = min(PREVIEW_TRACE_BINS, stop_sample)
    traces: list[dict[str, Any]] = []
    for channel_index, channel_name in enumerate(channel_names):
        bins: list[dict[str, float]] = []
        for bucket_index in range(bucket_count):
            first = bucket_index * stop_sample // bucket_count
            last = (bucket_index + 1) * stop_sample // bucket_count
            segment = values[channel_index, first:last]
            bins.append(
                {
                    "time_seconds": (first + last - 1) / (2 * sampling_frequency),
                    "minimum_uv": float(segment.min()),
                    "maximum_uv": float(segment.max()),
                }
            )
        traces.append({"channel_name": channel_name, "bins": bins})

    n_per_seg = min(PREVIEW_N_FFT, stop_sample)
    n_overlap = n_per_seg // 2
    spectrum = raw.compute_psd(
        method="welch",
        fmin=0.0,
        fmax=min(100.0, sampling_frequency / 2),
        tmin=float(raw.first_time),
        tmax=float(raw.first_time) + (stop_sample - 1) / sampling_frequency,
        picks=channel_names,
        reject_by_annotation=False,
        n_fft=PREVIEW_N_FFT,
        n_per_seg=n_per_seg,
        n_overlap=n_overlap,
        window="hamming",
        average="mean",
        remove_dc=True,
        verbose="ERROR",
    )
    powers, frequencies = spectrum.get_data(return_freqs=True)
    power_uv2 = np.asarray(powers, dtype=float) * 1e12
    frequencies = np.asarray(frequencies, dtype=float)
    if (
        power_uv2.shape != (len(channel_names), len(frequencies))
        or not np.isfinite(power_uv2).all()
        or not np.isfinite(frequencies).all()
    ):
        raise MneFilterError("MNE returned invalid spectrum values for the comparison preview.")
    return {
        "traces": traces,
        "frequencies_hz": [float(value) for value in frequencies],
        "spectrum_traces": [
            {
                "channel_name": channel_name,
                "power_uv2_per_hz": [float(value) for value in power_uv2[index]],
            }
            for index, channel_name in enumerate(channel_names)
        ],
        "preview_sample_count": stop_sample,
        "preview_duration_seconds": stop_sample / sampling_frequency,
        "spectrum_method": "MNE Welch, Hamming window, mean average, reject_by_annotation=False",
        "spectrum_window": "hamming",
        "spectrum_average": "mean",
        "spectrum_remove_dc": True,
        "spectrum_reject_by_annotation": False,
        "spectrum_n_fft": PREVIEW_N_FFT,
        "spectrum_n_per_seg": n_per_seg,
        "spectrum_n_overlap": n_overlap,
    }


def filter_raw_eeg(
    raw: Any,
    *,
    low_hz: float,
    high_hz: float,
    notch_frequencies_hz: list[float],
    notch_width_hz: float,
    staging_dir: Path,
    source_identity: str,
    recording_path: str,
    source_files: list[dict[str, Any]],
    cancel_check: Callable[[], None],
) -> MneFilterResult:
    """Filter a BIDS Raw object and write its FIF, preview, and provenance artifacts."""

    if not 0.1 <= low_hz <= 100.0:
        raise MneFilterError("Low cutoff must be between 0.1 and 100 Hz.")
    if not 0.5 <= high_hz <= 200.0:
        raise MneFilterError("High cutoff must be between 0.5 and 200 Hz.")
    if low_hz >= high_hz:
        raise MneFilterError("Low cutoff must be lower than the high cutoff.")
    if not math.isfinite(notch_width_hz) or not 0.1 <= notch_width_hz <= 10.0:
        raise MneFilterError("Notch width must be between 0.1 and 10 Hz.")
    if notch_frequencies_hz != sorted(set(notch_frequencies_hz)):
        raise MneFilterError("Notch frequencies must be unique and listed in ascending order.")
    sampling_frequency = float(raw.info["sfreq"])
    nyquist_hz = sampling_frequency / 2
    if high_hz >= nyquist_hz:
        raise MneFilterError(
            f"High cutoff must be below this recording's Nyquist frequency ({nyquist_hz:g} Hz)."
        )
    if any(
        not math.isfinite(frequency) or not low_hz < frequency < high_hz
        for frequency in notch_frequencies_hz
    ):
        raise MneFilterError(
            "Each notch center must be finite and strictly inside the configured pass band."
        )
    if len(raw.ch_names) > MAX_FILTER_CHANNELS:
        raise MneFilterError(
            f"This filter supports at most {MAX_FILTER_CHANNELS} recording channels."
        )
    if raw.n_times * len(raw.ch_names) > MAX_FILTER_CHANNEL_SAMPLES:
        raise MneFilterError(
            "This recording exceeds the filter's 25 million channel-sample limit. "
            "Use a shorter recording or a reduced-channel copy."
        )

    channel_types = raw.get_channel_types()
    eeg_names = [
        name
        for name, channel_type in zip(raw.ch_names, channel_types, strict=True)
        if channel_type == "eeg"
    ]
    if not eeg_names:
        raise MneFilterError("The selected recording has no EEG channels to filter.")

    raw.load_data(verbose="ERROR")
    cancel_check()
    preview_names = eeg_names[:PREVIEW_CHANNELS]
    np = import_module("numpy")
    before_preview = _preview_data(raw, preview_names, np)
    filtered = raw.copy()
    try:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            filtered.filter(
                l_freq=low_hz,
                h_freq=high_hz,
                picks=eeg_names,
                method="fir",
                phase="zero",
                fir_design="firwin",
                fir_window="hamming",
                filter_length="auto",
                l_trans_bandwidth="auto",
                h_trans_bandwidth="auto",
                skip_by_annotation=("edge", "bad_acq_skip"),
                pad="reflect_limited",
                verbose="ERROR",
            )
            if notch_frequencies_hz:
                filtered.notch_filter(
                    freqs=notch_frequencies_hz,
                    picks=eeg_names,
                    filter_length="auto",
                    notch_widths=notch_width_hz,
                    trans_bandwidth=1.0,
                    method="fir",
                    phase="zero",
                    fir_window="hamming",
                    fir_design="firwin",
                    skip_by_annotation=("edge", "bad_acq_skip"),
                    pad="reflect_limited",
                    verbose="ERROR",
                )
            warning_messages = [
                f"{item.category.__name__}: {str(item.message)[:512]}" for item in captured[:20]
            ]
        cancel_check()
        after_preview = _preview_data(filtered, preview_names, np)
        staging_dir.mkdir(parents=True, exist_ok=True)
        filtered_path = staging_dir / "filtered-raw.fif"
        preview_path = staging_dir / "filter-preview.json"
        provenance_path = staging_dir / "filter-provenance.json"
        filtered.save(str(filtered_path), fmt="single", overwrite=False, verbose="ERROR")
        _write_json(
            preview_path,
            {
                "schema_version": "1.0",
                "source_content_identity": source_identity,
                "recording_path": recording_path,
                "sampling_frequency_hz": sampling_frequency,
                "channels": preview_names,
                "unit": "µV",
                "spectrum_unit": "µV²/Hz",
                "before": before_preview,
                "after": after_preview,
                "note": (
                    "Bounded signal and spectrum comparison for filter inspection; it is not a "
                    "signal-quality assessment or evidence of scientific suitability."
                ),
            },
        )
        mne = import_module("mne")
        mne_bids = import_module("mne_bids")
        _write_json(
            provenance_path,
            {
                "schema_version": "1.0",
                "operation": "mne.raw.filter",
                "node_version": "1.1.0",
                "plugin_version": "0.2.0",
                "source_content_identity": source_identity,
                "source_files": source_files,
                "recording_path": recording_path,
                "parameters": {
                    "low_hz": low_hz,
                    "high_hz": high_hz,
                    "notch_frequencies_hz": notch_frequencies_hz,
                    "notch_width_hz": notch_width_hz,
                    "unit": "Hz",
                },
                "sampling_frequency_hz": sampling_frequency,
                "nyquist_frequency_hz": nyquist_hz,
                "filtered_channel_names": eeg_names,
                "settings": FILTER_SETTINGS,
                "warnings": warning_messages,
                "software": {"mne": mne.__version__, "mne-bids": mne_bids.__version__},
                "citations": [
                    {
                        "title": "MEG and EEG data analysis with MNE-Python",
                        "doi": "10.3389/fnins.2013.00267",
                        "url": "https://doi.org/10.3389/fnins.2013.00267",
                    },
                    {
                        "title": (
                            "MNE-BIDS: Organizing electrophysiological data into the BIDS "
                            "format and facilitating their analysis"
                        ),
                        "doi": "10.21105/joss.01896",
                        "url": "https://doi.org/10.21105/joss.01896",
                    },
                ],
            },
        )
        return MneFilterResult(filtered_path, preview_path, provenance_path)
    finally:
        filtered.close()
