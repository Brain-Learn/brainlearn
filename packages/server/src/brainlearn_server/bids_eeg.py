"""Read-only BIDS EEG recording discovery and essential metadata checks.

The scanner reads only BIDS JSON/TSV metadata. It never opens EEG signal bytes,
computes content identities, or changes dataset files. It intentionally reports
essential metadata readiness rather than claiming full BIDS-validator coverage.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from brainlearn_core import validate_relative_path
from pydantic import BaseModel, ConfigDict, Field

from brainlearn_server.project_store import ProjectStore, canonicalize_project_path

MAX_DISCOVERY_ENTRIES = 50_000
MAX_METADATA_ENTITY_KEYS = 12
MAX_METADATA_QUERY_MULTIPLIER = 256
MAX_JSON_BYTES = 1_048_576
MAX_TSV_BYTES = 16_777_216
MAX_TSV_ROWS = 200_000
MAX_CHANNEL_NAMES = 512
MAX_EVENT_TYPES = 1_000
_BIDS_VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")
_SUPPORTED_EEG_EXTENSIONS = {
    ".edf": "edf",
    ".bdf": "bdf",
    ".vhdr": "brainvision",
    ".set": "eeglab",
}
_EEG_SUFFIX = "_eeg"


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


class BidsDatasetPathError(ValueError):
    """The selected project-relative dataset directory cannot be scanned safely."""


class BidsMetadataError(ValueError):
    """A candidate metadata file cannot be read or parsed safely."""


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
    if not set(required).issubset(fields) or len(fields) != len(set(fields)):
        raise BidsMetadataError("Metadata TSV is missing required or unique column names.")
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
                    message="EEG recording filenames must include a task entity.",
                    path=entry.relative_to(root).as_posix(),
                )
            )
        extension = entry.suffix
        file_format = _SUPPORTED_EEG_EXTENSIONS.get(extension)
        if not file_format:
            issues.append(
                BidsEegIssue(
                    code="unsupported_format",
                    message="This EEG file format is not supported for discovery.",
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
                                "files."
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
                message="dataset_description.json must not be a symlink.",
                path="dataset_description.json",
            )
        )
    elif not description_path.is_file():
        issues.append(
            BidsEegIssue(
                code="missing_dataset_description",
                message="BIDS dataset_description.json is missing.",
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
                    message="dataset_description.json must provide a non-empty Name.",
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
                        "dataset_description.json must provide a numeric BIDSVersion such as 1.2.0."
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
                        message="This EEG metadata scanner supports BIDS major version 1 only.",
                        path="dataset_description.json",
                    )
                )
        dataset_type = description.get("DatasetType", "raw")
        if dataset_type != "raw":
            issues.append(
                BidsEegIssue(
                    code="unsupported_dataset_type",
                    message="BIDS derivatives are not supported as raw EEG inputs.",
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
            try:
                sidecars = _metadata_candidates(root, directory, entities, "eeg", ".json", budget)
                if not sidecars:
                    recording_issues.append(
                        BidsEegIssue(
                            code="missing_eeg_sidecar",
                            message="No matching or inherited *_eeg.json sidecar was found.",
                            path=relative,
                        )
                    )
                for sidecar in sidecars:
                    metadata.update(_read_json_object(sidecar))
            except BidsMetadataError as exc:
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_eeg_sidecar",
                        message=str(exc),
                        path=relative,
                    )
                )

            sampling_frequency = _finite_number(metadata.get("SamplingFrequency"))
            if sampling_frequency is None or sampling_frequency <= 0:
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_sampling_frequency",
                        message="EEG metadata must provide a positive numeric SamplingFrequency.",
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
            if eeg_reference is None:
                recording_issues.append(
                    BidsEegIssue(
                        code="missing_eeg_reference",
                        message="EEG metadata must provide a non-empty EEGReference.",
                        path=relative,
                    )
                )
            task_name = metadata.get("TaskName")
            if not isinstance(task_name, str) or not task_name.strip():
                recording_issues.append(
                    BidsEegIssue(
                        code="missing_task_name",
                        message="EEG metadata must provide a non-empty TaskName.",
                        path=relative,
                    )
                )
            power_line_frequency = metadata.get("PowerLineFrequency")
            if power_line_frequency != "n/a":
                numeric_power_line = _finite_number(power_line_frequency)
                if numeric_power_line is None or numeric_power_line <= 0:
                    recording_issues.append(
                        BidsEegIssue(
                            code="invalid_power_line_frequency",
                            message=(
                                "EEG metadata must provide a positive PowerLineFrequency or 'n/a'."
                            ),
                            path=relative,
                        )
                    )
            software_filters = metadata.get("SoftwareFilters")
            if software_filters != "n/a" and (
                not isinstance(software_filters, dict)
                or any(
                    not isinstance(name, str) or not isinstance(parameters, dict)
                    for name, parameters in software_filters.items()
                )
            ):
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_software_filters",
                        message="EEG metadata must provide SoftwareFilters as an object or 'n/a'.",
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
            try:
                channels_files = _metadata_candidates(
                    root, directory, entities, "channels", ".tsv", budget
                )
                if channels_files:
                    channel_rows = _read_tsv(channels_files[-1], required=("name", "type", "units"))
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
                        path=relative,
                    )
                )

            event_count: int | None = None
            event_types: tuple[str, ...] = ()
            try:
                events_files = _metadata_candidates(
                    root, directory, entities, "events", ".tsv", budget
                )
                if events_files:
                    event_rows = _read_tsv(events_files[-1], required=("onset", "duration"))
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
            except BidsMetadataError as exc:
                recording_issues.append(
                    BidsEegIssue(
                        code="invalid_events_tsv",
                        message=str(exc),
                        path=relative,
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

    def discover(self, raw_project_path: str, relative_dataset_path: str) -> BidsEegDiscovery:
        try:
            project = self.projects.require_allowed(canonicalize_project_path(raw_project_path))
            relative = validate_relative_path(relative_dataset_path, "Dataset path")
        except (PermissionError, ValueError) as exc:
            raise BidsDatasetPathError(
                "Open an authorized project and choose a project-relative dataset path."
            ) from exc
        if not (project / "project.json").is_file() or not (project / "workflow.json").is_file():
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
        return discover_bids_eeg(resolved, relative)
