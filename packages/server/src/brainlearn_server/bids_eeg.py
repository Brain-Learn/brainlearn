"""Read-only BIDS EEG recording discovery and essential metadata checks.

The scanner reads only BIDS JSON/TSV metadata. It never opens EEG signal bytes,
computes content identities, or changes dataset files. It intentionally reports
essential metadata readiness rather than claiming full BIDS-validator coverage.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, cast

from brainlearn_core import (
    PROJECT_MANIFEST_FILENAME,
    WORKFLOW_FILENAME,
    content_identity,
    validate_relative_path,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from brainlearn_server.project_store import ProjectStore, canonicalize_project_path

MAX_DISCOVERY_ENTRIES = 50_000
MAX_METADATA_ENTITY_KEYS = 12
MAX_METADATA_QUERY_MULTIPLIER = 256
MAX_JSON_BYTES = 1_048_576
MAX_TSV_BYTES = 16_777_216
MAX_TSV_ROWS = 200_000
MAX_CHANNEL_NAMES = 512
MAX_EVENT_TYPES = 1_000
MAX_INPUT_IDENTITY_BYTES = 10 * 1024 * 1024 * 1024
INPUT_IDENTITY_CHUNK_BYTES = 1024 * 1024
_BIDS_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_SUPPORTED_EEG_EXTENSIONS = {
    ".edf": "edf",
    ".bdf": "bdf",
    ".vhdr": "brainvision",
    ".set": "eeglab",
}
_EEG_SUFFIX = "_eeg"
_SUPPORTED_EEG_FORMATS_MESSAGE = (
    "Supported raw EEG formats are EDF (.edf), BDF (.bdf), BrainVision (.vhdr with matching "
    ".vmrk and .eeg companions), and EEGLAB (.set with an optional .fdt companion)."
)


class BidsEegIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None


class BidsEegRecording(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    subject: str
    session: str | None = None
    task: str
    format: str | None = None
    status: Literal["ready", "incomplete_metadata", "unsupported"]
    sampling_frequency_hz: float | None = None
    eeg_reference: str | None = None
    eeg_channel_count: int | None = Field(default=None, ge=0)
    channel_names: tuple[str, ...] = ()
    event_count: int | None = Field(default=None, ge=0)
    event_types: tuple[str, ...] = ()
    events_timing_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    issues: tuple[BidsEegIssue, ...] = ()


class BidsEegDiscovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    dataset_path: str
    status: Literal["ready", "incomplete_metadata", "unsupported", "not_bids", "no_recordings"]
    dataset_name: str | None = None
    bids_version: str | None = None
    recordings: tuple[BidsEegRecording, ...] = ()
    issues: tuple[BidsEegIssue, ...] = ()
    inspection_scope: Literal["metadata_only"] = "metadata_only"


def bids_eeg_discovery_error(discovery: BidsEegDiscovery) -> str:
    """Describe dataset issues and the next action before a workflow can proceed."""

    issues = [(issue.path, issue.message) for issue in discovery.issues]
    issues.extend(
        (recording.path, issue.message)
        for recording in discovery.recordings
        for issue in recording.issues
    )
    details = [f"{path}: {message}" if path else message for path, message in issues[:8]]
    if len(issues) > 8:
        details.append(f"and {len(issues) - 8} more issue(s)")
    if discovery.status == "unsupported":
        prefix = (
            "This BIDS EEG dataset contains an unsupported BIDS version/type or EEG format. "
            f"{_SUPPORTED_EEG_FORMATS_MESSAGE}"
        )
    elif discovery.status == "not_bids":
        prefix = "This folder is not a ready BIDS EEG dataset. Add and correct its BIDS metadata."
    elif discovery.status == "no_recordings":
        prefix = (
            "No supported raw BIDS EEG recordings were found. Add a recording under sub-*/eeg/."
        )
    else:
        prefix = (
            "BIDS EEG metadata is incomplete. Correct the listed files and fields, then scan again."
        )
    next_step = " ".join(details) if details else "Review the dataset metadata and scan again."
    return f"{prefix} {next_step}"


def _recording_readiness_error(
    discovery: BidsEegDiscovery, recording: BidsEegRecording
) -> str | None:
    if recording.status == "unsupported":
        reasons = " ".join(issue.message for issue in recording.issues)
        return (
            f"Recording {recording.path} uses an unsupported EEG format. "
            f"{_SUPPORTED_EEG_FORMATS_MESSAGE} {reasons}"
        )
    if recording.status == "incomplete_metadata":
        reasons = " ".join(issue.message for issue in recording.issues)
        return (
            f"Recording {recording.path} has incomplete required BIDS metadata. "
            f"Correct the applicable EEG sidecar/table fields and scan again. {reasons}"
        )
    if discovery.status != "ready":
        return bids_eeg_discovery_error(discovery)
    return None


class BidsEegIdentityFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BidsEegInputIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    recording_path: str
    status: Literal["ready", "resource_limit"]
    content_identity: str | None = Field(
        default=None, pattern=r"^brainlearn-v1:artifact:[0-9a-f]{64}$"
    )
    files: tuple[BidsEegIdentityFile, ...] = ()
    message: str | None = None
    inspection_scope: Literal["bounded_source_hashes"] = "bounded_source_hashes"

    @model_validator(mode="after")
    def validate_identity_state(self) -> BidsEegInputIdentity:
        paths = [item.path for item in self.files]
        for path in paths:
            validate_relative_path(path, "Identity file path")
        if paths != sorted(set(paths)):
            raise ValueError("Input identity files must be unique and sorted by relative path.")
        if self.status == "ready":
            if self.content_identity is None or not self.files or self.message is not None:
                raise ValueError("A ready input identity requires hashes and no error message.")
        elif self.content_identity is not None or self.files or not self.message:
            raise ValueError("A resource-limited input identity must contain no hashes.")
        return self


class BidsEegSignalInspection(BaseModel):
    """Measured, non-persisted properties returned by MNE for one recording."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    recording_path: str
    format: Literal["edf", "bdf", "brainvision", "eeglab"]
    sampling_frequency_hz: float = Field(gt=0)
    sample_count: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    channel_count: int = Field(gt=0)
    channel_types: dict[str, int]
    bad_channel_count: int = Field(ge=0)
    annotation_count: int = Field(ge=0)
    annotation_descriptions: tuple[str, ...] = ()
    highpass_hz: float | None = Field(default=None, ge=0)
    lowpass_hz: float | None = Field(default=None, gt=0)
    inspection_scope: Literal["read_only_signal_metadata"] = "read_only_signal_metadata"


class BidsEegPreviewChannel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=MAX_CHANNEL_NAMES)
    channel_type: str = Field(min_length=1, max_length=32)
    marked_bad: bool


class BidsEegTraceBin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time_seconds: float = Field(allow_inf_nan=False)
    minimum_uv: float = Field(allow_inf_nan=False)
    maximum_uv: float = Field(allow_inf_nan=False)


class BidsEegTracePreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel_name: str = Field(min_length=1, max_length=MAX_CHANNEL_NAMES)
    bins: tuple[BidsEegTraceBin, ...] = Field(min_length=1, max_length=1_000)
    unit: Literal["µV"] = "µV"


class BidsEegEventPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    onset_seconds: float = Field(allow_inf_nan=False)
    duration_seconds: float = Field(allow_inf_nan=False, ge=0)
    description: str = Field(max_length=256)


class BidsEegSpectrumTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    channel_name: str = Field(min_length=1, max_length=MAX_CHANNEL_NAMES)
    power_uv2_per_hz: tuple[float, ...] = Field(min_length=2, max_length=513)


class BidsEegSpectrumPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["welch"] = "welch"
    window: Literal["hamming"] = "hamming"
    n_fft: int = Field(gt=0, le=1_024)
    n_per_seg: int = Field(gt=0, le=1_024)
    n_overlap: int = Field(ge=0, le=512)
    reject_by_annotation: Literal[False] = False
    frequencies_hz: tuple[float, ...] = Field(min_length=2, max_length=513)
    traces: tuple[BidsEegSpectrumTrace, ...] = Field(min_length=1, max_length=8)
    unit: Literal["µV²/Hz"] = "µV²/Hz"

    @model_validator(mode="after")
    def validate_spectrum(self) -> BidsEegSpectrumPreview:
        if any(
            len(trace.power_uv2_per_hz) != len(self.frequencies_hz)
            or any(power < 0 for power in trace.power_uv2_per_hz)
            for trace in self.traces
        ):
            raise ValueError("Spectrum traces must align with non-negative frequency bins.")
        if any(
            right <= left
            for left, right in zip(self.frequencies_hz, self.frequencies_hz[1:], strict=False)
        ):
            raise ValueError("Spectrum frequencies must be strictly increasing.")
        if len({trace.channel_name for trace in self.traces}) != len(self.traces):
            raise ValueError("Spectrum channel names must be unique.")
        return self


class BidsEegSignalPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    recording_path: str
    source_content_identity: str = Field(pattern=r"^brainlearn-v1:artifact:[0-9a-f]{64}$")
    time_start_seconds: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    sampling_frequency_hz: float = Field(gt=0, allow_inf_nan=False)
    sample_count: int = Field(gt=0)
    channels: tuple[BidsEegPreviewChannel, ...] = Field(min_length=1, max_length=MAX_CHANNEL_NAMES)
    traces: tuple[BidsEegTracePreview, ...] = Field(min_length=1, max_length=8)
    events: tuple[BidsEegEventPreview, ...] = Field(max_length=500)
    event_count: int = Field(ge=0)
    events_truncated: bool
    spectrum: BidsEegSpectrumPreview
    preview_scope: Literal["bounded_read_only_signal_preview"] = "bounded_read_only_signal_preview"

    @model_validator(mode="after")
    def validate_preview_contents(self) -> BidsEegSignalPreview:
        channel_by_name = {channel.name: channel for channel in self.channels}
        trace_names = [trace.channel_name for trace in self.traces]
        if len(channel_by_name) != len(self.channels) or len(set(trace_names)) != len(trace_names):
            raise ValueError("Preview channel names must be unique.")
        if any(
            name not in channel_by_name or channel_by_name[name].channel_type != "eeg"
            for name in trace_names
        ):
            raise ValueError("Preview traces must reference selected EEG channels.")
        if set(trace_names) != {trace.channel_name for trace in self.spectrum.traces}:
            raise ValueError("Waveform and spectrum channels must match.")
        if len(self.events) > self.event_count or self.events_truncated != (
            self.event_count > len(self.events)
        ):
            raise ValueError("Preview event count and truncation state must agree.")
        if any(
            any(item.minimum_uv > item.maximum_uv for item in trace.bins)
            or any(
                right.time_seconds < left.time_seconds
                for left, right in zip(trace.bins, trace.bins[1:], strict=False)
            )
            for trace in self.traces
        ):
            raise ValueError("Trace bins must have ordered finite bounds and times.")
        return self


MAX_PREVIEW_CHANNELS = 8
DEFAULT_PREVIEW_CHANNELS = 4
DEFAULT_PREVIEW_BUCKETS = 400
MAX_PREVIEW_BUCKETS = 1_000
MAX_PREVIEW_DURATION_SECONDS = 20.0
MAX_PREVIEW_INPUT_SAMPLES = 2_000_000
MAX_PREVIEW_EVENTS = 500
PREVIEW_SPECTRUM_N_FFT = 1_024


class BidsSignalInspectionError(ValueError):
    """A recording could not be inspected safely through the pinned MNE stack."""


class BidsDatasetPathError(ValueError):
    """The selected project-relative dataset directory cannot be scanned safely."""


class BidsMetadataError(ValueError):
    """A candidate metadata file cannot be read or parsed safely."""


class BidsIdentityResourceLimit(ValueError):
    """Input identity work exceeded its explicit file or byte budget."""


@dataclass
class _DirectoryEntryBudget:
    limit: int = field(default_factory=lambda: MAX_DISCOVERY_ENTRIES)
    entries_seen: int = 0
    cache: dict[Path, tuple[Path, ...]] = field(default_factory=dict)
    metadata_indexes: dict[
        tuple[Path, str, str], dict[tuple[tuple[str, str], ...], tuple[Path, ...]]
    ] = field(default_factory=dict)
    metadata_queries: int = 0

    def list(self, directory: Path, *, metadata: bool = False) -> tuple[Path, ...]:
        if directory in self.cache:
            return self.cache[directory]
        entries: list[Path] = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if self.entries_seen >= self.limit:
                        message = "The dataset has too many directory entries to scan safely."
                        if metadata:
                            raise BidsMetadataError(message)
                        raise BidsDatasetPathError(message)
                    entries.append(Path(entry.path))
                    self.entries_seen += 1
        except OSError as exc:
            if metadata:
                raise BidsMetadataError("Dataset directory could not be read safely.") from exc
            raise BidsDatasetPathError("The selected dataset directory cannot be read.") from exc
        ordered = tuple(sorted(entries, key=lambda item: item.name))
        self.cache[directory] = ordered
        return ordered

    def matching_metadata(
        self,
        directory: Path,
        entities: dict[str, str],
        suffix: str,
        extension: str,
    ) -> tuple[Path, ...]:
        cache_key = (directory, suffix, extension)
        index = self.metadata_indexes.get(cache_key)
        if index is None:
            mutable_index: dict[tuple[tuple[str, str], ...], list[Path]] = {}
            for item in self.list(directory, metadata=True):
                if not item.name.endswith(f"_{suffix}{extension}"):
                    continue
                if item.is_symlink():
                    raise BidsMetadataError("A matching metadata file is a symlink.")
                candidate_entities = _parse_entities(item.name[: -len(extension)], f"_{suffix}")
                if candidate_entities is None:
                    continue
                entity_key = tuple(sorted(candidate_entities.items()))
                mutable_index.setdefault(entity_key, []).append(item)
            index = {entity_key: tuple(paths) for entity_key, paths in mutable_index.items()}
            self.metadata_indexes[cache_key] = index

        entity_items = tuple(sorted(entities.items()))
        if len(entity_items) > MAX_METADATA_ENTITY_KEYS:
            raise BidsMetadataError("Recording filename has too many BIDS entities to scan safely.")
        query_count = 1 << len(entity_items)
        self.metadata_queries += query_count
        if self.metadata_queries > self.limit * MAX_METADATA_QUERY_MULTIPLIER:
            raise BidsMetadataError("Dataset exceeds the metadata matching scan limit.")
        matches: list[Path] = []
        for mask in range(query_count):
            entity_key = tuple(
                entity_items[index] for index in range(len(entity_items)) if mask & (1 << index)
            )
            matches.extend(index.get(entity_key, ()))
        return tuple(matches)


