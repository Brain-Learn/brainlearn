"""Versioned, allowlisted Python rendering for the first EEG filter workflow."""

from __future__ import annotations

import json
import math
from typing import Literal

from brainlearn_core import NodeInstance, Workflow
from pydantic import BaseModel, ConfigDict, Field

CODE_RENDERER_SCHEMA_VERSION: Literal["1.0"] = "1.0"
CODE_RENDERER_ID = "brainlearn.eeg.mne-filter.python"
CODE_RENDERER_VERSION = "1.2.1"
_EXPECTED_NODE_TYPES = {"input.bids_eeg", "eeg.inspect", "eeg.filter"}


class RenderedCode(BaseModel):
    """Result envelope shared by versioned workflow code renderers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = CODE_RENDERER_SCHEMA_VERSION
    renderer_id: str = CODE_RENDERER_ID
    renderer_version: str = CODE_RENDERER_VERSION
    language: Literal["python"] = "python"
    filename: str = "brainlearn_eeg_filter.py"
    dependencies: list[str]
    source: str = Field(min_length=1)


class CodeRenderError(ValueError):
    """The workflow is outside the renderer's deliberately narrow contract."""


def _parameters(node: NodeInstance) -> dict[str, object]:
    return {parameter.id: parameter.value for parameter in node.parameters}


def _python_string(value: str) -> str:
    """Encode user-controlled strings as valid, injection-safe Python literals."""

    return json.dumps(value, ensure_ascii=True)


def _finite_parameter(parameters: dict[str, object], name: str, default: float) -> float:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        raise CodeRenderError(f"Filter parameter {name!r} must be a finite number in Hz.")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise CodeRenderError(f"Filter parameter {name!r} must be a finite number in Hz.") from None
    if not math.isfinite(result):
        raise CodeRenderError(f"Filter parameter {name!r} must be finite.")
    return result


def _notch_parameters(
    parameters: dict[str, object], low_hz: float, high_hz: float
) -> tuple[list[float], float]:
    value = parameters.get("notch_hz", "")
    if not isinstance(value, str):
        raise CodeRenderError("Notch frequencies must be comma-separated numbers in Hz.")
    if len(value) > 128:
        raise CodeRenderError("Enter at most 16 notch frequencies.")
    pieces = [piece.strip() for piece in value.split(",")] if value.strip() else []
    if len(pieces) > 16 or any(not piece for piece in pieces):
        raise CodeRenderError("Enter up to 16 comma-separated notch frequencies in Hz.")
    try:
        frequencies = [float(piece) for piece in pieces]
    except ValueError:
        raise CodeRenderError("Notch frequencies must be finite numbers in Hz.") from None
    if any(not math.isfinite(frequency) for frequency in frequencies):
        raise CodeRenderError("Notch frequencies must be finite numbers in Hz.")
    if frequencies != sorted(set(frequencies)):
        raise CodeRenderError("Notch frequencies must be unique and listed in ascending order.")
    width_hz = _finite_parameter(parameters, "notch_width_hz", 1.0)
    if not 0.1 <= width_hz <= 10.0:
        raise CodeRenderError("Notch width must be between 0.1 and 10 Hz.")
    if any(not low_hz < frequency < high_hz for frequency in frequencies):
        raise CodeRenderError("Each notch center must be strictly inside the configured pass band.")
    return frequencies, width_hz


def _assert_supported_topology(
    workflow: Workflow,
) -> tuple[NodeInstance, NodeInstance, NodeInstance]:
    by_type = {node.type: node for node in workflow.nodes}
    if len(workflow.nodes) != 3 or set(by_type) != _EXPECTED_NODE_TYPES:
        raise CodeRenderError(
            "Python export currently supports one BIDS EEG input connected to Inspect Signal "
            "and Band-pass Filter. Remove unimplemented or additional nodes before exporting."
        )
    bids = by_type["input.bids_eeg"]
    inspect = by_type["eeg.inspect"]
    filter_node = by_type["eeg.filter"]
    expected = {
        (bids.id, "dataset", inspect.id, "dataset"),
        (inspect.id, "raw", filter_node.id, "raw"),
    }
    actual = {
        (edge.source.node_id, edge.source.port_id, edge.target.node_id, edge.target.port_id)
        for edge in workflow.edges
    }
    if actual != expected or len(workflow.edges) != 2:
        raise CodeRenderError(
            "Python export requires the direct BIDS EEG → Inspect Signal → Band-pass Filter path."
        )
    return bids, inspect, filter_node


