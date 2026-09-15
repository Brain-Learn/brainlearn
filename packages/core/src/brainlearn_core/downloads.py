"""Versioned dataset-download transfer records (no networking).

This module fixes the persisted shape of one download of an explicitly
selected immutable public snapshot into a project: which snapshot, how many
bytes are verified on disk, which lifecycle state the transfer is in, how
many bounded attempts were spent, and why it failed. Byte movement itself
lives in the server downloader; providers only supply streamed bytes through
the download-source boundary.

State machine::

    queued -> downloading -> succeeded
       |           |  ^
       v           v  | (resume re-queues)
    paused      cancelled
       |           |
       +--> failed (attempts exhausted or unverifiable bytes)

Only ``succeeded`` means bytes on disk: it requires every expected file
verified (checksum where the catalog carries one, exact size always), the
finalized tree in place, and a lock record whose identity was recomputed
from verified facts. Recovery after a restart moves ``downloading`` back to
``queued`` with byte accounting intact; it never invents success.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from brainlearn_core.datasets import (
    CatalogEntry,
    _file_sort_key,
    catalog_entry_identity,
    validate_relative_path,
)

DOWNLOAD_SCHEMA_VERSION: Literal["1.0"] = "1.0"
SUPPORTED_DOWNLOAD_VERSIONS: tuple[str, ...] = ("1.0",)
DOWNLOAD_ID_PATTERN = r"^dl-[0-9a-f]{12}$"
DOWNLOAD_MAX_ATTEMPTS = 3


class DownloadState(StrEnum):
    """Lifecycle states of one dataset download."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


DOWNLOAD_TERMINAL_STATES: tuple[str, ...] = ("cancelled", "failed", "succeeded")
DOWNLOAD_RESUMABLE_STATES: tuple[str, ...] = ("queued", "paused", "cancelled")


def _require_iso_timestamp(value: str, field_name: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp, got {value!r}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} must include a timezone offset, got {value!r}. "
            "Naive timestamps cannot be ordered reliably across platforms."
        )
    return value


