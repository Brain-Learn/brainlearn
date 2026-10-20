"""Run a direct-MNE reference on the pinned public EEGEyeNet BIDS recording."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

FIXTURE_MANIFEST = (
    Path(__file__).resolve().parents[1] / "docs" / "fixtures" / "ds007338-sub-EP10-1.0.0.json"
)


def _prepare_output_path(path: Path, *, create_parents: bool = False) -> Path:
    """Reject symlinked parents and optionally create a new real directory tree."""
    output = Path(os.path.abspath(path.expanduser()))
    parts = output.parent.parts
    current = Path(parts[0])
    for part in parts[1:]:
        candidate = current / part
        if candidate.is_symlink():
            system_tmp = Path("/private/tmp")
            if (
                candidate == Path("/tmp")
                and candidate.resolve(strict=True) == system_tmp
                and system_tmp.is_dir()
                and not system_tmp.is_symlink()
            ):
                current = system_tmp
                continue
            raise ValueError(f"Refusing output under symlinked directory: {candidate}")
        if candidate.exists():
            if not candidate.is_dir():
                raise ValueError(f"Output parent is not a directory: {candidate}")
        elif create_parents:
            candidate.mkdir()
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError(f"Output parent could not be created safely: {candidate}")
        current = candidate

    output = current / output.name
    if output.is_symlink() or output.exists():
        raise FileExistsError(f"Refusing to replace existing reference output: {output}")
    return output


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


def build_reference(dataset_root: Path, output_path: Path) -> dict[str, Any]:
    """Filter and fit ICA with direct MNE, then save an explicit keep-all result."""
    output = _prepare_output_path(output_path)
    import mne
    import mne_bids
    import numpy
    import scipy
    from mne_bids import find_matching_paths, read_raw_bids
    from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]

    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    if (manifest["dataset_id"], manifest["snapshot"]) != ("ds007338", "1.0.0"):
        raise ValueError("The direct reference is scoped to OpenNeuro ds007338 snapshot 1.0.0.")
    root = dataset_root.expanduser().resolve(strict=True)
    source_files = _verified_files(root, manifest)
    recording_path = manifest["fixture"]["recording_path"]
    matches = find_matching_paths(
        root,
        subjects=[manifest["fixture"]["subject"]],
        sessions=[manifest["fixture"]["session"]],
        tasks=[manifest["fixture"]["task"]],
        runs=[manifest["fixture"]["run"]],
        suffixes=["eeg"],
        extensions=[".edf"],
        datatypes=["eeg"],
        check=True,
    )
    if len(matches) != 1 or Path(matches[0].fpath).relative_to(root).as_posix() != recording_path:
        raise ValueError("The pinned BIDS query did not select exactly the manifested recording.")

    raw = read_raw_bids(matches[0], verbose="ERROR")
    try:
        raw.load_data(verbose="ERROR")
        channel_names = [
            name
            for name, kind in zip(raw.ch_names, raw.get_channel_types(), strict=True)
            if kind == "eeg"
        ]
        if len(channel_names) != 129:
            raise ValueError(
                f"Expected 129 EEG channels in the pinned recording, got {len(channel_names)}."
            )
        raw.filter(
            l_freq=1.0,
            h_freq=40.0,
            picks=channel_names,
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

        output = _prepare_output_path(output, create_parents=True)

        with tempfile.TemporaryDirectory(prefix="brainlearn-public-reference-") as temporary:
            temporary_root = Path(temporary)
            filtered_path = temporary_root / "filtered-raw.fif"
            raw.save(str(filtered_path), fmt="single", overwrite=False, verbose="ERROR")
            fit_raw = mne.io.read_raw_fif(filtered_path, preload=True, verbose="ERROR")
            try:
                ica = mne.preprocessing.ICA(
                    n_components=None,
                    method="fastica",
                    random_state=0,
                    max_iter=1000,
                    fit_params=None,
                    allow_ref_meg=False,
                    verbose="ERROR",
                )
                with mne.utils.use_log_level("ERROR"):
                    import warnings

                    with warnings.catch_warnings(record=True) as captured:
                        warnings.simplefilter("always", ConvergenceWarning)
                        ica.fit(
                            fit_raw.copy(),
                            picks="eeg",
                            reject_by_annotation=False,
                            decim=1,
                            verbose="ERROR",
                        )
                if any(issubclass(item.category, ConvergenceWarning) for item in captured):
                    raise RuntimeError(
                        "Direct-MNE FastICA did not converge on the pinned recording."
                    )
                if type(ica.n_iter_) is not int or not 1 <= ica.n_iter_ < 1000:
                    raise RuntimeError("Direct-MNE did not report a converged iteration count.")
                # The fixture's explicit engineering decision keeps every component.
                ica.apply(fit_raw, exclude=[], verbose="ERROR")
                staged_output = temporary_root / "reference-reviewed-raw.fif"
                fit_raw.save(str(staged_output), fmt="single", overwrite=False, verbose="ERROR")
            finally:
                fit_raw.close()

            os.link(staged_output, output)

        if _verified_files(root, manifest) != source_files:
            output.unlink(missing_ok=True)
            raise RuntimeError("Pinned public fixture files changed during direct-MNE processing.")

        return {
            "schema_version": "1.0",
            "fixture": {
                "provider": manifest["provider"],
                "dataset_id": manifest["dataset_id"],
                "snapshot": manifest["snapshot"],
                "input_files": source_files,
            },
            "output_path": str(output),
            "method": {
                "reader": "mne_bids.read_raw_bids",
                "filter": {
                    "low_hz": 1.0,
                    "high_hz": 40.0,
                    "phase": "zero",
                    "design": "firwin",
                    "window": "hamming",
                    "skip_by_annotation": ["edge", "bad_acq_skip"],
                },
                "ica": {
                    "method": "fastica",
                    "random_state": 0,
                    "max_iter": 1000,
                    "converged": True,
                    "iterations": ica.n_iter_,
                    "components": int(ica.n_components_),
                    "excluded_components": [],
                },
                "sampling_frequency_hz": float(raw.info["sfreq"]),
                "sample_count": int(raw.n_times),
                "duration_seconds": float(raw.n_times / raw.info["sfreq"]),
                "channel_names": channel_names,
                "annotation_count": len(raw.annotations),
                "montage_channel_count": len(raw.get_montage().ch_names)
                if raw.get_montage() is not None
                else 0,
            },
            "tool_versions": {
                "python": __import__("platform").python_version(),
                "mne": mne.__version__,
                "mne_bids": mne_bids.__version__,
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
            },
        }
    finally:
        raw.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path, help="verified fixture root")
    parser.add_argument("output", type=Path, help="new FIF output path")
    args = parser.parse_args()
    result = build_reference(args.dataset_root, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
