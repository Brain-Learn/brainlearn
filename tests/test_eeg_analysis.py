"""Direct-MNE numerical checks for event, epoch, ERP, and PSD adapters."""

from __future__ import annotations

from importlib import import_module

import pytest
from brainlearn_mne.analysis import MneAnalysisError, create_epochs, erp_summary, psd_summary

mne = pytest.importorskip("mne")
np = import_module("numpy")


def _annotated_raw() -> object:
    sfreq = 100.0
    times = np.arange(600, dtype=float) / sfreq
    data = np.vstack(
        (
            12e-6 * np.sin(2 * np.pi * 8 * times),
            7e-6 * np.cos(2 * np.pi * 10 * times),
        )
    )
    raw = mne.io.RawArray(
        data,
        mne.create_info(["Cz", "Pz"], sfreq, ch_types=["eeg", "eeg"]),
        verbose="ERROR",
    )
    raw.set_annotations(
        mne.Annotations(
            onset=[0.05, 2.0, 4.0, 3.0],
            duration=[0, 0, 0, 0],
            description=["target", "target", "target", "other"],
        )
    )
    return raw


def _cancel_check() -> None:
    return None


def test_epochs_erp_and_psd_match_independent_direct_mne_calls() -> None:
    raw = _annotated_raw()
    result = create_epochs(
        raw,
        event_label="target",
        tmin_seconds=-0.2,
        tmax_seconds=0.8,
        baseline_enabled=True,
        baseline_start_seconds=-0.2,
        baseline_end_seconds=0.0,
        reject_eeg_peak_to_peak_uv=0.0,
        reject_by_annotation=True,
        cancel_check=_cancel_check,
    )

    direct_events, direct_event_id = mne.events_from_annotations(
        raw, event_id={"target": 1}, regexp=None, verbose="ERROR"
    )
    direct = mne.Epochs(
        raw,
        direct_events,
        event_id={"target": direct_event_id["target"]},
        tmin=-0.2,
        tmax=0.8,
        baseline=(-0.2, 0.0),
        picks="eeg",
        preload=True,
        reject=None,
        reject_by_annotation=True,
        event_repeated="error",
        on_missing="raise",
        verbose="ERROR",
    )
    np.testing.assert_allclose(result.epochs.get_data(), direct.get_data(), rtol=0, atol=0)
    assert result.diagnostics["available_event_counts"] == {"other": 1, "target": 3}
    assert result.diagnostics["selected_event_count"] == 3
    assert result.diagnostics["retained_epoch_count"] == 2
    assert result.diagnostics["drop_reasons"].get("NO_DATA") == 1

    erp = erp_summary(result.epochs, cancel_check=_cancel_check)
    direct_uv = direct.get_data(units="uV", verbose="ERROR")
    np.testing.assert_allclose(erp["mean_uv"], direct_uv.mean(axis=0), rtol=0, atol=1e-12)
    np.testing.assert_allclose(
        erp["within_participant_epoch_sd_uv"], direct_uv.std(axis=0, ddof=1), rtol=0, atol=1e-12
    )

    psd = psd_summary(
        result.epochs,
        fmin_hz=1.0,
        fmax_hz=40.0,
        n_fft=64,
        n_per_seg=64,
        n_overlap=32,
        window="hamming",
        cancel_check=_cancel_check,
    )
    direct_psd = direct.compute_psd(
        method="welch",
        fmin=1.0,
        fmax=40.0,
        picks="eeg",
        n_fft=64,
        n_per_seg=64,
        n_overlap=32,
        window="hamming",
        average="mean",
        remove_dc=True,
        verbose="ERROR",
    )
    expected = direct_psd.get_data() * 1e12
    np.testing.assert_allclose(psd["frequencies_hz"], direct_psd.freqs, rtol=0, atol=0)
    np.testing.assert_allclose(
        psd["mean_power_uv2_per_hz"], expected.mean(axis=0), rtol=0, atol=1e-12
    )
    np.testing.assert_allclose(
        psd["within_participant_epoch_sd_uv2_per_hz"],
        expected.std(axis=0, ddof=1),
        rtol=0,
        atol=1e-12,
    )


def test_missing_event_label_reports_available_exact_labels() -> None:
    with pytest.raises(MneAnalysisError, match="Available labels: 'other', 'target'"):
        create_epochs(
            _annotated_raw(),
            event_label="stimulus",
            tmin_seconds=-0.2,
            tmax_seconds=0.8,
            baseline_enabled=False,
            baseline_start_seconds=-0.2,
            baseline_end_seconds=0.0,
            reject_eeg_peak_to_peak_uv=0,
            reject_by_annotation=True,
            cancel_check=_cancel_check,
        )


def test_ambiguous_event_prefix_is_not_matched_automatically() -> None:
    raw = _annotated_raw()
    raw.set_annotations(
        mne.Annotations(
            onset=[1.0, 2.0],
            duration=[0.0, 0.0],
            description=["target/left", "target/right"],
        )
    )
    with pytest.raises(MneAnalysisError, match="Available labels: 'target/left', 'target/right'"):
        create_epochs(
            raw,
            event_label="target",
            tmin_seconds=-0.2,
            tmax_seconds=0.8,
            baseline_enabled=False,
            baseline_start_seconds=-0.2,
            baseline_end_seconds=0.0,
            reject_eeg_peak_to_peak_uv=0,
            reject_by_annotation=True,
            cancel_check=_cancel_check,
        )


def test_zero_retained_epochs_explains_boundary_drop() -> None:
    raw = _annotated_raw()
    with pytest.raises(MneAnalysisError, match="Zero epochs were retained"):
        create_epochs(
            raw,
            event_label="target",
            tmin_seconds=10.0,
            tmax_seconds=11.0,
            baseline_enabled=False,
            baseline_start_seconds=10.0,
            baseline_end_seconds=11.0,
            reject_eeg_peak_to_peak_uv=0,
            reject_by_annotation=True,
            cancel_check=_cancel_check,
        )


def test_psd_rejects_frequencies_above_nyquist_without_resampling() -> None:
    epochs = create_epochs(
        _annotated_raw(),
        event_label="target",
        tmin_seconds=-0.2,
        tmax_seconds=0.8,
        baseline_enabled=False,
        baseline_start_seconds=-0.2,
        baseline_end_seconds=0.0,
        reject_eeg_peak_to_peak_uv=0,
        reject_by_annotation=True,
        cancel_check=_cancel_check,
    ).epochs
    with pytest.raises(MneAnalysisError, match="exceeds Nyquist"):
        psd_summary(
            epochs,
            fmin_hz=1,
            fmax_hz=60,
            n_fft=64,
            n_per_seg=64,
            n_overlap=32,
            window="hamming",
            cancel_check=_cancel_check,
        )
