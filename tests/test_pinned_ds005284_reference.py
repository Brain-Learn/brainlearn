"""Pinned real-data parity against separately written direct MNE operations.

Set ``BRAINLEARN_DS005284_ROOT`` to an extracted OpenNeuro ds005284:1.0.0
sample containing sub-001 through sub-005 to enable this integration test.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from importlib import import_module
from pathlib import Path

import pytest
from brainlearn_mne.analysis import create_epochs, erp_summary, psd_summary

mne = pytest.importorskip("mne")
mne_bids = pytest.importorskip("mne_bids")
np = import_module("numpy")

REFERENCE_ROOT = os.environ.get("BRAINLEARN_DS005284_ROOT")
pytestmark = pytest.mark.skipif(
    not REFERENCE_ROOT,
    reason="Set BRAINLEARN_DS005284_ROOT to the pinned ds005284:1.0.0 sample.",
)

EXPECTED_BDF = {
    "001": (65_511_936, "a3dcbdc69c5cd2b47c19cd2fcbe27ddbca0baa3deca6881b37e8e02fed970cda"),
    "002": (65_711_616, "a8f700a4b3c50420d9c357e698653deb73419f6a39ec9ddc52fafdf2c03f4b25"),
    "003": (65_911_296, "4c2fb27bc6e73d3e0aca19bdba1f85b905bd8ec48335f10bdf6d07e83d5fd47f"),
    "004": (65_112_576, "e0e5984ece35f742585106c63642dbdf96c833ddaa9a574193855cafb3d7b50c"),
    "005": (69_106_176, "0d4ab10e6a1fef3723f9749ef3c42a17964cb56ff9717c371bda22b1fc53e3d8"),
}
EVENT_LABEL = "condition 54"
TMIN_SECONDS = -0.2
TMAX_SECONDS = 0.8
BASELINE_SECONDS = (-0.2, 0.0)
REJECT_EEG_PEAK_TO_PEAK_UV = 0.0
REJECT_BY_ANNOTATION = True
PSD_SETTINGS = {
    "fmin_hz": 1.0,
    "fmax_hz": 40.0,
    "n_fft": 256,
    "n_per_seg": 256,
    "n_overlap": 128,
    "window": "hamming",
}
EPOCH_ATOL_VOLTS = 1e-12
SUMMARY_ATOL = 1e-12


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _no_cancel() -> None:
    return None


@pytest.mark.parametrize("subject", tuple(EXPECTED_BDF))
def test_pinned_subject_epoch_erp_psd_match_direct_mne(subject: str) -> None:
    """Compare production analysis operations with direct MNE on the pinned raw files."""

    root = Path(REFERENCE_ROOT or "")
    recording = root / f"sub-{subject}" / "eeg" / f"sub-{subject}_task-26ByBiosemi_eeg.bdf"
    expected_size, expected_sha = EXPECTED_BDF[subject]
    assert recording.stat().st_size == expected_size
    assert _sha256(recording) == expected_sha

    bids_path = mne_bids.BIDSPath(
        root=root,
        subject=subject,
        task="26ByBiosemi",
        suffix="eeg",
        extension=".bdf",
    )
    raw = mne_bids.read_raw_bids(bids_path, verbose="ERROR", extra_params={"preload": False})
    try:
        assert raw.info["sfreq"] == 1024.0
        assert sum(channel_type == "eeg" for channel_type in raw.get_channel_types()) == 64
        assert Counter(raw.annotations.description)[EVENT_LABEL] == 16

        production = create_epochs(
            raw,
            event_label=EVENT_LABEL,
            tmin_seconds=TMIN_SECONDS,
            tmax_seconds=TMAX_SECONDS,
            baseline_enabled=True,
            baseline_start_seconds=BASELINE_SECONDS[0],
            baseline_end_seconds=BASELINE_SECONDS[1],
            reject_eeg_peak_to_peak_uv=REJECT_EEG_PEAK_TO_PEAK_UV,
            reject_by_annotation=REJECT_BY_ANNOTATION,
            cancel_check=_no_cancel,
        )

        # This reference is written in direct MNE calls and does not call the
        # BrainLearn helper that is under comparison.
        events, event_id = mne.events_from_annotations(
            raw, event_id={EVENT_LABEL: 1}, regexp=None, verbose="ERROR"
        )
        direct_epochs = mne.Epochs(
            raw,
            events,
            event_id={EVENT_LABEL: event_id[EVENT_LABEL]},
            tmin=TMIN_SECONDS,
            tmax=TMAX_SECONDS,
            baseline=BASELINE_SECONDS,
            picks="eeg",
            preload=True,
            reject=None,
            reject_by_annotation=REJECT_BY_ANNOTATION,
            event_repeated="error",
            on_missing="raise",
            verbose="ERROR",
        )
        np.testing.assert_allclose(
            production.epochs.get_data(),
            direct_epochs.get_data(),
            rtol=0,
            atol=EPOCH_ATOL_VOLTS,
        )
        assert production.diagnostics["selected_event_count"] == len(events) == 16
        assert production.diagnostics["retained_epoch_count"] == len(direct_epochs) == 16

        production_erp = erp_summary(production.epochs, cancel_check=_no_cancel)
        direct_uv = direct_epochs.get_data(units="uV", verbose="ERROR")
        np.testing.assert_allclose(
            production_erp["mean_uv"], direct_uv.mean(axis=0), rtol=1e-7, atol=SUMMARY_ATOL
        )
        np.testing.assert_allclose(
            production_erp["within_participant_epoch_sd_uv"],
            direct_uv.std(axis=0, ddof=1),
            rtol=1e-7,
            atol=SUMMARY_ATOL,
        )

        production_psd = psd_summary(production.epochs, **PSD_SETTINGS, cancel_check=_no_cancel)
        direct_spectrum = direct_epochs.compute_psd(
            method="welch",
            fmin=PSD_SETTINGS["fmin_hz"],
            fmax=PSD_SETTINGS["fmax_hz"],
            picks="eeg",
            n_fft=PSD_SETTINGS["n_fft"],
            n_per_seg=PSD_SETTINGS["n_per_seg"],
            n_overlap=PSD_SETTINGS["n_overlap"],
            window=PSD_SETTINGS["window"],
            average="mean",
            remove_dc=True,
            verbose="ERROR",
        )
        direct_power = direct_spectrum.get_data() * 1e12
        np.testing.assert_allclose(
            production_psd["frequencies_hz"], direct_spectrum.freqs, rtol=0, atol=0
        )
        np.testing.assert_allclose(
            production_psd["mean_power_uv2_per_hz"],
            direct_power.mean(axis=0),
            rtol=1e-7,
            atol=SUMMARY_ATOL,
        )
        np.testing.assert_allclose(
            production_psd["within_participant_epoch_sd_uv2_per_hz"],
            direct_power.std(axis=0, ddof=1),
            rtol=1e-7,
            atol=SUMMARY_ATOL,
        )
    finally:
        raw.close()

    # The operations above read only the public source files.
    assert _sha256(recording) == expected_sha