def render_eeg_filter_python(workflow: Workflow) -> RenderedCode:
    """Render only the implemented BIDS import, metadata inspection, and MNE filtering path."""

    bids, inspect, filter_node = _assert_supported_topology(workflow)
    bids_parameters = _parameters(bids)
    inspect_parameters = _parameters(inspect)
    filter_parameters = _parameters(filter_node)
    dataset_root = bids_parameters.get("root", "synthetic/example-bids")
    recording_path = inspect_parameters.get("recording_path", "")
    if not isinstance(dataset_root, str) or not dataset_root.strip():
        raise CodeRenderError("Dataset root must be a non-empty project-relative path.")
    if not isinstance(recording_path, str):
        raise CodeRenderError("Recording path must be a string relative to the BIDS dataset root.")
    low_hz = _finite_parameter(filter_parameters, "low_hz", 1.0)
    high_hz = _finite_parameter(filter_parameters, "high_hz", 40.0)
    if not 0.1 <= low_hz <= 100.0:
        raise CodeRenderError("Low cutoff must be between 0.1 and 100 Hz.")
    if not 0.5 <= high_hz <= 200.0:
        raise CodeRenderError("High cutoff must be between 0.5 and 200 Hz.")
    if low_hz >= high_hz:
        raise CodeRenderError("Low cutoff must be lower than the high cutoff.")
    notch_frequencies, notch_width_hz = _notch_parameters(filter_parameters, low_hz, high_hz)

    recording_default = _python_string(recording_path) if recording_path else "None"
    source = f'''#!/usr/bin/env python3
"""Generated by BrainLearn's versioned MNE filter renderer 1.2.1.

This script imports and inspects one BIDS EEG recording, applies the configured
MNE FIR band-pass filter and optional notch filter to EEG channels, and writes
a FIF output plus provenance. Notch frequencies are explicit researcher inputs.
It does not perform ICA, epoching, ERP, PSD analysis, or scientific QC approval.

References:
  MNE-Python: https://doi.org/10.3389/fnins.2013.00267
  MNE-BIDS: https://doi.org/10.21105/joss.01896
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import mne
import mne_bids
from mne_bids import get_bids_path_from_fname, read_raw_bids


LOW_HZ = {low_hz!r}
HIGH_HZ = {high_hz!r}
NOTCH_HZ = {notch_frequencies!r}
NOTCH_WIDTH_HZ = {notch_width_hz!r}
EXPECTED_MNE = "1.13.2"
EXPECTED_MNE_BIDS = "0.20.0"
MAX_INPUT_BYTES = 10 * 1024**3
MAX_DIRECTORY_ENTRIES = 100_000
INHERITED_SIDECARS = (
    ("eeg", ".json"),
    ("channels", ".tsv"),
    ("events", ".tsv"),
    ("events", ".json"),
    ("electrodes", ".tsv"),
    ("coordsystem", ".json"),
)


def parse_entities(stem: str, suffix: str) -> dict[str, str] | None:
    marker = f"_{{suffix}}"
    if not stem.endswith(marker):
        return None
    prefix = stem[:-len(marker)]
    if not prefix:
        return {{}}
    entities: dict[str, str] = {{}}
    for token in prefix.split("_"):
        key, separator, value = token.partition("-")
        if not separator or not key or not value or key in entities:
            return None
        entities[key] = value
    return entities


def input_files(recording: Path, dataset_root: Path) -> list[Path]:
    """List bounded source files MNE-BIDS can consume for this recording."""
    try:
        recording.relative_to(dataset_root)
    except ValueError as exc:
        raise SystemExit("Recording path must stay inside the selected BIDS dataset.") from exc
    entities = parse_entities(recording.stem, "eeg")
    if entities is None or "sub" not in entities:
        raise SystemExit("Recording filename must use valid BIDS EEG entities.")
    candidates = [recording]
    candidates.extend(
        path
        for path in (
            recording.with_suffix(".vmrk"),
            recording.with_suffix(".eeg"),
            recording.with_suffix(".fdt"),
        )
        if path.exists() or path.is_symlink()
    )
    description = dataset_root / "dataset_description.json"
    if not description.is_file():
        raise SystemExit("BIDS dataset_description.json is missing or unreadable.")
    candidates.append(description)

    participants = dataset_root / "participants.tsv"
    if participants.exists() or participants.is_symlink():
        candidates.append(participants)
    subject = entities["sub"]
    session = entities.get("ses")
    scans_directory = dataset_root / f"sub-{{subject}}"
    scans_prefix = f"sub-{{subject}}_"
    if session is not None:
        scans_directory /= f"ses-{{session}}"
        scans_prefix += f"ses-{{session}}_"
    scans = scans_directory / f"{{scans_prefix}}scans.tsv"
    if scans.exists() or scans.is_symlink():
        candidates.append(scans)

    directory = recording.parent
    entries_seen = 0
    while True:
        if directory.is_symlink() or not directory.is_dir():
            raise SystemExit("A BIDS source directory is unsafe or unreadable.")
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    entries_seen += 1
                    if entries_seen > MAX_DIRECTORY_ENTRIES:
                        raise SystemExit(
                            "The BIDS source has too many directory entries to identify safely."
                        )
                    name = entry.name
                    for suffix, extension in INHERITED_SIDECARS:
                        ending = f"_{{suffix}}{{extension}}"
                        if not name.endswith(ending):
                            continue
                        sidecar_entities = parse_entities(
                            name[:-len(extension)], suffix
                        )
                        if sidecar_entities is None:
                            continue
                        if all(
                            entities.get(key) == value
                            for key, value in sidecar_entities.items()
                        ):
                            candidates.append(Path(entry.path))
                        break
        except OSError as exc:
            raise SystemExit("A BIDS metadata directory could not be read safely.") from exc
        if directory == dataset_root:
            break
        directory = directory.parent
        if dataset_root not in directory.parents and directory != dataset_root:
            raise SystemExit("Recording path must stay inside the selected BIDS dataset.")

    unique = sorted(set(candidates))
    for path in unique:
        try:
            path.relative_to(dataset_root)
        except ValueError as exc:
            raise SystemExit("BIDS source paths must stay inside the selected dataset.") from exc
    return unique


def snapshot(paths: list[Path], dataset_root: Path) -> dict[str, str]:
    result: dict[str, str] = {{}}
    total_bytes = 0
    for path in paths:
        try:
            before = path.lstat()
        except OSError as exc:
            raise SystemExit(f"BIDS source file is missing or unreadable: {{path}}") from exc
        if path.is_symlink() or not path.is_file() or before.st_nlink > 1:
            raise SystemExit(f"BIDS source file is not a safe regular file: {{path}}")
        total_bytes += before.st_size
        if total_bytes > MAX_INPUT_BYTES:
            raise SystemExit("BIDS source files exceed the 10 GiB identity limit.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            after = path.lstat()
        except OSError as exc:
            raise SystemExit("A BIDS source file changed while its identity was created.") from exc
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SystemExit("A BIDS source file changed while its identity was created.")
        result[path.relative_to(dataset_root).as_posix()] = digest.hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and filter one BIDS EEG recording.")
    parser.add_argument(
        "--dataset-root",
        default={_python_string(dataset_root)},
        help="Path to the BIDS dataset root; defaults to the exported workflow setting.",
    )
    parser.add_argument(
        "--recording-path",
        default={recording_default},
        required={"True" if not recording_path else "False"},
        help="Recording path relative to the dataset root.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output FIF path outside the source BIDS dataset (default: beside the dataset).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output FIF and provenance file.",
    )
    args = parser.parse_args()
    if mne.__version__ != EXPECTED_MNE or mne_bids.__version__ != EXPECTED_MNE_BIDS:
        raise SystemExit(
            f"Install the pinned dependencies: mne=={{EXPECTED_MNE}} and "
            f"mne-bids=={{EXPECTED_MNE_BIDS}} (found {{mne.__version__}}, "
            f"{{mne_bids.__version__}})."
        )

    dataset_root = Path(args.dataset_root).expanduser().resolve(strict=True)
    recording_arg = Path(args.recording_path).expanduser()
    recording_candidate = (
        recording_arg if recording_arg.is_absolute() else dataset_root / recording_arg
    )
    try:
        recording_relative = recording_candidate.relative_to(dataset_root)
    except ValueError as exc:
        raise SystemExit("Recording path must stay inside the selected BIDS dataset.") from exc
    if ".." in recording_relative.parts:
        raise SystemExit("Recording path must stay inside the selected BIDS dataset.")
    current_path = dataset_root
    for part in recording_relative.parts:
        current_path /= part
        if current_path.is_symlink():
            raise SystemExit("Recording path cannot contain symlinks.")
    recording = recording_candidate.resolve(strict=True)
    try:
        recording.relative_to(dataset_root)
    except ValueError as exc:
        raise SystemExit("Recording path must stay inside the selected BIDS dataset.") from exc
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else dataset_root.parent / "brainlearn_filtered_raw.fif"
    )
    try:
        output_path.relative_to(dataset_root)
    except ValueError:
        pass
    else:
        raise SystemExit("Choose an output path outside the source BIDS dataset.")
    if output_path.suffix.lower() != ".fif":
        raise SystemExit("Output path must end in .fif.")
    provenance_path = output_path.with_suffix(".provenance.json")
    if not args.overwrite and (output_path.exists() or provenance_path.exists()):
        raise SystemExit("Output already exists. Choose another path or pass --overwrite.")

    paths = input_files(recording, dataset_root)
    before = snapshot(paths, dataset_root)
    bids_path = get_bids_path_from_fname(recording, check=False)
    bids_path.root = dataset_root
    raw = read_raw_bids(bids_path, verbose="ERROR", extra_params={{"preload": False}})
    sampling_frequency = float(raw.info["sfreq"])
    nyquist_hz = sampling_frequency / 2
    if not LOW_HZ < HIGH_HZ < nyquist_hz:
        raw.close()
        raise SystemExit(
            f"Require 0 < low cutoff < high cutoff < Nyquist ({{nyquist_hz:g}} Hz)."
        )
    if any(not LOW_HZ < frequency < min(HIGH_HZ, nyquist_hz) for frequency in NOTCH_HZ):
        raw.close()
        raise SystemExit("Each notch center must be inside the pass band and below Nyquist.")
    eeg_names = [
        name
        for name, kind in zip(raw.ch_names, raw.get_channel_types(), strict=True)
        if kind == "eeg"
    ]
    if not eeg_names:
        raw.close()
        raise SystemExit("The selected recording has no EEG channels to filter.")
    if len(raw.ch_names) > 256 or raw.n_times * len(raw.ch_names) > 25_000_000:
        raw.close()
        raise SystemExit(
            "Recording exceeds the filter limits of 256 channels and 25 million samples."
        )

    raw.load_data(verbose="ERROR")

    print(json.dumps({{
        "recording": recording.relative_to(dataset_root).as_posix(),
        "sampling_frequency_hz": sampling_frequency,
        "sample_count": int(raw.n_times),
        "duration_seconds": float(raw.n_times / sampling_frequency),
        "channel_types": dict(zip(sorted(set(raw.get_channel_types())),
                                  [raw.get_channel_types().count(kind)
                                   for kind in sorted(set(raw.get_channel_types()))], strict=True)),
        "eeg_channels": eeg_names,
    }}, indent=2))

    filtered = raw.copy()
    raw.close()
    filtered.filter(
        l_freq=LOW_HZ,
        h_freq=HIGH_HZ,
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
    if NOTCH_HZ:
        filtered.notch_filter(
            freqs=NOTCH_HZ,
            picks=eeg_names,
            filter_length="auto",
            notch_widths=NOTCH_WIDTH_HZ,
            trans_bandwidth=1.0,
            method="fir",
            phase="zero",
            fir_window="hamming",
            fir_design="firwin",
            skip_by_annotation=("edge", "bad_acq_skip"),
            pad="reflect_limited",
            verbose="ERROR",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_handle, temporary_fif_name = tempfile.mkstemp(
        prefix=".brainlearn-filter-", suffix="_raw.fif", dir=output_path.parent
    )
    os.close(temporary_handle)
    Path(temporary_fif_name).unlink()
    temp_path = Path(temporary_fif_name)
    temporary_provenance_path: Path | None = None
    backup_paths: dict[Path, Path] = {{}}
    published_paths: list[Path] = []
    published_links: list[tuple[Path, Path]] = []
    outputs_committed = False
    try:
        filtered.save(str(temp_path), fmt="single", overwrite=False, verbose="ERROR")
        after = snapshot(input_files(recording, dataset_root), dataset_root)
        if before != after:
            raise SystemExit("Source files changed during filtering; output was discarded.")
        provenance = {{
        "schema_version": "1.0",
        "renderer": "{CODE_RENDERER_ID}@{CODE_RENDERER_VERSION}",
        "source_file_sha256": before,
        "parameters": {{
            "low_hz": LOW_HZ, "high_hz": HIGH_HZ,
            "notch_frequencies_hz": NOTCH_HZ, "notch_width_hz": NOTCH_WIDTH_HZ,
            "unit": "Hz",
        }},
        "settings": {{
            "method": "fir", "phase": "zero", "fir_design": "firwin",
            "fir_window": "hamming", "filter_length": "auto",
            "l_trans_bandwidth": "auto", "h_trans_bandwidth": "auto",
            "pad": "reflect_limited", "skip_by_annotation": ["edge", "bad_acq_skip"],
            "picks": "all EEG channels; non-EEG channels unchanged",
            "notch_operation": (
                "MNE Raw.notch_filter after band-pass; skipped when no centers are selected"
            ),
            "notch_method": "fir", "notch_phase": "zero",
            "notch_width_unit": "Hz", "notch_trans_bandwidth_hz": 1.0,
        }},
        "sampling_frequency_hz": sampling_frequency,
        "software": {{"mne": mne.__version__, "mne-bids": mne_bids.__version__}},
        "citations": [
            "https://doi.org/10.3389/fnins.2013.00267",
            "https://doi.org/10.21105/joss.01896",
        ],
        "output_path": str(output_path),
        }}
        handle, temporary_name = tempfile.mkstemp(
            prefix=".brainlearn-provenance-", dir=provenance_path.parent
        )
        temporary_provenance_path = Path(temporary_name)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(provenance, stream, indent=2, sort_keys=True)
            stream.write("\\n")
        assert temporary_provenance_path is not None
        staged_outputs = (
            (output_path, temp_path),
            (provenance_path, temporary_provenance_path),
        )
        if args.overwrite:
            for final_path, _ in staged_outputs:
                if not final_path.exists():
                    continue
                backup_handle, backup_name = tempfile.mkstemp(
                    prefix=f".{{final_path.name}}-", suffix=".backup", dir=final_path.parent
                )
                os.close(backup_handle)
                Path(backup_name).unlink()
                backup_path = Path(backup_name)
                os.replace(final_path, backup_path)
                backup_paths[final_path] = backup_path
            for final_path, temporary_path in staged_outputs:
                published_paths.append(final_path)
                os.replace(temporary_path, final_path)
        else:
            # Hard-link publication is an atomic create-if-absent operation on
            # the same filesystem. A late file must never be silently replaced.
            for final_path, temporary_path in staged_outputs:
                try:
                    os.link(temporary_path, final_path)
                except FileExistsError:
                    raise SystemExit(
                        "An output appeared during filtering; choose another path or "
                        "pass --overwrite."
                    ) from None
                published_links.append((final_path, temporary_path))
        outputs_committed = True
    except BaseException:
        for published_path in published_paths:
            published_path.unlink(missing_ok=True)
        for final_path, temporary_path in published_links:
            try:
                if os.path.samefile(final_path, temporary_path):
                    final_path.unlink()
            except OSError:
                pass
        for final_path, backup_path in backup_paths.items():
            if backup_path.exists():
                os.replace(backup_path, final_path)
        raise
    finally:
        temp_path.unlink(missing_ok=True)
        if temporary_provenance_path is not None:
            temporary_provenance_path.unlink(missing_ok=True)
        if outputs_committed:
            for backup_path in backup_paths.values():
                backup_path.unlink(missing_ok=True)
        filtered.close()
    print(f"Filtered EEG written to {{output_path}}")
    print(f"Provenance written to {{provenance_path}}")


if __name__ == "__main__":
    main()
'''
    return RenderedCode(
        dependencies=["mne==1.13.2", "mne-bids==0.20.0"],
        source=source,
    )