def _read_metadata(path: Path, limit: int) -> bytes:
    """Read one bounded regular file without following a leaf symlink."""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BidsMetadataError("Metadata file is missing or blocked.") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise BidsMetadataError("Metadata file is not regular or exceeds the scan limit.")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise BidsMetadataError("Metadata file exceeds the scan limit.")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        identity_path = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )
        if identity_before != identity_after or identity_before != identity_path:
            raise BidsMetadataError("Metadata file changed during discovery.")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_entities(stem: str, suffix: str) -> dict[str, str] | None:
    if not stem.endswith(suffix):
        return None
    prefix = stem[: -len(suffix)]
    if prefix.endswith("_"):
        prefix = prefix[:-1]
    entities: dict[str, str] = {}
    if not prefix:
        return entities
    for token in prefix.split("_"):
        key, separator, value = token.partition("-")
        if not separator or not key or not value or key in entities:
            return None
        entities[key] = value
    return entities


def _metadata_candidates(
    root: Path,
    recording_dir: Path,
    entities: dict[str, str],
    suffix: str,
    extension: str,
    budget: _DirectoryEntryBudget,
) -> list[Path]:
    candidates: list[tuple[int, int, str, Path]] = []
    current = recording_dir
    while True:
        for item in budget.matching_metadata(current, entities, suffix, extension):
            candidate_entities = _parse_entities(item.name[: -len(extension)], f"_{suffix}")
            assert candidate_entities is not None
            relative = item.relative_to(root).as_posix()
            depth = len(item.parent.relative_to(root).parts)
            candidates.append((depth, len(candidate_entities), relative, item))
        if current == root:
            break
        current = current.parent
        if current != root and root not in current.parents:
            break
    # BIDS inheritance is applied from broad to specific: deeper folders and
    # more explicitly matched entities override metadata from parent sidecars.
    candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return [entry[3] for entry in candidates]


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(_read_metadata(path, MAX_JSON_BYTES).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BidsMetadataError("Metadata JSON is malformed or not UTF-8.") from exc
    if not isinstance(payload, dict):
        raise BidsMetadataError("Metadata JSON must contain an object.")
    return payload


def _read_tsv(path: Path, *, required: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        text = _read_metadata(path, MAX_TSV_BYTES).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BidsMetadataError("Metadata TSV is not UTF-8.") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    fields = reader.fieldnames or []
    missing = sorted(set(required) - set(fields))
    seen_fields: set[str] = set()
    duplicate_fields: set[str] = set()
    for column_name in fields:
        if column_name in seen_fields:
            duplicate_fields.add(column_name)
        seen_fields.add(column_name)
    duplicates = sorted(duplicate_fields)
    if missing or duplicates:
        problems = []
        if missing:
            problems.append(f"missing required column(s): {', '.join(missing)}")
        if duplicates:
            problems.append(f"duplicate column name(s): {', '.join(duplicates)}")
        raise BidsMetadataError(f"Metadata TSV has {'; '.join(problems)}.")
    rows: list[dict[str, str]] = []
    try:
        for row in reader:
            if len(rows) >= MAX_TSV_ROWS:
                raise BidsMetadataError("Metadata TSV exceeds the row limit.")
            if None in row:
                raise BidsMetadataError("Metadata TSV has rows with extra fields.")
            rows.append({key: value or "" for key, value in row.items() if key is not None})
    except csv.Error as exc:
        raise BidsMetadataError("Metadata TSV is malformed.") from exc
    return rows


def _events_timing_sha256(rows: list[dict[str, str]], type_field: str) -> str:
    """Hash normalized BIDS event timing and descriptions without returning all rows."""

    def rounded_time(value: str) -> float | None:
        if value.casefold() == "n/a":
            return None
        rounded = round(float(value), 9)
        return 0.0 if rounded == 0.0 else rounded

    events: list[tuple[float | None, float | None, str]] = []
    for row in rows:
        onset_text = row["onset"].strip()
        duration_text = row["duration"].strip()
        onset = rounded_time(onset_text)
        duration = rounded_time(duration_text)
        description = row.get(type_field, "").strip()
        if description.casefold() == "n/a":
            description = ""
        events.append((onset, duration, description))
    events.sort(
        key=lambda item: (item[0] is None, item[0] or 0.0, item[1] is None, item[1] or 0.0, item[2])
    )
    payload = json.dumps(
        {"contract": "brainlearn-bids-events-v1", "events": events},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _find_recording_dirs(
    root: Path, budget: _DirectoryEntryBudget
) -> list[tuple[str, str | None, Path]]:
    found: list[tuple[str, str | None, Path]] = []
    subjects = budget.list(root)
    for subject_dir in subjects:
        if not subject_dir.name.startswith("sub-"):
            continue
        if subject_dir.is_symlink():
            raise BidsDatasetPathError("A BIDS subject directory is a symlink.")
        if not subject_dir.is_dir():
            continue
        subject = subject_dir.name.removeprefix("sub-")
        if not subject or "_" in subject:
            continue
        children = budget.list(subject_dir)
        eeg_dir = subject_dir / "eeg"
        if eeg_dir.exists() or eeg_dir.is_symlink():
            if eeg_dir.is_symlink():
                raise BidsDatasetPathError("A BIDS EEG directory is a symlink.")
            if eeg_dir.is_dir():
                found.append((subject, None, eeg_dir))
        for session_dir in children:
            if not session_dir.name.startswith("ses-"):
                continue
            if session_dir.is_symlink():
                raise BidsDatasetPathError("A BIDS session directory is a symlink.")
            if not session_dir.is_dir():
                continue
            session = session_dir.name.removeprefix("ses-")
            if not session or "_" in session:
                continue
            session_eeg_dir = session_dir / "eeg"
            if session_eeg_dir.exists() or session_eeg_dir.is_symlink():
                if session_eeg_dir.is_symlink():
                    raise BidsDatasetPathError("A BIDS EEG directory is a symlink.")
                if session_eeg_dir.is_dir():
                    found.append((subject, session, session_eeg_dir))
    return found


def _recording_candidates(
    root: Path,
    directory: Path,
    subject: str,
    session: str | None,
    budget: _DirectoryEntryBudget,
) -> list[tuple[Path, dict[str, str], str | None, list[BidsEegIssue]]]:
    result: list[tuple[Path, dict[str, str], str | None, list[BidsEegIssue]]] = []
    entries = budget.list(directory)
    for entry in entries:
        if entry.is_symlink():
            if entry.stem.endswith(_EEG_SUFFIX) and entry.suffix.lower() not in {
                ".json",
                ".tsv",
                ".eeg",
                ".vmrk",
                ".fdt",
            }:
                result.append(
                    (
                        entry,
                        {"sub": subject, **({"ses": session} if session else {})},
                        None,
                        [
                            BidsEegIssue(
                                code="unsafe_recording", message="EEG source file is a symlink."
                            )
                        ],
                    )
                )
            continue
        if (
            not entry.is_file()
            or not entry.stem.endswith(_EEG_SUFFIX)
            or entry.suffix.lower() in {".json", ".tsv", ".eeg", ".vmrk", ".fdt"}
        ):
            continue
        stem = entry.stem
        entities = _parse_entities(stem, _EEG_SUFFIX)
        if entities is None or entities.get("sub") != subject:
            continue
        if entities.get("ses") != session:
            continue
        task = entities.get("task")
        issues: list[BidsEegIssue] = []
        if not task:
            issues.append(
                BidsEegIssue(
                    code="missing_task_entity",
                    message=(
                        "EEG recording filenames must include a task entity; rename the file "
                        "with a `task-<label>_eeg` suffix."
                    ),
                    path=entry.relative_to(root).as_posix(),
                )
            )
        extension = entry.suffix
        file_format = _SUPPORTED_EEG_EXTENSIONS.get(extension)
        if not file_format:
            issues.append(
                BidsEegIssue(
                    code="unsupported_format",
                    message=(
                        f"This EEG file extension ({entry.suffix}) is unsupported. "
                        f"{_SUPPORTED_EEG_FORMATS_MESSAGE} Convert or export the recording to "
                        "one of these formats, then scan again."
                    ),
                    path=entry.relative_to(root).as_posix(),
                )
            )
        elif file_format == "brainvision":
            for companion_suffix in (".vmrk", ".eeg"):
                companion = entry.with_suffix(companion_suffix)
                if companion.is_symlink() or not companion.is_file():
                    issues.append(
                        BidsEegIssue(
                            code="incomplete_brainvision_set",
                            message=(
                                "BrainVision recordings require matching .vhdr, .vmrk, and .eeg "
                                "files; add the missing companion(s)."
                            ),
                            path=entry.relative_to(root).as_posix(),
                        )
                    )
                    break
        result.append((entry, entities, file_format, issues))
        if len(result) > MAX_DISCOVERY_ENTRIES:
            raise BidsDatasetPathError("The selected dataset has too many EEG recordings to scan.")
    return result


def discover_bids_eeg(root: Path, dataset_path: str) -> BidsEegDiscovery:
    """Discover raw BIDS EEG files under one already-authorized dataset root."""

    issues: list[BidsEegIssue] = []
    description_path = root / "dataset_description.json"
    description: dict[str, object] | None = None
    if description_path.is_symlink():
        issues.append(
            BidsEegIssue(
                code="unsafe_dataset_description",
                message=(
                    "dataset_description.json must be a regular file, not a symlink. Replace "
                    "the symlink with a project-local BIDS description."
                ),
                path="dataset_description.json",
            )
        )
    elif not description_path.is_file():
        issues.append(
            BidsEegIssue(
                code="missing_dataset_description",
                message=(
                    "BIDS dataset_description.json is missing. Add it with a non-empty Name "
                    "and numeric BIDSVersion."
                ),
                path="dataset_description.json",
            )
        )
    else:
        try:
            description = _read_json_object(description_path)
        except BidsMetadataError as exc:
            issues.append(
                BidsEegIssue(
                    code="invalid_dataset_description",
                    message=str(exc),
                    path="dataset_description.json",
                )
            )

    dataset_name: str | None = None
    bids_version: str | None = None
    if description is not None:
        raw_name = description.get("Name")
        dataset_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
        if dataset_name is None:
            issues.append(
                BidsEegIssue(
                    code="missing_dataset_name",
                    message=(
                        "dataset_description.json must provide a non-empty Name. Add the dataset "
                        "name to that file."
                    ),
                    path="dataset_description.json",
                )
            )
        raw_version = description.get("BIDSVersion")
        bids_version = raw_version.strip() if isinstance(raw_version, str) else None
        if not bids_version or _BIDS_VERSION.fullmatch(bids_version) is None:
            issues.append(
                BidsEegIssue(
                    code="invalid_bids_version",
                    message=(
                        "dataset_description.json must provide a numeric BIDSVersion such as "
                        "1.2.0. Add or correct that field."
                    ),
                    path="dataset_description.json",
                )
            )
        else:
            version_match = _BIDS_VERSION.fullmatch(bids_version)
            assert version_match is not None
            if int(version_match.group(1)) != 1:
                issues.append(
                    BidsEegIssue(
                        code="unsupported_bids_version",
                        message=(
                            "This EEG metadata scanner supports BIDS major version 1 only. "
                            "Use a raw BIDS 1.x dataset or convert it explicitly."
                        ),
                        path="dataset_description.json",
                    )
                )
        dataset_type = description.get("DatasetType", "raw")
        if dataset_type != "raw":
            issues.append(
                BidsEegIssue(
                    code="unsupported_dataset_type",
                    message=(
                        "BIDS derivatives are not supported as raw EEG inputs. Select the raw "
                        "dataset or create a new raw import."
                    ),
                    path="dataset_description.json",
                )
            )

    budget = _DirectoryEntryBudget()
    try:
        eeg_dirs = _find_recording_dirs(root, budget)
    except BidsDatasetPathError as exc:
        issues.append(BidsEegIssue(code="unsafe_dataset_tree", message=str(exc)))
        eeg_dirs = []

    recordings: list[BidsEegRecording] = []
    for subject, session, directory in eeg_dirs:
        try:
            candidates = _recording_candidates(root, directory, subject, session, budget)
        except BidsDatasetPathError as exc:
            issues.append(BidsEegIssue(code="unsafe_dataset_tree", message=str(exc)))
            break
        for raw_file, entities, file_format, file_issues in candidates:
            relative = raw_file.relative_to(root).as_posix()
            task = entities.get("task", "")
            if not task:
                task = "unspecified"
            if file_format is None:
                recordings.append(
                    BidsEegRecording(
                        path=relative,
                        subject=subject,
                        session=session,
                        task=task,
                        format=None,
                        status="unsupported",
                        issues=tuple(file_issues),
                    )
                )
                continue

            metadata: dict[str, object] = {}
            recording_issues = list(file_issues)
            sidecar_parse_failed = False
            sidecar_path: Path | None = None
            try:
                sidecars = _metadata_candidates(root, directory, entities, "eeg", ".json", budget)
                if not sidecars:
                    recording_issues.append(
                        BidsEegIssue(
                            code="missing_eeg_sidecar",
                            message=(
                                "No matching or inherited *_eeg.json sidecar was found. Add an "
                                "applicable sidecar with TaskName, SamplingFrequency, "
                                "EEGReference, PowerLineFrequency, and SoftwareFilters."
                            ),
                            path=relative,
                        )
                    )
                for sidecar in sidecars:
                    sidecar_path = sidecar
                    metadata.update(_read_json_object(sidecar))
            except BidsMetadataError as exc:
                sidecar_parse_failed = True
                metadata = {}
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_eeg_sidecar",
                        message=str(exc),
                        path=(
                            sidecar_path.relative_to(root).as_posix()
                            if sidecar_path is not None
                            else relative
                        ),
                    )
                )

            sampling_frequency = _finite_number(metadata.get("SamplingFrequency"))
            if not sidecar_parse_failed and (sampling_frequency is None or sampling_frequency <= 0):
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_sampling_frequency",
                        message=(
                            "The applicable *_eeg.json sidecar must provide a positive numeric "
                            "SamplingFrequency. Add or correct that field."
                        ),
                        path=relative,
                    )
                )
                sampling_frequency = None
            eeg_reference_raw = metadata.get("EEGReference")
            eeg_reference = (
                eeg_reference_raw.strip()
                if isinstance(eeg_reference_raw, str) and eeg_reference_raw.strip()
                else None
            )
            if not sidecar_parse_failed and eeg_reference is None:
                recording_issues.append(
                    BidsEegIssue(
                        code="missing_eeg_reference",
                        message=(
                            "The applicable *_eeg.json sidecar must provide a non-empty "
                            "EEGReference. Add or correct that field."
                        ),
                        path=relative,
                    )
                )
            task_name = metadata.get("TaskName")
            if not sidecar_parse_failed and (
                not isinstance(task_name, str) or not task_name.strip()
            ):
                recording_issues.append(
                    BidsEegIssue(
                        code="missing_task_name",
                        message=(
                            "The applicable *_eeg.json sidecar must provide a non-empty TaskName. "
                            "Add or correct that field."
                        ),
                        path=relative,
                    )
                )
            power_line_frequency = metadata.get("PowerLineFrequency")
            if not sidecar_parse_failed and power_line_frequency != "n/a":
                numeric_power_line = _finite_number(power_line_frequency)
                if numeric_power_line is None or numeric_power_line <= 0:
                    recording_issues.append(
                        BidsEegIssue(
                            code="invalid_power_line_frequency",
                            message=(
                                "The applicable *_eeg.json sidecar must provide a positive "
                                "PowerLineFrequency or 'n/a'. Add or correct that field."
                            ),
                            path=relative,
                        )
                    )
            software_filters = metadata.get("SoftwareFilters")
            if (
                not sidecar_parse_failed
                and software_filters != "n/a"
                and (
                    not isinstance(software_filters, dict)
                    or any(
                        not isinstance(name, str) or not isinstance(parameters, dict)
                        for name, parameters in software_filters.items()
                    )
                )
            ):
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_software_filters",
                        message=(
                            "The applicable *_eeg.json sidecar must provide SoftwareFilters as an "
                            "object or 'n/a'. Add or correct that field."
                        ),
                        path=relative,
                    )
                )
            declared_channel_count = metadata.get("EEGChannelCount")
            channel_count: int | None = None
            if declared_channel_count is not None:
                if (
                    isinstance(declared_channel_count, bool)
                    or not isinstance(declared_channel_count, int)
                    or declared_channel_count < 0
                ):
                    recording_issues.append(
                        BidsEegIssue(
                            code="invalid_eeg_channel_count",
                            message="EEGChannelCount must be a non-negative integer when present.",
                            path=relative,
                        )
                    )
                else:
                    channel_count = declared_channel_count

            channel_names: tuple[str, ...] = ()
            channels_file: Path | None = None
            try:
                channels_files = _metadata_candidates(
                    root, directory, entities, "channels", ".tsv", budget
                )
                if channels_files:
                    channels_file = channels_files[-1]
                    channel_rows = _read_tsv(channels_file, required=("name", "type", "units"))
                    names = [row["name"].strip() for row in channel_rows]
                    if any(not name for name in names) or len(names) != len(set(names)):
                        raise BidsMetadataError("Channel names must be non-empty and unique.")
                    if not isinstance(declared_channel_count, int):
                        eeg_rows = sum(
                            row["type"].strip().casefold() == "eeg" for row in channel_rows
                        )
                        channel_count = eeg_rows
                    channel_names = tuple(names[:MAX_CHANNEL_NAMES])
            except BidsMetadataError as exc:
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_channels_tsv",
                        message=str(exc),
                        path=(
                            channels_file.relative_to(root).as_posix()
                            if channels_file is not None
                            else relative
                        ),
                    )
                )

            event_count: int | None = None
            event_types: tuple[str, ...] = ()
            events_timing_sha256: str | None = None
            events_file: Path | None = None
            try:
                events_files = _metadata_candidates(
                    root, directory, entities, "events", ".tsv", budget
                )
                if events_files:
                    events_file = events_files[-1]
                    event_rows = _read_tsv(events_file, required=("onset", "duration"))
                    for row in event_rows:
                        onset_text = row["onset"].strip()
                        duration_text = row["duration"].strip()
                        try:
                            onset = None if onset_text.casefold() == "n/a" else float(onset_text)
                            duration = (
                                None if duration_text.casefold() == "n/a" else float(duration_text)
                            )
                        except ValueError as exc:
                            raise BidsMetadataError(
                                "Event onset and duration values must be numeric or 'n/a'."
                            ) from exc
                        if (onset is not None and not math.isfinite(onset)) or (
                            duration is not None and (not math.isfinite(duration) or duration < 0)
                        ):
                            raise BidsMetadataError(
                                "Event timing values must be finite and duration non-negative."
                            )
                    event_count = len(event_rows)
                    type_field = (
                        "trial_type" if event_rows and "trial_type" in event_rows[0] else "value"
                    )
                    event_types = tuple(
                        sorted(
                            {
                                row[type_field].strip()
                                for row in event_rows
                                if row.get(type_field, "").strip()
                                and row[type_field].strip().casefold() != "n/a"
                            }
                        )[:MAX_EVENT_TYPES]
                    )
                    events_timing_sha256 = _events_timing_sha256(event_rows, type_field)
            except BidsMetadataError as exc:
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_events_tsv",
                        message=str(exc),
                        path=(
                            events_file.relative_to(root).as_posix()
                            if events_file is not None
                            else relative
                        ),
                    )
                )

            recording_status: Literal["ready", "incomplete_metadata", "unsupported"] = (
                "incomplete_metadata" if recording_issues else "ready"
            )
            recordings.append(
                BidsEegRecording(
                    path=relative,
                    subject=subject,
                    session=session,
                    task=task,
                    format=file_format,
                    status=recording_status,
                    sampling_frequency_hz=sampling_frequency,
                    eeg_reference=eeg_reference,
                    eeg_channel_count=channel_count,
                    channel_names=channel_names,
                    event_count=event_count,
                    event_types=event_types,
                    events_timing_sha256=events_timing_sha256,
                    issues=tuple(recording_issues),
                )
            )

    has_description = description is not None
    if not has_description and not recordings:
        status: Literal[
            "ready", "incomplete_metadata", "unsupported", "not_bids", "no_recordings"
        ] = "not_bids"
    elif any(record.status == "unsupported" for record in recordings) or any(
        issue.code.startswith("unsupported_") for issue in issues
    ):
        status = "unsupported"
    elif not recordings:
        status = "no_recordings" if not issues else "incomplete_metadata"
    elif issues or any(record.status != "ready" for record in recordings):
        status = "incomplete_metadata"
    else:
        status = "ready"
    return BidsEegDiscovery(
        dataset_path=dataset_path,
        status=status,
        dataset_name=dataset_name,
        bids_version=bids_version,
        recordings=tuple(recordings),
        issues=tuple(issues),
    )


