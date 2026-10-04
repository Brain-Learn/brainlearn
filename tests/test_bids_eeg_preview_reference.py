"""Direct-library reference check for bounded BIDS EEG previews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from brainlearn_server.app import store
from brainlearn_server.bids_eeg import BidsEegDiscoveryService


def test_preview_values_match_independent_mne_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mne = pytest.importorskip("mne")
    mne_bids = pytest.importorskip("mne_bids")
    np = pytest.importorskip("numpy")
    project = tmp_path / "project"
    project.mkdir()
    (project / "brainlearn.project.json").write_text("{}", encoding="utf-8")
    (project / "workflow.json").write_text("{}", encoding="utf-8")
    root = project / "raw-data" / "study"
    eeg = root / "sub-01" / "eeg"
    eeg.mkdir(parents=True)
    (root / "dataset_description.json").write_text(
        json.dumps({"Name": "Preview reference", "BIDSVersion": "1.2.0"}),
        encoding="utf-8",
    )
    name = "sub-01_task-Rest_eeg.edf"
    source = eeg / name
    source.write_bytes(b"controlled synthetic source identity")
    sidecar = {
        "TaskName": "Rest",
        "SamplingFrequency": 256,
        "EEGReference": "Cz",
        "PowerLineFrequency": 50,
        "SoftwareFilters": "n/a",
        "EEGChannelCount": 2,
    }
    (eeg / "sub-01_task-Rest_eeg.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (eeg / "sub-01_task-Rest_channels.tsv").write_text(
        "name\ttype\tunits\nCz\tEEG\tuV\nPz\tEEG\tuV\n", encoding="utf-8"
    )
    (eeg / "sub-01_task-Rest_events.tsv").write_text(
        "onset\tduration\ttrial_type\n1\t0.25\tstimulus\n", encoding="utf-8"
    )
    store.allowed_roots.add(project.resolve())

    sfreq = 256.0
    times = np.arange(2048) / sfreq
    data = np.vstack([1e-5 * np.sin(2 * np.pi * 10 * times), 2e-5 * np.sin(2 * np.pi * 20 * times)])
    info = mne.create_info(["Cz", "Pz"], sfreq, ch_types=["eeg", "eeg"])
    annotations = mne.Annotations([1.0, 4.0], [0.25, 0.1], ["stimulus", "later"])

    def make_raw():
        raw = mne.io.RawArray(data.copy(), info.copy(), verbose="ERROR")
        raw.set_annotations(annotations.copy())
        return raw

    bids_path = SimpleNamespace(root=None)
    monkeypatch.setattr(mne_bids, "get_bids_path_from_fname", lambda *_args, **_kwargs: bids_path)
    monkeypatch.setattr(
        mne_bids,
        "read_raw_bids",
        lambda *_args, **_kwargs: make_raw(),
    )
    service = BidsEegDiscoveryService(store)
    preview = service.preview_signal(
        str(project),
        "raw-data/study",
        "sub-01/eeg/" + name,
        time_start_seconds=0.5,
        duration_seconds=3.0,
        channel_names=("Cz", "Pz"),
        max_buckets=64,
    )

    reference = make_raw()
    start = round(0.5 * sfreq)
    stop = start + round(3.0 * sfreq)
    reference_data = reference.get_data(
        picks=["Cz", "Pz"], start=start, stop=stop, units="uV", verbose="ERROR"
    )
    np.testing.assert_allclose(
        [bin_.minimum_uv for bin_ in preview.traces[0].bins],
        [reference_data[0, i * 12 : (i + 1) * 12].min() for i in range(64)],
        rtol=0,
        atol=1e-10,
    )
    reference_psd = reference.compute_psd(
        method="welch",
        fmin=0.0,
        fmax=100.0,
        tmin=start / sfreq,
        tmax=(stop - 1) / sfreq,
        picks=["Cz", "Pz"],
        reject_by_annotation=False,
        n_fft=1024,
        n_per_seg=768,
        n_overlap=384,
        window="hamming",
        average="mean",
        verbose="ERROR",
    )
    expected, frequencies = reference_psd.get_data(return_freqs=True)
    np.testing.assert_allclose(preview.spectrum.frequencies_hz, frequencies, rtol=0, atol=0)
    np.testing.assert_allclose(
        preview.spectrum.traces[0].power_uv2_per_hz,
        expected[0] * 1e12,
        rtol=1e-12,
        atol=1e-12,
    )
    assert preview.source_content_identity.startswith("brainlearn-v1:artifact:")
    assert [event.description for event in preview.events] == ["stimulus"]
    assert (
        hashlib.sha256(source.read_bytes()).hexdigest()
        == hashlib.sha256(b"controlled synthetic source identity").hexdigest()
    )
    assert not list(project.rglob("*.fif"))
    reference.close()
    store.allowed_roots.clear()
