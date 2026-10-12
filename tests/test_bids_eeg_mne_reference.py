"""Optional parity check against the pinned direct-MNE reference script."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from brainlearn_server.app import store
from brainlearn_server.bids_eeg import BidsEegDiscoveryService

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SCRIPT = REPOSITORY_ROOT / "scripts" / "reference_bids_eeg_mne.py"
FIXTURE_MANIFEST = REPOSITORY_ROOT / "docs/fixtures/ds002181-sub-1473-1.0.0.json"
REFERENCE_EXPECTED = REPOSITORY_ROOT / "docs/fixtures/ds002181-sub-1473-mne-reference-1.0.0.json"
FIXTURE_ROOT_ENV = "BRAINLEARN_BIDS_EEG_FIXTURE"


def _fixture_hashes(root: Path, entries: list[dict[str, Any]]) -> dict[str, str]:
    hashes = {}
    for entry in entries:
        path = root.joinpath(*PurePosixPath(entry["path"]).parts)
        hashes[entry["path"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


@pytest.mark.skipif(
    not os.environ.get(FIXTURE_ROOT_ENV),
    reason=(
        "Set BRAINLEARN_BIDS_EEG_FIXTURE to the verified pinned fixture for this integration check."
    ),
)
def test_direct_mne_reference_matches_brainlearn_without_reading_samples(tmp_path: Path) -> None:
    fixture_root = Path(os.environ[FIXTURE_ROOT_ENV]).expanduser().resolve(strict=True)
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["fixture"]["files"]
    before = _fixture_hashes(fixture_root, entries)
    completed = subprocess.run(
        [sys.executable, str(REFERENCE_SCRIPT), str(fixture_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    reference = json.loads(completed.stdout)
    expected = json.loads(REFERENCE_EXPECTED.read_text(encoding="utf-8"))
    reference_for_comparison = json.loads(json.dumps(reference))
    reference_for_comparison["tool_versions"]["python"] = expected["tool_versions"]["python"]
    assert reference_for_comparison == expected

    project = tmp_path / "project"
    project.mkdir()
    (project / "brainlearn.project.json").write_text("{}", encoding="utf-8")
    (project / "workflow.json").write_text("{}", encoding="utf-8")
    dataset_root = project / "raw-data/study"
    for entry in entries:
        relative = PurePosixPath(entry["path"])
        source = fixture_root.joinpath(*relative.parts)
        target = dataset_root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    stub = reference["method"]["external_eeglab_data_stub"]
    stub_path = dataset_root.joinpath(*PurePosixPath(stub["path"]).parts)
    stub_path.touch()
    store.allowed_roots.add(project.resolve())
    try:
        service = BidsEegDiscoveryService(store)
        discovery = service.discover(str(project), "raw-data/study")
        assert discovery.status == "ready"
        assert len(discovery.recordings) == 1
        recording = discovery.recordings[0]
        inspection = service.inspect_signal(str(project), "raw-data/study", recording.path)
    finally:
        store.allowed_roots.discard(project.resolve())

    assert reference["fixture"]["dataset_id"] == manifest["dataset_id"]
    assert reference["fixture"]["snapshot"] == manifest["snapshot"]
    assert reference["fixture"]["snapshot_tree_sha1"] == manifest["snapshot_tree_sha1"]
    assert reference["tool_versions"]["mne"] == "1.13.2"
    assert reference["tool_versions"]["mne_bids"] == "0.20.0"
    assert reference["method"]["preload"] is False
    assert reference["method"]["signal_samples_read"] is False

    assert recording.path == reference["recording_path"]
    assert list(recording.channel_names) == reference["channel_names"]
    assert recording.sampling_frequency_hz == reference["sampling_frequency_hz"]
    assert recording.eeg_channel_count == reference["channel_type_counts"]["eeg"]
    assert recording.event_count == reference["annotation_count"]
    assert list(recording.event_types) == sorted(
        {event["description"] for event in reference["annotations"]}
    )
    assert recording.events_timing_sha256 == reference["events_timing_sha256"]
    assert recording.task == reference["bids_sidecar_values"]["TaskName"]
    assert recording.eeg_reference == reference["bids_sidecar_values"]["EEGReference"]
    assert recording.sampling_frequency_hz == reference["bids_sidecar_values"]["SamplingFrequency"]

    assert inspection.recording_path == reference["recording_path"]
    assert inspection.sampling_frequency_hz == reference["sampling_frequency_hz"]
    assert inspection.sample_count == reference["sample_count"]
    assert inspection.duration_seconds == reference["duration_seconds"]
    assert inspection.channel_count == reference["channel_count"]
    assert inspection.channel_types == reference["channel_type_counts"]
    assert inspection.bad_channel_count == reference["bad_channel_count"]
    assert inspection.annotation_count == reference["annotation_count"]
    assert list(inspection.annotation_descriptions) == sorted(
        {event["description"] for event in reference["annotations"]}
    )
    assert inspection.highpass_hz == reference["highpass_hz"]
    assert inspection.lowpass_hz == reference["lowpass_hz"]

    assert _fixture_hashes(fixture_root, entries) == before
    staged_hashes = _fixture_hashes(dataset_root, entries)
    assert staged_hashes == before
    assert stub_path.stat().st_size == 0