class DownloadFileState(BaseModel):
    """Progress and verification state of one expected file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bytes_completed: int = Field(ge=0, default=0)
    verified: bool = False

    @field_validator("path")
    @classmethod
    def _path_must_be_portable(cls, value: str) -> str:
        return validate_relative_path(value, "Expected file path")

    @model_validator(mode="after")
    def _progress_must_be_consistent(self) -> DownloadFileState:
        if self.bytes_completed > self.byte_size:
            raise ValueError(f"Downloaded bytes for {self.path!r} exceed its declared size.")
        if self.verified and self.bytes_completed != self.byte_size:
            raise ValueError(f"Verified file {self.path!r} must be fully downloaded.")
        return self


class DownloadFailure(BaseModel):
    """Why a download failed, in stable researcher-actionable codes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class DownloadRecord(BaseModel):
    """One download of an immutable public snapshot into a project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = DOWNLOAD_SCHEMA_VERSION
    download_id: str = Field(pattern=DOWNLOAD_ID_PATTERN)
    provider: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    snapshot: str = Field(min_length=1)
    catalog_identity: str = Field(pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$")
    catalog_entry: CatalogEntry
    expected_total_bytes: int = Field(ge=0)
    files: tuple[DownloadFileState, ...] = Field(min_length=1)
    bytes_completed: int = Field(ge=0, default=0)
    state: DownloadState = DownloadState.QUEUED
    attempt: int = Field(ge=0, default=0)
    max_attempts: int = Field(ge=1, default=DOWNLOAD_MAX_ATTEMPTS)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    failure: DownloadFailure | None = None
    lock_identity: str | None = Field(default=None, pattern=r"^brainlearn-v1:dataset:[0-9a-f]{64}$")

    @field_validator("created_at", "updated_at")
    @classmethod
    def _timestamps_must_be_iso(cls, value: str, info: Any) -> str:
        return _require_iso_timestamp(value, info.field_name or "timestamp")

    @field_validator("files", mode="before")
    @classmethod
    def _files_must_be_canonical(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return sorted(value, key=_file_sort_key)
        return value

    @model_validator(mode="after")
    def _must_match_embedded_catalog(self) -> DownloadRecord:
        """Bind every persisted transfer field to the embedded catalog entry.

        A corrupted record must never fetch a different source triple or
        verify/finalize a different file set while retaining the original
        catalog identity.
        """

        entry = self.catalog_entry
        if self.provider != entry.provider:
            raise ValueError("Transfer provider must equal the embedded catalog provider.")
        if self.dataset_id != entry.dataset_id:
            raise ValueError("Transfer dataset id must equal the embedded catalog dataset id.")
        if self.snapshot != entry.snapshot:
            raise ValueError("Transfer snapshot must equal the embedded catalog snapshot.")
        if self.expected_total_bytes != entry.expected_total_bytes:
            raise ValueError("Transfer expected total must equal the embedded catalog total.")
        recomputed = catalog_entry_identity(
            provider=entry.provider,
            dataset_id=entry.dataset_id,
            snapshot=entry.snapshot,
            modality=entry.modality,
            task=entry.task,
            participants=entry.participants,
            formats=list(entry.formats),
            approximate_total_bytes=entry.approximate_total_bytes,
            expected_total_bytes=entry.expected_total_bytes,
            expected_files=[item.model_dump(mode="json") for item in entry.expected_files],
            access=entry.access.value,
            license_name=entry.license_name,
            license_spdx=entry.license_spdx,
            reuse_statement=entry.reuse_statement,
            citations=[item.model_dump(mode="json") for item in entry.citations],
            landing_page=entry.landing_page,
            compatible_templates=list(entry.compatible_templates),
        )
        if self.catalog_identity != recomputed or entry.catalog_identity != recomputed:
            raise ValueError("Transfer catalog identity does not match its embedded catalog entry.")
        expected = [(item.path, item.byte_size, item.sha256) for item in entry.expected_files]
        actual = [(item.path, item.byte_size, item.sha256) for item in self.files]
        if actual != expected:
            raise ValueError(
                "Transfer files must be the canonical projection of the catalog "
                "expectations (same order, paths, sizes, and declared checksums)."
            )
        return self

    @model_validator(mode="after")
    def _accounting_must_be_consistent(self) -> DownloadRecord:
        if sum(item.bytes_completed for item in self.files) != self.bytes_completed:
            raise ValueError("Record bytes_completed must equal the sum over files.")
        if self.attempt > self.max_attempts:
            raise ValueError("Spent attempts must not exceed max_attempts.")
        if self.state == DownloadState.SUCCEEDED:
            if not all(item.verified for item in self.files):
                raise ValueError("A succeeded download must verify every file.")
            if self.lock_identity is None:
                raise ValueError("A succeeded download must record its lock identity.")
            if self.failure is not None:
                raise ValueError("A succeeded download must not carry a failure.")
        elif self.lock_identity is not None:
            raise ValueError("Only a succeeded download may record its lock identity.")
        if self.state == DownloadState.FAILED and self.failure is None:
            raise ValueError("A failed download must record its failure.")
        if self.state in (DownloadState.QUEUED, DownloadState.DOWNLOADING) and self.failure:
            raise ValueError("Active downloads must not carry a stale failure.")
        return self

    def with_progress(
        self, *, files: tuple[DownloadFileState, ...], updated_at: str
    ) -> DownloadRecord:
        """Return a copy with refreshed per-file progress and totals."""

        return self.model_copy(
            update={
                "files": files,
                "bytes_completed": sum(item.bytes_completed for item in files),
                "updated_at": updated_at,
            }
        )


def migrate_download_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate a raw download record dict to the current schema."""

    version = data.get("schema_version")
    if version not in SUPPORTED_DOWNLOAD_VERSIONS:
        raise ValueError(
            f"Unsupported download schema_version {version!r}. "
            f"Supported versions: {', '.join(SUPPORTED_DOWNLOAD_VERSIONS)}. "
            "Open the transfer with a compatible BrainLearn release."
        )
    return data
