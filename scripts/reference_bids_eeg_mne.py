"""Emit an independent, metadata-only direct-MNE reference for the pinned EEG fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import stat
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

FIXTURE_MANIFEST = (
    Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "ds002181-sub-1473-1.0.0.json"
)
EEGLAB_DATA_FIELD = "data"


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            byte_size += len(chunk)
            digest.update(chunk)
    return byte_size, digest.hexdigest()


def _verified_files(dataset_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for entry in manifest["fixture"]["files"]:
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe fixture path in manifest: {entry['path']!r}")
        path = dataset_root.joinpath(*relative.parts)
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ValueError(f"Pinned fixture file is missing: {entry['path']}") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"Pinned fixture path is not a regular file: {entry['path']}")
        byte_size, sha256 = _sha256(path)
        if byte_size != entry["byte_size"] or sha256 != entry["sha256"]:
            raise ValueError(f"Pinned fixture size or SHA-256 mismatch: {entry['path']}")
        files.append({"path": entry["path"], "byte_size": byte_size, "sha256": sha256})
    return files


def _annotation_timing_sha256(annotations: list[dict[str, Any]]) -> str:
    """Digest event timing and labels at 1 ns precision in canonical event order."""

    events = [
        (
            round(float(item["onset_seconds"]), 9),
            round(float(item["duration_seconds"]), 9),
            str(item["description"]),
        )
        for item in annotations
    ]
    events.sort(key=lambda item: (item[0], item[1], item[2]))
    payload = json.dumps(
        {"contract": "brainlearn-bids-events-v1", "events": events},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stage_metadata_fixture(
    dataset_root: Path, staging_root: Path, manifest: dict[str, Any]
) -> str:
    for entry in manifest["fixture"]["files"]:
        relative = PurePosixPath(entry["path"])
        source = dataset_root.joinpath(*relative.parts)
        target = staging_root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    set_file = staging_root / "sub-1473/eeg/sub-1473_task-Baseline_eeg.set"
    from scipy.io import loadmat

    eeg = loadmat(set_file, variable_names=["EEG"], squeeze_me=True, struct_as_record=False)["EEG"]
    data_reference = getattr(eeg, EEGLAB_DATA_FIELD, None)
    if not isinstance(data_reference, str) or not data_reference.lower().endswith(".fdt"):
        raise ValueError("The pinned EEGLAB header does not name an external .fdt data file.")
    data_name = PurePosixPath(data_reference)
    if data_name.is_absolute() or len(data_name.parts) != 1 or data_name.name in {"", ".", ".."}:
        raise ValueError("The pinned EEGLAB data reference must be a single safe filename.")
    data_path = set_file.parent / data_name.name
    if data_path.exists():
        raise ValueError(
            "The temporary reference staging directory unexpectedly contains .fdt data."
        )
    # This pinned subset omits the source .fdt referenced by its .set header.
    # MNE needs the companion path to open header and event metadata, but this
    # reference never loads, reads, or compares signal samples.
    data_path.touch()
    return data_path.relative_to(staging_root).as_posix()


def build_reference(dataset_root: Path) -> dict[str, Any]:
    """Read the pinned fixture with direct MNE-BIDS calls and no signal samples."""

    import mne
    import mne_bids
    import numpy
    import scipy
    from mne_bids import find_matching_paths, read_raw_bids

    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    if (manifest["dataset_id"], manifest["snapshot"]) != ("ds002181", "1.0.0"):
        raise ValueError("The reference is scoped to OpenNeuro ds002181 snapshot 1.0.0.")
    root = dataset_root.expanduser().resolve(strict=True)
    input_files = _verified_files(root, manifest)

    with tempfile.TemporaryDirectory(prefix="brainlearn-mne-reference-") as temporary:
        staging_root = Path(temporary) / "dataset"
        staging_root.mkdir()
        metadata_stub = _stage_metadata_fixture(root, staging_root, manifest)
        matches = find_matching_paths(
            staging_root,
            subjects=["1473"],
            tasks=["Baseline"],
            suffixes=["eeg"],
            extensions=[".set"],
            datatypes=["eeg"],
            check=True,
        )
        if len(matches) != 1:
            raise ValueError(f"Expected one pinned EEG recording, found {len(matches)}.")
        recording_path = Path(matches[0].fpath).relative_to(staging_root).as_posix()
        raw = read_raw_bids(matches[0], verbose="ERROR")
        try:
            if raw.preload:
                raise RuntimeError("The direct-MNE reference must not preload signal samples.")
            channel_types = raw.get_channel_types()
            annotations = [
                {
                    "onset_seconds": float(onset),
                    "duration_seconds": float(duration),
                    "description": str(description),
                }
                for onset, duration, description in zip(
                    raw.annotations.onset,
                    raw.annotations.duration,
                    raw.annotations.description,
                    strict=True,
                )
            ]
            sidecar_path = staging_root / "sub-1473/eeg/sub-1473_task-Baseline_eeg.json"
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            result = {
                "schema_version": "1.0",
                "fixture": {
                    "provider": manifest["provider"],
                    "dataset_id": manifest["dataset_id"],
                    "snapshot": manifest["snapshot"],
                    "snapshot_tree_sha1": manifest["snapshot_tree_sha1"],
                    "input_files": input_files,
                },
                "tool_versions": {
                    "python": platform.python_version(),
                    "mne": mne.__version__,
                    "mne_bids": mne_bids.__version__,
                    "numpy": numpy.__version__,
                    "scipy": scipy.__version__,
                },
                "method": {
                    "selection": "mne_bids.find_matching_paths",
                    "reader": "mne_bids.read_raw_bids",
                    "preload": False,
                    "signal_samples_read": False,
                    "external_eeglab_data_stub": {
                        "path": metadata_stub,
                        "byte_size": 0,
                        "reason": (
                            "The pinned five-file subset omits the .fdt named by the .set header; "
                            "an empty companion exists only in a temporary copy so MNE can read "
                            "the header and BIDS event metadata."
                        ),
                    },
                },
                "recording_path": recording_path,
                "bids_sidecar_values": {
                    key: sidecar[key]
                    for key in (
                        "TaskName",
                        "SamplingFrequency",
                        "EEGReference",
                        "PowerLineFrequency",
                        "EEGChannelCount",
                        "MiscChannelCount",
                    )
                },
                "sampling_frequency_hz": float(raw.info["sfreq"]),
                "sample_count": int(raw.n_times),
                "duration_seconds": float(raw.n_times / raw.info["sfreq"]),
                "channel_count": len(raw.ch_names),
                "channel_names": list(raw.ch_names),
                "channel_type_counts": dict(sorted(Counter(channel_types).items())),
                "bad_channel_count": len(raw.info["bads"]),
                "annotation_count": len(annotations),
                "annotations": annotations,
                "events_timing_sha256": _annotation_timing_sha256(annotations),
                "highpass_hz": float(raw.info["highpass"]),
                "lowpass_hz": float(raw.info["lowpass"]),
            }
        finally:
            raw.close()

    if _verified_files(root, manifest) != input_files:
        raise RuntimeError("Pinned fixture files changed while generating the MNE reference.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path, help="verified ds002181:1.0.0 fixture root")
    args = parser.parse_args()
    print(json.dumps(build_reference(args.dataset_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