@dataclass
class BidsEegDiscoveryService:
    """Resolve a selected dataset under an explicitly opened project."""

    projects: ProjectStore

    def _resolve_dataset(
        self, raw_project_path: str, relative_dataset_path: str
    ) -> tuple[Path, str]:
        try:
            project = self.projects.require_allowed(canonicalize_project_path(raw_project_path))
            relative = validate_relative_path(relative_dataset_path, "Dataset path")
        except (PermissionError, ValueError) as exc:
            raise BidsDatasetPathError(
                "Open an authorized project and choose a project-relative dataset path."
            ) from exc
        if (
            not (project / PROJECT_MANIFEST_FILENAME).is_file()
            or not (project / WORKFLOW_FILENAME).is_file()
        ):
            raise BidsDatasetPathError("The selected path is not an open BrainLearn project.")
        root = project
        for part in relative.split("/"):
            root = root / part
            if root.is_symlink():
                raise BidsDatasetPathError("The selected dataset path contains a symlink.")
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise BidsDatasetPathError("The selected dataset directory was not found.") from exc
        if resolved != project and project not in resolved.parents:
            raise BidsDatasetPathError(
                "The selected dataset directory is outside the open project."
            )
        if not resolved.is_dir():
            raise BidsDatasetPathError("The selected dataset path is not a directory.")
        return resolved, relative

    def discover(self, raw_project_path: str, relative_dataset_path: str) -> BidsEegDiscovery:
        """Read-only discovery of raw EEG recordings and essential metadata."""

        root, relative = self._resolve_dataset(raw_project_path, relative_dataset_path)
        return discover_bids_eeg(root, relative)

    def _resolve_signal_recording(
        self,
        raw_project_path: str,
        relative_dataset_path: str,
        recording_path: str,
    ) -> tuple[Path, str, BidsEegRecording]:
        root, relative = self._resolve_dataset(raw_project_path, relative_dataset_path)
        try:
            recording_relative = validate_relative_path(recording_path, "Recording path")
        except ValueError as exc:
            raise BidsDatasetPathError(
                "Choose a discovered recording inside this dataset."
            ) from exc
        discovery = discover_bids_eeg(root, relative)
        recording = next(
            (item for item in discovery.recordings if item.path == recording_relative), None
        )
        if recording is None:
            raise BidsDatasetPathError(
                "The selected path is not a discovered recording. Scan the dataset and choose "
                "a listed recording."
            )
        readiness_error = _recording_readiness_error(discovery, recording)
        if readiness_error is not None:
            if recording.status == "unsupported":
                raise BidsSignalInspectionError(readiness_error)
            raise BidsDatasetPathError(readiness_error)
        if recording.format is None:
            raise BidsDatasetPathError(
                "The recording format is unavailable. Correct its BIDS filename and scan again."
            )
        signal_path = root / recording_relative
        source_paths = [signal_path]
        if recording.format == "brainvision":
            source_paths.extend((signal_path.with_suffix(".vmrk"), signal_path.with_suffix(".eeg")))
        elif recording.format == "eeglab":
            fdt_path = signal_path.with_suffix(".fdt")
            if fdt_path.exists() or fdt_path.is_symlink():
                source_paths.append(fdt_path)
        for source_path in source_paths:
            try:
                info = os.lstat(source_path)
            except OSError as exc:
                raise BidsSignalInspectionError(
                    f"Required {recording.format} signal file {source_path.name} is missing "
                    "or unreadable."
                ) from exc
            if source_path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise BidsSignalInspectionError(
                    f"Required {recording.format} signal file {source_path.name} is not safe."
                )
        return root, recording_relative, recording

    @staticmethod
    def _open_signal_raw(root: Path, recording_path: str, file_format: str) -> Any:
        signal_path = root / recording_path
        try:
            mne_bids = import_module("mne_bids")
            get_bids_path_from_fname = mne_bids.get_bids_path_from_fname
            read_raw_bids = mne_bids.read_raw_bids
        except ImportError as exc:
            raise BidsSignalInspectionError(
                "Signal inspection requires the pinned optional EEG dependencies. "
                "Install BrainLearn with its eeg dependency group."
            ) from exc
        try:
            bids_path = get_bids_path_from_fname(signal_path, check=False)
            bids_path.root = root
            return read_raw_bids(bids_path, verbose="ERROR", extra_params={"preload": False})
        except Exception as exc:
            raise BidsSignalInspectionError(
                f"MNE could not read this {file_format} EEG recording. "
                f"Check that its signal file and required companion files are complete: {exc}"
            ) from exc

    @staticmethod
    def _inspection_from_raw(
        raw: Any, recording_relative: str, file_format: str
    ) -> BidsEegSignalInspection:
        sampling_frequency = float(raw.info["sfreq"])
        sample_count = int(raw.n_times)
        channel_types: dict[str, int] = {}
        for channel_type in raw.get_channel_types():
            channel_types[channel_type] = channel_types.get(channel_type, 0) + 1
        descriptions = tuple(dict.fromkeys(str(value) for value in raw.annotations.description))[
            :100
        ]
        return BidsEegSignalInspection(
            recording_path=recording_relative,
            format=cast(Literal["edf", "bdf", "brainvision", "eeglab"], file_format),
            sampling_frequency_hz=sampling_frequency,
            sample_count=sample_count,
            duration_seconds=sample_count / sampling_frequency,
            channel_count=len(raw.ch_names),
            channel_types=channel_types,
            bad_channel_count=len(raw.info["bads"]),
            annotation_count=len(raw.annotations),
            annotation_descriptions=descriptions,
            highpass_hz=float(raw.info["highpass"]),
            lowpass_hz=float(raw.info["lowpass"]),
        )

    def inspect_signal(
        self,
        raw_project_path: str,
        relative_dataset_path: str,
        recording_path: str,
    ) -> BidsEegSignalInspection:
        """Measure raw signal metadata with MNE without preloading or writing data."""

        root, recording_relative, recording = self._resolve_signal_recording(
            raw_project_path, relative_dataset_path, recording_path
        )
        raw = self._open_signal_raw(root, recording_relative, recording.format or "EEG")
        try:
            return self._inspection_from_raw(raw, recording_relative, recording.format or "EEG")
        except BidsSignalInspectionError:
            raise
        except Exception as exc:
            raise BidsSignalInspectionError(
                f"MNE could not inspect this {recording.format} EEG recording: {exc}"
            ) from exc
        finally:
            raw.close()

    def preview_signal(
        self,
        raw_project_path: str,
        relative_dataset_path: str,
        recording_path: str,
        *,
        time_start_seconds: float = 0.0,
        duration_seconds: float = 10.0,
        channel_names: tuple[str, ...] = (),
        max_buckets: int = DEFAULT_PREVIEW_BUCKETS,
    ) -> BidsEegSignalPreview:
        """Return a bounded, downsampled view without storing or changing samples."""

        identity_before = self.identify(raw_project_path, relative_dataset_path, recording_path)
        if identity_before.status != "ready" or identity_before.content_identity is None:
            raise BidsIdentityResourceLimit(
                identity_before.message or "The recording input identity is unavailable."
            )
        root, recording_relative, recording = self._resolve_signal_recording(
            raw_project_path, relative_dataset_path, recording_path
        )
        raw = self._open_signal_raw(root, recording_relative, recording.format or "EEG")
        try:
            np = import_module("numpy")

            sampling_frequency = float(raw.info["sfreq"])
            channel_types = list(raw.get_channel_types())
            channel_names_all = list(raw.ch_names)
            if (
                len(channel_names_all) != len(channel_types)
                or len(channel_names_all) > MAX_CHANNEL_NAMES
            ):
                raise BidsSignalInspectionError(
                    f"This preview supports at most {MAX_CHANNEL_NAMES} recording channels."
                )
            eeg_channel_names = [
                name
                for name, channel_type in zip(channel_names_all, channel_types, strict=True)
                if channel_type == "eeg"
            ]
            selected_names = channel_names or tuple(eeg_channel_names[:DEFAULT_PREVIEW_CHANNELS])
            if not selected_names:
                raise BidsSignalInspectionError(
                    "The recording has no EEG channels available for a signal preview."
                )
            if len(selected_names) > MAX_PREVIEW_CHANNELS or len(set(selected_names)) != len(
                selected_names
            ):
                raise BidsSignalInspectionError(
                    f"Choose between 1 and {MAX_PREVIEW_CHANNELS} unique EEG channels."
                )
            non_eeg = [name for name in selected_names if name not in eeg_channel_names]
            if non_eeg:
                raise BidsSignalInspectionError(
                    "Signal previews currently support EEG channels only; unknown or non-EEG "
                    f"channel requested: {non_eeg[0]}"
                )
            if not math.isfinite(time_start_seconds) or time_start_seconds < 0:
                raise BidsSignalInspectionError(
                    "Preview start time must be finite and non-negative."
                )
            if (
                not math.isfinite(duration_seconds)
                or duration_seconds <= 0
                or duration_seconds > MAX_PREVIEW_DURATION_SECONDS
            ):
                raise BidsSignalInspectionError(
                    f"Preview duration must be greater than zero and at most "
                    f"{MAX_PREVIEW_DURATION_SECONDS:g} seconds."
                )
            if max_buckets < 64 or max_buckets > MAX_PREVIEW_BUCKETS:
                raise BidsSignalInspectionError(
                    f"Preview resolution must be between 64 and {MAX_PREVIEW_BUCKETS} bins."
                )
            sample_count_total = int(raw.n_times)
            start_sample = int(math.floor(time_start_seconds * sampling_frequency))
            if start_sample >= sample_count_total:
                raise BidsSignalInspectionError(
                    "Preview start time is outside the recording duration."
                )
            requested_sample_count = max(1, int(round(duration_seconds * sampling_frequency)))
            stop_sample = min(sample_count_total, start_sample + requested_sample_count)
            selected_sample_count = stop_sample - start_sample
            if selected_sample_count < 8:
                raise BidsSignalInspectionError(
                    "The selected preview interval is too short; choose a window containing "
                    "at least 8 samples."
                )
            if selected_sample_count * len(selected_names) > MAX_PREVIEW_INPUT_SAMPLES:
                raise BidsSignalInspectionError(
                    "This preview window contains too many channel samples. Shorten the time "
                    "window or select fewer channels."
                )
            bucket_count = min(max_buckets, selected_sample_count)
            values = np.asarray(
                raw.get_data(
                    picks=list(selected_names),
                    start=start_sample,
                    stop=stop_sample,
                    reject_by_annotation=None,
                    units="uV",
                    verbose="ERROR",
                ),
                dtype=float,
            )
            if values.shape != (len(selected_names), selected_sample_count):
                raise BidsSignalInspectionError(
                    "MNE returned an unexpected shape for the selected preview samples."
                )
            if not np.isfinite(values).all():
                raise BidsSignalInspectionError(
                    "MNE returned non-finite samples; this recording cannot be previewed safely."
                )
            actual_start_seconds = start_sample / sampling_frequency
            actual_end_seconds = stop_sample / sampling_frequency
            traces: list[BidsEegTracePreview] = []
            for channel_index, channel_name in enumerate(selected_names):
                bins: list[BidsEegTraceBin] = []
                for bucket_index in range(bucket_count):
                    first = bucket_index * selected_sample_count // bucket_count
                    last = (bucket_index + 1) * selected_sample_count // bucket_count
                    segment = values[channel_index, first:last]
                    bins.append(
                        BidsEegTraceBin(
                            time_seconds=actual_start_seconds
                            + ((first + last - 1) / 2) / sampling_frequency,
                            minimum_uv=float(segment.min()),
                            maximum_uv=float(segment.max()),
                        )
                    )
                traces.append(BidsEegTracePreview(channel_name=channel_name, bins=tuple(bins)))

            annotation_events: list[BidsEegEventPreview] = []
            event_count = 0
            preview_start = actual_start_seconds
            preview_end = actual_end_seconds
            raw_first_time = float(raw.first_time)
            for onset, duration, description in zip(
                raw.annotations.onset,
                raw.annotations.duration,
                raw.annotations.description,
                strict=True,
            ):
                relative_onset = float(onset) - raw_first_time
                event_duration = max(0.0, float(duration))
                event_end = relative_onset + event_duration
                if event_end < preview_start or relative_onset > preview_end:
                    continue
                event_count += 1
                if len(annotation_events) < MAX_PREVIEW_EVENTS:
                    annotation_events.append(
                        BidsEegEventPreview(
                            onset_seconds=relative_onset,
                            duration_seconds=event_duration,
                            description=str(description)[:256],
                        )
                    )
            annotation_events.sort(key=lambda event: event.onset_seconds)

            n_per_seg = min(PREVIEW_SPECTRUM_N_FFT, selected_sample_count)
            n_overlap = min(n_per_seg // 2, PREVIEW_SPECTRUM_N_FFT // 2)
            max_frequency = min(100.0, sampling_frequency / 2)
            spectrum = raw.compute_psd(
                method="welch",
                fmin=0.0,
                fmax=max_frequency,
                tmin=actual_start_seconds,
                tmax=(stop_sample - 1) / sampling_frequency,
                picks=list(selected_names),
                reject_by_annotation=False,
                n_fft=PREVIEW_SPECTRUM_N_FFT,
                n_per_seg=n_per_seg,
                n_overlap=n_overlap,
                window="hamming",
                average="mean",
                verbose="ERROR",
            )
            powers, frequencies = spectrum.get_data(return_freqs=True)
            power_values = np.asarray(powers, dtype=float) * 1e12
            frequencies = np.asarray(frequencies, dtype=float)
            if (
                power_values.shape != (len(selected_names), len(frequencies))
                or not np.isfinite(power_values).all()
                or not np.isfinite(frequencies).all()
            ):
                raise BidsSignalInspectionError(
                    "MNE returned invalid spectral values for the selected preview interval."
                )
            spectrum_preview = BidsEegSpectrumPreview(
                n_fft=PREVIEW_SPECTRUM_N_FFT,
                n_per_seg=n_per_seg,
                n_overlap=n_overlap,
                frequencies_hz=tuple(float(value) for value in frequencies),
                traces=tuple(
                    BidsEegSpectrumTrace(
                        channel_name=channel_name,
                        power_uv2_per_hz=tuple(float(value) for value in power_values[index]),
                    )
                    for index, channel_name in enumerate(selected_names)
                ),
            )
            channels = tuple(
                BidsEegPreviewChannel(
                    name=name,
                    channel_type=channel_type,
                    marked_bad=name in raw.info["bads"],
                )
                for name, channel_type in zip(channel_names_all, channel_types, strict=True)
            )
        except BidsSignalInspectionError:
            raise
        except Exception as exc:
            raise BidsSignalInspectionError(
                f"MNE could not create this bounded EEG signal preview: {exc}"
            ) from exc
        finally:
            raw.close()

        identity_after = self.identify(raw_project_path, relative_dataset_path, recording_path)
        if (
            identity_after.status != "ready"
            or identity_after.content_identity != identity_before.content_identity
        ):
            raise BidsSignalInspectionError(
                "The recording changed while the signal preview was being created; "
                "refusing to return a stale preview."
            )
        return BidsEegSignalPreview(
            recording_path=recording_relative,
            source_content_identity=identity_before.content_identity,
            time_start_seconds=actual_start_seconds,
            duration_seconds=actual_end_seconds - actual_start_seconds,
            sampling_frequency_hz=sampling_frequency,
            sample_count=selected_sample_count,
            channels=channels,
            traces=tuple(traces),
            events=tuple(annotation_events[:MAX_PREVIEW_EVENTS]),
            event_count=event_count,
            events_truncated=event_count > MAX_PREVIEW_EVENTS,
            spectrum=spectrum_preview,
        )

    def identify(
        self,
        raw_project_path: str,
        relative_dataset_path: str,
        recording_path: str,
    ) -> BidsEegInputIdentity:
        """Hash the source files that define one ready recording without copying them."""

        root, relative = self._resolve_dataset(raw_project_path, relative_dataset_path)
        try:
            recording_relative = validate_relative_path(recording_path, "Recording path")
        except ValueError as exc:
            raise BidsDatasetPathError(
                "Choose a discovered recording inside this dataset."
            ) from exc
        discovery = discover_bids_eeg(root, relative)
        recording = next(
            (item for item in discovery.recordings if item.path == recording_relative), None
        )
        if recording is None:
            raise BidsDatasetPathError(
                "The selected path is not a discovered recording. Scan the dataset and choose "
                "a listed recording."
            )
        readiness_error = _recording_readiness_error(discovery, recording)
        if readiness_error is not None:
            raise BidsDatasetPathError(readiness_error)
        signal_path = root / recording_relative
        entities = _parse_entities(signal_path.stem, _EEG_SUFFIX)
        file_format = _SUPPORTED_EEG_EXTENSIONS.get(signal_path.suffix)
        if entities is None:
            raise BidsDatasetPathError(
                "The selected recording has an invalid BIDS filename. Correct its BIDS entities "
                "and scan again."
            )
        if file_format is None:
            raise BidsDatasetPathError(
                f"The selected recording has an unsupported EEG format. "
                f"{_SUPPORTED_EEG_FORMATS_MESSAGE}"
            )
        directory = signal_path.parent
        budget = _DirectoryEntryBudget()
        source_paths = [signal_path]
        if file_format == "brainvision":
            source_paths.extend((signal_path.with_suffix(".vmrk"), signal_path.with_suffix(".eeg")))
        elif file_format == "eeglab":
            fdt_path = signal_path.with_suffix(".fdt")
            if fdt_path.exists() or fdt_path.is_symlink():
                source_paths.append(fdt_path)

        try:
            source_paths.extend(
                _metadata_candidates(root, directory, entities, "eeg", ".json", budget)
            )
            for suffix, extension in (("channels", ".tsv"), ("events", ".tsv")):
                candidates = _metadata_candidates(
                    root, directory, entities, suffix, extension, budget
                )
                if candidates:
                    # These table sidecars replace broader inherited tables; this is the
                    # same effective sidecar selection used during metadata inspection.
                    source_paths.append(candidates[-1])
        except BidsMetadataError as exc:
            raise BidsDatasetPathError(str(exc)) from None

        source_paths = sorted(set(source_paths), key=lambda path: path.relative_to(root).as_posix())
        if len(source_paths) > MAX_DISCOVERY_ENTRIES:
            raise BidsIdentityResourceLimit(
                "This recording has too many source files to identify safely."
            )
        directory_snapshots = _snapshot_directories(root, directory)
        source_records: list[tuple[Path, os.stat_result]] = []
        total_bytes = 0
        for path in source_paths:
            relpath = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise BidsDatasetPathError(
                    f"Source file {relpath} is a symlink and cannot be hashed safely."
                )
            try:
                info = os.lstat(path)
            except OSError:
                raise BidsDatasetPathError(
                    f"Source file {relpath} is missing or unreadable."
                ) from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise BidsDatasetPathError(f"Source file {relpath} is not a safe regular file.")
            source_records.append((path, info))
            total_bytes += info.st_size
            if total_bytes > MAX_INPUT_IDENTITY_BYTES:
                return BidsEegInputIdentity(
                    recording_path=recording_relative,
                    status="resource_limit",
                    message=(
                        "Recording source files exceed the 10 GiB identity limit. "
                        "No identity was created."
                    ),
                )

        final_discovery = discover_bids_eeg(root, relative)
        final_recording = next(
            (item for item in final_discovery.recordings if item.path == recording_relative), None
        )
        final_error = (
            _recording_readiness_error(final_discovery, final_recording)
            if final_recording is not None
            else None
        )
        if (
            final_recording is None
            or final_recording.status != "ready"
            or final_recording.format != file_format
            or final_discovery.status != "ready"
        ):
            raise BidsDatasetPathError(
                final_error
                or "The BIDS recording changed while its identity was being created. "
                "Scan again before retrying."
            )
        if directory_snapshots != _snapshot_directories(root, directory):
            raise BidsDatasetPathError(
                "The BIDS directory changed while its metadata was being checked."
            )
        files: list[BidsEegIdentityFile] = []
        for path, info in source_records:
            relpath = path.relative_to(root).as_posix()
            digest = _hash_identity_file(path, info, relpath)
            files.append(BidsEegIdentityFile(path=relpath, byte_size=info.st_size, sha256=digest))
        for path, info in source_records:
            try:
                current = os.lstat(path)
            except OSError:
                raise BidsDatasetPathError(
                    "A source file changed while the input identity was being created."
                ) from None
            if _stat_identity(current) != _stat_identity(info):
                raise BidsDatasetPathError(
                    "A source file changed while the input identity was being created."
                )
        if directory_snapshots != _snapshot_directories(root, directory):
            raise BidsDatasetPathError(
                "The BIDS directory changed while source files were being hashed."
            )
        identity = content_identity(
            "artifact",
            {
                "schema_version": "bids-eeg-input-1.0",
                "recording_path": recording_relative,
                "files": [item.model_dump(mode="json") for item in files],
            },
        )
        return BidsEegInputIdentity(
            recording_path=recording_relative,
            status="ready",
            content_identity=identity,
            files=tuple(files),
        )


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _snapshot_directories(
    root: Path, recording_dir: Path
) -> tuple[tuple[str, tuple[int, ...]], ...]:
    directories: list[Path] = []
    current = recording_dir
    while True:
        if current != root and root not in current.parents:
            raise BidsDatasetPathError("The recording directory escaped the selected dataset.")
        directories.append(current)
        if current == root:
            break
        current = current.parent
    snapshots: list[tuple[str, tuple[int, ...]]] = []
    for directory in directories:
        try:
            info = os.lstat(directory)
        except OSError:
            raise BidsDatasetPathError("A BIDS directory changed during source hashing.") from None
        if not stat.S_ISDIR(info.st_mode):
            raise BidsDatasetPathError("A BIDS directory is unsafe during source hashing.")
        snapshots.append((directory.as_posix(), _stat_identity(info)))
    return tuple(snapshots)


def _hash_identity_file(path: Path, expected: os.stat_result, relative_path: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise BidsDatasetPathError(f"Source file {relative_path} is missing or blocked.") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
            raise BidsDatasetPathError(f"Source file {relative_path} is not a safe regular file.")
        if _stat_identity(before) != _stat_identity(expected):
            raise BidsDatasetPathError(f"Source file {relative_path} changed before hashing.")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(descriptor, INPUT_IDENTITY_CHUNK_BYTES)
            except OSError:
                raise BidsDatasetPathError(
                    f"Source file {relative_path} could not be read."
                ) from None
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_INPUT_IDENTITY_BYTES:
                raise BidsIdentityResourceLimit(
                    "A source file exceeds the 10 GiB identity limit. No identity was created."
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            current_path = os.lstat(path)
        except OSError:
            raise BidsDatasetPathError(
                f"Source file {relative_path} changed during hashing."
            ) from None
        if (
            total != before.st_size
            or _stat_identity(before) != _stat_identity(after)
            or (_stat_identity(before) != _stat_identity(current_path))
        ):
            raise BidsDatasetPathError(f"Source file {relative_path} changed during hashing.")
        return digest.hexdigest()
    finally:
        os.close(descriptor)
