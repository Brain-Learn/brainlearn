"""Verified dataset download lifecycle (no archive extraction).

One download fulfills the exact file set of an immutable public snapshot:
bytes stream into a unique partial tree, every file is verified (catalog
checksum where one exists, exact declared size always, with hashes computed
over landed bytes for the lock), and only then is the tree atomically
renamed into ``datasets/<provider>/<dataset-id>/<snapshot>/`` with a lock
record whose identity is recomputed from verified facts.

Safety properties:

- Every record, staging, and destination path crosses one component-checked
  storage boundary (:meth:`DownloadService._verified`): each existing
  ancestor must be a real directory inside the authorized project with no
  symlink, and the boundary is re-proved immediately before every open,
  write, mkdir, hash, rename, and lock publication. Nothing is ever moved
  or deleted through an untrusted ancestor.
- Destinations are authorized and revalidated before any byte is written;
  free space must cover the catalog expectation plus
  :data:`DOWNLOAD_DISK_MARGIN_BYTES`.
- Memory stays bounded: sources are consumed chunk by chunk, never buffered,
  and verification reads bounded chunks through no-follow descriptors.
- Partial output is never a successful output: success requires full
  verification, an atomic rename, an atomic lock write, and a durable
  succeeded record, in that order. A failure after the rename returns the
  owned tree to its unique staging location (or parks the transfer when the
  tree cannot be proven ours), so a terminal failure never coexists with an
  unlocked finalized dataset.
- Restart recovery requeues interrupted transfers with byte accounting
  re-derived from disk, and adopts a finalized tree as succeeded only after
  re-hashing every expected file through the hardened boundary; it never
  invents success from sizes alone.
- Failure and HTTP messages are static strings plus validated identifiers
  and counts only; provider exception text is never persisted or returned.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brainlearn_core import (
    OPENNEURO_PROVIDER,
    CatalogEntry,
    DatasetLock,
    DownloadFailure,
    DownloadFileState,
    DownloadRecord,
    DownloadState,
    FileDownloadSource,
    OpenNeuroDownloadSource,
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
    RangeUnsupportedError,
    VerifiedFile,
    migrate_download_dict,
    project_lock_from_catalog,
)
from brainlearn_core.projects import utc_now_iso

from brainlearn_server.datasets import get_dataset_provider
from brainlearn_server.project_store import (
    ProjectStore,
    atomic_write_json,
    canonicalize_project_path,
)

DOWNLOAD_DISK_MARGIN_BYTES = 64 * 1024 * 1024
DOWNLOAD_ID_PATTERN = re.compile(r"^dl-[0-9a-f]{12}$")
DOWNLOAD_LOCK_FILENAME = "dataset-lock-1.0.json"
DOWNLOADS_DIRNAME = "downloads"
DATASETS_DIRNAME = "datasets"
PARTIAL_DIRNAME = ".partial"
TRANSFER_FILENAME = "transfer.json"
VERIFY_CHUNK_BYTES = 1024 * 1024
MAX_SCAN_FILES = 100_000
MAX_SCAN_DEPTH = 64
_RESUMABLE_STATES = ("queued", "paused", "cancelled")


class CancelledByUser(Exception):
    """Cooperative download cancellation observed by the driver."""


class DownloadConflictError(ValueError):
    """A download cannot start or transition in its current state."""


class UnsafeStorageError(DownloadConflictError):
    """A download storage path is not provably owned by the project."""


class DownloadNotFoundError(FileNotFoundError):
    """No such download record or dataset snapshot exists (or is public)."""


class DiskFullError(OSError):
    """Free space cannot cover the expected bytes plus the safety margin."""


class _DiskFullSignal(Exception):
    """ENOSPC observed mid-write; the driver pauses instead of failing."""


class _VerificationMismatch(ValueError):
    """Bytes on disk do not match the catalog or lock expectations."""


class _FileFailed(Exception):
    """One file pass failed; carries a stable code, path, and retryability."""

    def __init__(self, code: str, path: str = "", *, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.path = path
        self.retryable = retryable


class _PrefixVanished(Exception):
    """The resume prefix shrank before it could be hashed; restart the file."""


def _failure_message(code: str, path: str, attempt: int, max_attempts: int) -> str:
    explanations = {
        "timeout": "The source timed out while sending bytes.",
        "connection_failed": "The source connection failed while sending bytes.",
        "incomplete_download": "The source ended the file before its declared size.",
        "checksum_mismatch": "Downloaded bytes do not match the catalog checksum.",
        "size_mismatch": "The source sent more bytes than the catalog declares.",
        "disk_full": "Free space ran out while writing bytes.",
        "destination_exists": (
            "A dataset directory occupies the destination without a valid lock. "
            "Inspect and remove it, then resume."
        ),
        "unexpected_symlink": "A symlink appeared inside the download tree.",
        "unsafe_storage": "A download storage path is blocked by a symlink or non-directory.",
        "finalize_failed": "Verification or lock publication failed after the bytes landed.",
        "interrupted": "The service stopped before the download finished.",
        "internal_error": "The downloader hit an unexpected local error.",
    }
    location = f" File {path!r}," if path else ""
    return (
        f"{explanations.get(code, 'The download failed.')}"
        f"{location} attempt {attempt} of {max_attempts}."
    )


def _path_parts(relative: str) -> tuple[str, ...]:
    parts = tuple(relative.replace("\\", "/").split("/"))
    for part in parts:
        if not part or part in (".", "..") or "\x00" in part:
            raise UnsafeStorageError(f"Download file path {relative!r} is not portable.")
    return parts


@dataclass
class DownloadService:
    """Owns download records, driver threads, and recovery for projects."""

    projects: ProjectStore
    providers_override: dict[str, Any] | None = None
    sources_override: dict[str, FileDownloadSource] | None = None
    _openneuro_source: OpenNeuroDownloadSource | None = None
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _threads: dict[str, threading.Thread] = field(default_factory=dict)
    _cancel: dict[str, threading.Event] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    # -- wiring --------------------------------------------------------

    def _source_for(self, name: str) -> FileDownloadSource:
        if self.sources_override is not None:
            source = self.sources_override.get(name)
            if source is None:
                raise DownloadNotFoundError(f"Unknown dataset provider {name}.")
            return source
        if name == OPENNEURO_PROVIDER:
            if self._openneuro_source is None:
                self._openneuro_source = OpenNeuroDownloadSource()
            return self._openneuro_source
        raise DownloadNotFoundError(f"Unknown dataset provider {name}.")

    def _provider_for(self, name: str) -> Any:
        if self.providers_override is not None:
            provider = self.providers_override.get(name)
            if provider is None:
                raise DownloadNotFoundError(f"Unknown dataset provider {name}.")
            return provider
        return get_dataset_provider(name)

    # -- storage boundary ----------------------------------------------

    def _verified(self, project: Path, parts: Sequence[str], *, what: str) -> Path:
        """Prove one project-relative path is contained and symlink-free.

        Every component is checked lexically, each existing ancestor must be
        a real directory, no component may be a symlink, and the fully
        resolved path must stay inside the authorized project. Callers
        re-invoke this immediately before each filesystem operation so a
        path swapped in between is refused rather than followed.
        """

        root = project.resolve(strict=False)
        current = project
        last = len(parts) - 1
        for index, part in enumerate(parts):
            if not part or part in (".", "..") or "/" in part or "\\" in part or "\x00" in part:
                raise UnsafeStorageError(f"{what} must use plain path components.")
            current = current / part
            if current.is_symlink():
                raise UnsafeStorageError(f"{what} is blocked by a symlink at {part!r}.")
            if index < last and current.exists() and not current.is_dir():
                raise UnsafeStorageError(f"{what} is blocked by a non-directory at {part!r}.")
        resolved = current.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise UnsafeStorageError(f"{what} escapes the authorized project.")
        self.projects.require_allowed(resolved)
        return resolved

    def _makedirs_verified(self, project: Path, parts: Sequence[str], *, what: str) -> Path:
        """Create missing directories one component at a time, re-verifying."""

        for depth in range(1, len(parts) + 1):
            target = self._verified(project, parts[:depth], what=what)
            if target.exists():
                if not target.is_dir():
                    raise UnsafeStorageError(f"{what} is blocked by a non-directory.")
                continue
            try:
                target.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise UnsafeStorageError(f"{what} could not be created.") from exc
            if target.is_symlink() or not target.is_dir():
                raise UnsafeStorageError(f"{what} could not be created safely.")
        return self._verified(project, parts, what=what)

    def _record_parts(self, download_id: str) -> tuple[str, ...]:
        if DOWNLOAD_ID_PATTERN.match(download_id) is None:
            raise ValueError(f"Unknown dataset download {download_id!r}.")
        return (DOWNLOADS_DIRNAME, download_id, TRANSFER_FILENAME)

    def _partial_parts(self, download_id: str) -> tuple[str, ...]:
        if DOWNLOAD_ID_PATTERN.match(download_id) is None:
            raise ValueError(f"Unknown dataset download {download_id!r}.")
        return (DATASETS_DIRNAME, PARTIAL_DIRNAME, download_id)

    def _final_parts(self, entry: CatalogEntry) -> tuple[str, ...]:
        return (DATASETS_DIRNAME, entry.provider, entry.dataset_id, entry.snapshot)

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    # -- bounded, no-follow reads ---------------------------------------

    def _open_no_follow(self, path: Path, flags: int, *, what: str, mode: int = 0o644) -> int:
        try:
            return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), mode)
        except FileNotFoundError:
            raise _VerificationMismatch(f"{what} is no longer present.") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise UnsafeStorageError(f"{what} must not be a symlink.") from None
            raise _VerificationMismatch(f"{what} could not be opened.") from exc

    def _hash_regular(self, path: Path, expected_size: int, *, what: str) -> str:
        """Hash one regular file through a no-follow descriptor, bounded."""

        fd = self._open_no_follow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0), what=what)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _VerificationMismatch(f"{what} is not a regular file.")
            if info.st_size != expected_size:
                raise _VerificationMismatch(f"{what} does not have its expected size.")
            digest = hashlib.sha256()
            remaining = expected_size
            while remaining > 0:
                try:
                    chunk = os.read(fd, min(VERIFY_CHUNK_BYTES, remaining))
                except OSError as exc:
                    raise _VerificationMismatch(f"{what} could not be read.") from exc
                if not chunk:
                    raise _VerificationMismatch(f"{what} ended before its expected size.")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                raise _VerificationMismatch(f"{what} grew past its expected size.")
            return digest.hexdigest()
        finally:
            os.close(fd)

    def _scan_regular_files(
        self, project: Path, dir_parts: Sequence[str], *, what: str
    ) -> dict[str, int]:
        """Map every regular file under a verified tree to its size.

        Refuses symlinks and non-regular objects anywhere in the walk and
        stays bounded, so an oversized or hostile tree cannot be adopted.
        """

        root = self._verified(project, dir_parts, what=what)
        if not root.is_dir():
            raise _VerificationMismatch(f"{what} is not a directory.")
        found: dict[str, int] = {}
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            depth = len(Path(current).relative_to(root).parts)
            if depth > MAX_SCAN_DEPTH:
                raise _VerificationMismatch(f"{what} is nested too deep to verify.")
            base = Path(current)
            for name in list(dirnames):
                if (base / name).is_symlink():
                    raise UnsafeStorageError(f"{what} contains a symlinked directory.")
            for name in filenames:
                child = base / name
                if child.is_symlink():
                    raise UnsafeStorageError(f"{what} contains a symlink.")
                info = child.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise _VerificationMismatch(f"{what} contains a non-regular file.")
                if len(found) >= MAX_SCAN_FILES:
                    raise _VerificationMismatch(f"{what} holds too many files to verify.")
                found[str(child.relative_to(root)).replace(os.sep, "/")] = info.st_size
        return found

    # -- records -------------------------------------------------------

    def _load_record(self, project: Path, download_id: str) -> DownloadRecord:
        parts = self._record_parts(download_id)
        path = self._verified(project, parts, what="Download record")
        try:
            raw = migrate_download_dict(json.loads(path.read_text(encoding="utf-8")))
            record = DownloadRecord.model_validate(raw)
        except FileNotFoundError:
            raise DownloadNotFoundError(f"Unknown dataset download {download_id}.") from None
        except ValueError as exc:
            raise ValueError(f"Unreadable download record {download_id}: {exc}.") from exc
        if record.download_id != download_id:
            raise ValueError(f"Download record {download_id} names another transfer.")
        return record

    def _save_record(self, project: Path, record: DownloadRecord) -> None:
        parts = self._record_parts(record.download_id)
        path = self._verified(project, parts, what="Download record")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise UnsafeStorageError("Download record path is blocked.")
        self._makedirs_verified(project, parts[:-1], what="Download record directory")
        atomic_write_json(path, record.model_dump(mode="json"))

    def _iter_records(self, project: Path) -> list[DownloadRecord]:
        records: list[DownloadRecord] = []
        root = project / DOWNLOADS_DIRNAME
        if root.is_symlink():
            raise UnsafeStorageError("The downloads directory must not be a symlink.")
        if not root.is_dir():
            return records
        for child in sorted(root.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                records.append(self._load_record(project, child.name))
            except UnsafeStorageError:
                raise
            except ValueError as exc:
                raise ValueError(f"Unreadable download record {child.name}: {exc}.") from exc
        return records

    # -- public operations ---------------------------------------------

    def start_download(
        self, raw_path: str, provider_name: str, dataset_id: str, snapshot: str
    ) -> DownloadRecord:
        """Authorize, resolve, check space, persist, and launch a download."""
        project = self._authorize(raw_path)
        key = f"{project}::downloads"
        with self._lock(key):
            self._refuse_duplicate_active(project, provider_name, dataset_id, snapshot)
            entry = self._resolve_entry(provider_name, dataset_id, snapshot)
            self._refuse_occupied_final(project, entry)
            self._require_disk_space(project, entry.expected_total_bytes)
            now = utc_now_iso()
            record = DownloadRecord.model_validate(
                {
                    "schema_version": "1.0",
                    "download_id": f"dl-{secrets.token_hex(6)}",
                    "provider": entry.provider,
                    "dataset_id": entry.dataset_id,
                    "snapshot": entry.snapshot,
                    "catalog_identity": entry.catalog_identity,
                    "catalog_entry": entry.model_dump(mode="json"),
                    "expected_total_bytes": entry.expected_total_bytes,
                    "files": [
                        {
                            "path": item.path,
                            "byte_size": item.byte_size,
                            "sha256": item.sha256,
                            "bytes_completed": 0,
                            "verified": False,
                        }
                        for item in entry.expected_files
                    ],
                    "bytes_completed": 0,
                    "state": DownloadState.QUEUED,
                    "attempt": 0,
                    "max_attempts": 3,
                    "created_at": now,
                    "updated_at": now,
                    "failure": None,
                    "lock_identity": None,
                }
            )
            self._save_record(project, record)
            self._spawn_driver(project, record.download_id)
            return record

    def get_download(self, raw_path: str, download_id: str) -> DownloadRecord:
        """Return one transfer record for the authorized project."""
        return self._load_record(self._authorize(raw_path), download_id)

    def list_downloads(self, raw_path: str) -> list[DownloadRecord]:
        """List transfer records for the authorized project."""
        return self._iter_records(self._authorize(raw_path))

    def cancel_download(self, raw_path: str, download_id: str) -> DownloadRecord:
        """Halt a transfer, keeping downloaded bytes for an explicit resume."""
        project = self._authorize(raw_path)
        key = f"{project}::{download_id}"
        with self._lock(key):
            record = self._load_record(project, download_id)
            if record.state in ("failed", "succeeded"):
                raise DownloadConflictError(
                    f"Download {download_id} is {record.state}; it cannot be cancelled."
                )
            self._cancel_event(project, download_id).set()
            if not self._thread_alive(project, download_id):
                self._persist_cancelled(project, record)
                return self._load_record(project, download_id)
            return record

    def resume_download(self, raw_path: str, download_id: str) -> DownloadRecord:
        """Requeue a halted transfer after rechecking space, then drive it."""
        project = self._authorize(raw_path)
        key = f"{project}::{download_id}"
        with self._lock(key):
            record = self._load_record(project, download_id)
            if record.state not in _RESUMABLE_STATES:
                raise DownloadConflictError(
                    f"Download {download_id} is {record.state}; only halted transfers can resume."
                )
            self._require_disk_space(project, record.expected_total_bytes - record.bytes_completed)
            record = record.model_copy(
                update={
                    "state": DownloadState.QUEUED,
                    "failure": None,
                    "updated_at": utc_now_iso(),
                }
            )
            self._save_record(project, record)
            self._spawn_driver(project, record.download_id)
            return record

    def recover_project(self, raw_path: str) -> list[DownloadRecord]:
        """Reconcile transfers after a restart without inventing success.

        ``downloading`` records return to ``queued`` with byte accounting
        re-derived from disk. Any non-terminal record whose destination
        already carries a lock that survives full re-verification is adopted
        as succeeded; a lockless destination that provably holds only this
        transfer's expected files moves back to staging for resume. Drivers
        are never auto-started here: resuming bytes is always explicit.
        """
        project = self._authorize(raw_path)
        reconciled: list[DownloadRecord] = []
        for record in self._iter_records(project):
            if record.state in ("failed", "succeeded"):
                reconciled.append(record)
                continue
            key = f"{project}::{record.download_id}"
            with self._lock(key):
                fresh = self._load_record(project, record.download_id)
                if fresh.state in ("failed", "succeeded"):
                    reconciled.append(fresh)
                    continue
                if fresh.state != DownloadState.DOWNLOADING:
                    # A halted transfer whose destination already verifies
                    # adopts the proven outcome; anything else is untouched.
                    adopted = self._adopt_verified_final(
                        project, fresh, self._final_parts(fresh.catalog_entry)
                    )
                    if adopted is not None:
                        self._persist_succeeded(project, fresh, adopted)
                        reconciled.append(self._load_record(project, fresh.download_id))
                    else:
                        reconciled.append(fresh)
                    continue
                reconciled.append(self._recover_record(project, fresh))
        return reconciled

    # -- internals -----------------------------------------------------

    def _authorize(self, raw_path: str) -> Path:
        candidate = canonicalize_project_path(raw_path)
        return self.projects.require_allowed(candidate.resolve(strict=False))

    def _resolve_entry(self, provider_name: str, dataset_id: str, snapshot: str) -> CatalogEntry:
        provider = self._provider_for(provider_name)
        entry = asyncio.run(provider.resolve_snapshot(dataset_id, snapshot))
        if not isinstance(entry, CatalogEntry):
            raise ProviderMalformed("Dataset source returned an invalid catalog entry.")
        return entry

    def _refuse_occupied_final(self, project: Path, entry: CatalogEntry) -> None:
        parts = self._final_parts(entry)
        final = self._verified(project, parts, what="Dataset destination")
        if not final.exists():
            return
        lock_path = self._verified(project, parts + (DOWNLOAD_LOCK_FILENAME,), what="Dataset lock")
        if lock_path.is_file() and not lock_path.is_symlink():
            raise DownloadConflictError(
                f"Dataset {entry.dataset_id}:{entry.snapshot} is already downloaded "
                "and verified in this project."
            )
        raise DownloadConflictError(
            "The dataset destination already exists without a valid lock; "
            "remove it before downloading."
        )

    def _refuse_duplicate_active(
        self, project: Path, provider_name: str, dataset_id: str, snapshot: str
    ) -> None:
        for record in self._iter_records(project):
            if (
                record.provider == provider_name
                and record.dataset_id == dataset_id
                and record.snapshot == snapshot
                and record.state
                not in (
                    DownloadState.CANCELLED,
                    DownloadState.FAILED,
                    DownloadState.SUCCEEDED,
                )
            ):
                raise DownloadConflictError(
                    f"Dataset {dataset_id}:{snapshot} already has "
                    f"{record.state} transfer {record.download_id}."
                )

    def _require_disk_space(self, project: Path, needed: int) -> None:
        try:
            free = shutil.disk_usage(project).free
        except OSError as exc:
            raise DiskFullError("Unable to measure free disk space.") from exc
        if free < needed + DOWNLOAD_DISK_MARGIN_BYTES:
            raise DiskFullError(
                f"Insufficient disk space: need {needed + DOWNLOAD_DISK_MARGIN_BYTES} bytes "
                f"free (expected bytes plus safety margin), have {free} bytes."
            )

    def _cancel_event(self, project: Path, download_id: str) -> threading.Event:
        with self._guard:
            event = self._cancel.get(f"{project}::{download_id}")
            if event is None:
                event = threading.Event()
                self._cancel[f"{project}::{download_id}"] = event
            return event

    def _thread_alive(self, project: Path, download_id: str) -> bool:
        with self._guard:
            thread = self._threads.get(f"{project}::{download_id}")
            return thread is not None and thread.is_alive()

    def _spawn_driver(self, project: Path, download_id: str) -> None:
        with self._guard:
            key = f"{project}::{download_id}"
            existing = self._threads.get(key)
            if existing is not None and existing.is_alive():
                return
            self._cancel.setdefault(key, threading.Event()).clear()
            worker = threading.Thread(
                target=self._drive, args=(str(project), download_id), daemon=True
            )
            self._threads[key] = worker
            worker.start()

    # -- driver --------------------------------------------------------

    def _drive(self, project_raw: str, download_id: str) -> None:
        project = Path(project_raw)
        key = f"{project}::{download_id}"
        try:
            while True:
                with self._lock(key):
                    record = self._load_record(project, download_id)
                    if record.state in ("failed", "succeeded", "cancelled", "paused"):
                        return
                    if record.attempt >= record.max_attempts:
                        self._persist_failed(project, record, "interrupted", "Attempts exhausted.")
                        return
                    if self._cancel_event(project, download_id).is_set():
                        self._persist_cancelled(project, record)
                        return
                    record = record.model_copy(
                        update={
                            "state": DownloadState.DOWNLOADING,
                            "attempt": record.attempt + 1,
                            "failure": None,
                            "updated_at": utc_now_iso(),
                        }
                    )
                    self._save_record(project, record)
                try:
                    self._run_pass(project, record)
                except CancelledByUser:
                    with self._lock(key):
                        fresh = self._load_record(project, download_id)
                        if fresh.state not in ("failed", "succeeded"):
                            self._persist_cancelled(project, fresh)
                    return
                except _DiskFullSignal:
                    with self._lock(key):
                        fresh = self._load_record(project, download_id)
                        if fresh.state not in ("failed", "succeeded"):
                            self._persist_paused(project, fresh, "disk_full")
                    return
                except _FileFailed as failed:
                    with self._lock(key):
                        fresh = self._load_record(project, download_id)
                        if failed.retryable and fresh.attempt < fresh.max_attempts:
                            continue
                        self._persist_failed(
                            project,
                            fresh,
                            failed.code,
                            _failure_message(
                                failed.code, failed.path, fresh.attempt, fresh.max_attempts
                            ),
                        )
                        return
                with self._lock(key):
                    self._finalize(project, self._load_record(project, download_id))
                    return
        except UnsafeStorageError:
            # No record or byte can be written safely; stop without claiming
            # any outcome. Recovery re-examines the layout on restart.
            return
        except Exception:
            with self._lock(key):
                try:
                    fresh = self._load_record(project, download_id)
                except (ValueError, DownloadNotFoundError, UnsafeStorageError):
                    return
                if fresh.state == DownloadState.DOWNLOADING:
                    try:
                        self._persist_failed(
                            project,
                            fresh,
                            "internal_error",
                            _failure_message(
                                "internal_error", "", fresh.attempt, fresh.max_attempts
                            ),
                        )
                    except UnsafeStorageError:
                        # The record tree itself is untrusted; leave the
                        # transfer untouched for a later safe recovery.
                        return
        finally:
            with self._guard:
                if self._threads.get(key) is threading.current_thread():
                    del self._threads[key]

    def _persist_cancelled(self, project: Path, record: DownloadRecord) -> None:
        # Re-derive byte accounting from disk: the interrupted file's progress
        # was never persisted, but its prefix bytes survived. Files already
        # verified keep their flag when their full bytes are still present.
        partial_parts = self._partial_parts(record.download_id)
        restated: list[DownloadFileState] = []
        for item in record.files:
            fresh = self._restat_file(project, partial_parts, item)
            if item.verified and fresh.bytes_completed == item.byte_size:
                restated.append(item)
            else:
                restated.append(fresh)
        self._save_record(
            project,
            record.with_progress(files=tuple(restated), updated_at=utc_now_iso()).model_copy(
                update={"state": DownloadState.CANCELLED, "failure": None}
            ),
        )

    def _run_pass(self, project: Path, record: DownloadRecord) -> None:
        source = self._source_for(record.provider)
        try:
            self._makedirs_verified(
                project, self._partial_parts(record.download_id), what="Download staging directory"
            )
        except UnsafeStorageError as exc:
            raise _FileFailed("unsafe_storage", retryable=False) from exc
        states: list[DownloadFileState] = list(record.files)
        for index, file_state in enumerate(states):
            if file_state.verified:
                continue
            self._raise_if_cancelled(project, record.download_id)
            try:
                states[index] = self._download_file(project, record, source, file_state)
            except UnsafeStorageError as exc:
                raise _FileFailed("unexpected_symlink", file_state.path, retryable=False) from exc
            except _FileFailed as failed:
                if not failed.path:
                    failed.path = file_state.path
                raise
            with self._lock(f"{project}::{record.download_id}"):
                fresh = self._load_record(project, record.download_id)
                self._save_record(
                    project, fresh.with_progress(files=tuple(states), updated_at=utc_now_iso())
                )

    def _raise_if_cancelled(self, project: Path, download_id: str) -> None:
        if self._cancel_event(project, download_id).is_set():
            raise CancelledByUser(f"Download {download_id} cancelled.")

    def _download_file(
        self,
        project: Path,
        record: DownloadRecord,
        source: FileDownloadSource,
        file_state: DownloadFileState,
    ) -> DownloadFileState:
        parts = self._partial_parts(record.download_id) + _path_parts(file_state.path)
        try:
            self._makedirs_verified(project, parts[:-1], what="Download staging directory")
            target = self._verified(project, parts, what="Download staging file")
        except UnsafeStorageError as exc:
            raise _FileFailed("unexpected_symlink", file_state.path, retryable=False) from exc
        if target.is_dir():
            raise _FileFailed("unexpected_symlink", file_state.path, retryable=False)
        existing = 0
        try:
            info = os.lstat(target)
            if stat.S_ISREG(info.st_mode):
                existing = info.st_size
        except FileNotFoundError:
            existing = 0
        except OSError as exc:
            raise _FileFailed("connection_failed", file_state.path) from exc
        if existing > file_state.byte_size:
            existing = 0
        offset = existing if source.supports_resume and 0 < existing < file_state.byte_size else 0
        downgraded = False
        prefix_retried = False
        while True:
            try:
                digest_hex, received = self._stream_one_file(
                    project, record, source, target, file_state, offset
                )
            except RangeUnsupportedError:
                # The server answered a full stream for a resume offset:
                # restart the file from zero within the same attempt instead
                # of appending mismatched bytes.
                if offset == 0 or downgraded:
                    raise _FileFailed("connection_failed", file_state.path)
                offset = 0
                downgraded = True
                continue
            except _PrefixVanished:
                # The staged prefix changed underneath us: drop it and
                # restart from zero within the same attempt. A repeat means
                # the staging area itself is unstable, so fail instead of
                # looping forever.
                if prefix_retried:
                    raise _FileFailed("connection_failed", file_state.path)
                prefix_retried = True
                target.unlink(missing_ok=True)
                offset = 0
                continue
            if received != file_state.byte_size:
                raise _FileFailed("incomplete_download", file_state.path)
            return self._verify_complete(project, parts, target, file_state, digest_hex)

    def _stream_one_file(
        self,
        project: Path,
        record: DownloadRecord,
        source: FileDownloadSource,
        target: Path,
        file_state: DownloadFileState,
        offset: int,
    ) -> tuple[str, int]:
        """Stream one file to disk; return its digest and total bytes landed."""

        flags = os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        # Read-write: resuming re-reads the surviving prefix for its digest
        # through the same descriptor before appending continues.
        flags |= os.O_RDWR
        flags |= os.O_APPEND if offset else os.O_TRUNC
        try:
            fd = self._open_no_follow(target, flags, what="Download staging file")
        except (_VerificationMismatch, UnsafeStorageError) as exc:
            raise _FileFailed("unexpected_symlink", file_state.path, retryable=False) from exc
        try:
            digest = hashlib.sha256()
            if offset:
                # Hash the surviving prefix incrementally: only an integer
                # byte count is retained, so resume memory stays bounded by
                # one chunk no matter how large the offset grows.
                os.lseek(fd, 0, os.SEEK_SET)
                remaining = offset
                while remaining > 0:
                    try:
                        chunk = os.read(fd, min(VERIFY_CHUNK_BYTES, remaining))
                    except OSError as exc:
                        raise _FileFailed("connection_failed", file_state.path) from exc
                    if not chunk:
                        raise _PrefixVanished(file_state.path)
                    digest.update(chunk)
                    remaining -= len(chunk)
            received = offset
            try:
                stream = source.stream_file(
                    record.dataset_id, record.snapshot, file_state.path, offset
                )
                iterator = iter(stream)
            except RangeUnsupportedError:
                raise
            except (ProviderTimeout, ProviderError, ProviderNotFound, ProviderMalformed) as exc:
                raise self._provider_file_error(exc, file_state.path) from exc
            while True:
                self._raise_if_cancelled(project, record.download_id)
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                except RangeUnsupportedError:
                    raise
                except (
                    ProviderTimeout,
                    ProviderError,
                    ProviderNotFound,
                    ProviderMalformed,
                ) as exc:
                    raise self._provider_file_error(exc, file_state.path) from exc
                if not chunk:
                    continue
                received += len(chunk)
                if received > file_state.byte_size:
                    raise _FileFailed("size_mismatch", file_state.path, retryable=False)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    try:
                        written = os.write(fd, view)
                    except OSError as exc:
                        if exc.errno == errno.ENOSPC:
                            raise _DiskFullSignal() from exc
                        raise _FileFailed("connection_failed", file_state.path) from exc
                    view = view[written:]
            os.fsync(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != received:
                raise _FileFailed("incomplete_download", file_state.path)
            return digest.hexdigest(), received
        finally:
            os.close(fd)

    def _provider_file_error(self, exc: Exception, path: str) -> _FileFailed:
        if isinstance(exc, ProviderTimeout):
            return _FileFailed("timeout", path)
        return _FileFailed("connection_failed", path)

    def _verify_complete(
        self,
        project: Path,
        parts: Sequence[str],
        target: Path,
        file_state: DownloadFileState,
        digest_hex: str,
    ) -> DownloadFileState:
        # Re-prove the path immediately before trusting the landed bytes.
        self._verified(project, parts, what="Download staging file")
        if file_state.sha256 is not None and digest_hex != file_state.sha256:
            target.unlink(missing_ok=True)
            raise _FileFailed("checksum_mismatch", file_state.path)
        return file_state.model_copy(
            update={"bytes_completed": file_state.byte_size, "verified": True}
        )

    # -- finalization ----------------------------------------------------

    def _finalize(self, project: Path, record: DownloadRecord) -> None:
        """Verify, atomically finalize, and publish the lock.

        Any failure after the atomic rename is handled by
        :meth:`_handle_finalize_failure`, so this method never raises and a
        terminal failure never coexists with an unlocked finalized dataset.
        """

        lock_identity: str | None = None
        try:
            entry = record.catalog_entry
            final_parts = self._final_parts(entry)
            adopted = self._adopt_verified_final(project, record, final_parts)
            if adopted is not None:
                self._persist_succeeded(project, record, adopted)
                return
            if self._final_exists(project, final_parts):
                completed = self._complete_unlocked_final(project, record, final_parts)
                if completed is not None:
                    self._persist_succeeded(project, record, completed)
                    return
                self._persist_paused(project, record, "destination_exists")
                return
            partial_parts = self._partial_parts(record.download_id)
            partial = self._verified(project, partial_parts, what="Download staging directory")
            if not partial.is_dir():
                self._persist_failed(
                    project,
                    record,
                    "incomplete_download",
                    _failure_message(
                        "incomplete_download", "", record.attempt, record.max_attempts
                    ),
                )
                return
            self._makedirs_verified(project, final_parts[:-1], what="Dataset destination directory")
            final = self._verified(project, final_parts, what="Dataset destination")
            if final.is_symlink() or final.exists():
                self._persist_paused(project, record, "destination_exists")
                return
            # Re-prove both endpoints immediately before the atomic rename.
            self._verified(project, partial_parts, what="Download staging directory")
            os.rename(partial, final)
            verified = self._hash_final_tree(project, record, final_parts)
            lock = project_lock_from_catalog(
                entry,
                retrieved_at=utc_now_iso(),
                local_path="/".join(final_parts),
                expected_files=[item.model_dump(mode="json") for item in verified],
            )
            self._publish_lock(project, final_parts, lock)
            lock_identity = lock.dataset_identity
            self._persist_succeeded(project, record, lock_identity)
        except Exception:
            self._handle_finalize_failure(project, record, lock_identity)

    def _final_exists(self, project: Path, final_parts: Sequence[str]) -> bool:
        try:
            return self._verified(project, final_parts, what="Dataset destination").exists()
        except UnsafeStorageError:
            return False

    def _read_lock(self, project: Path, final_parts: Sequence[str]) -> DatasetLock | None:
        try:
            lock_path = self._verified(
                project, tuple(final_parts) + (DOWNLOAD_LOCK_FILENAME,), what="Dataset lock"
            )
        except UnsafeStorageError:
            return None
        if lock_path.is_symlink() or not lock_path.is_file():
            return None
        try:
            with lock_path.open("rb") as handle:
                return DatasetLock.model_validate_json(handle.read())
        except (OSError, ValueError):
            return None

    def _expected_file_map(self, record: DownloadRecord) -> dict[str, int]:
        return {item.path: item.byte_size for item in record.files}

    def _hash_final_tree(
        self, project: Path, record: DownloadRecord, final_parts: Sequence[str]
    ) -> list[VerifiedFile]:
        """Hash every expected file in a finalized tree; refuse anything else."""

        scanned = self._scan_regular_files(project, final_parts, what="Dataset destination")
        expected = self._expected_file_map(record)
        if set(scanned) != set(expected):
            raise _VerificationMismatch(
                "Dataset destination does not hold exactly the expected files."
            )
        verified: list[VerifiedFile] = []
        for item in record.files:
            size = scanned[item.path]
            if size != item.byte_size:
                raise _VerificationMismatch(f"Dataset file {item.path!r} has an unexpected size.")
            digest = self._hash_regular(
                self._verified(
                    project, tuple(final_parts) + _path_parts(item.path), what="Dataset file"
                ),
                item.byte_size,
                what=f"Dataset file {item.path!r}",
            )
            if item.sha256 is not None and digest != item.sha256:
                raise _VerificationMismatch(f"Dataset file {item.path!r} failed its checksum.")
            verified.append(VerifiedFile(path=item.path, byte_size=size, sha256=digest))
        return verified

    def _publish_lock(self, project: Path, final_parts: Sequence[str], lock: DatasetLock) -> None:
        parts = tuple(final_parts) + (DOWNLOAD_LOCK_FILENAME,)
        lock_path = self._verified(project, parts, what="Dataset lock")
        if lock_path.is_symlink() or lock_path.exists():
            raise UnsafeStorageError("Dataset lock path is blocked.")
        self._verified(project, final_parts, what="Dataset destination")
        atomic_write_json(lock_path, lock.model_dump(mode="json"))

    def _adopt_verified_final(
        self, project: Path, record: DownloadRecord, final_parts: Sequence[str]
    ) -> str | None:
        """Return the lock identity when the finalized tree verifies fully."""

        lock = self._read_lock(project, final_parts)
        if lock is None or lock.catalog_identity != record.catalog_identity:
            return None
        expected = self._expected_file_map(record)
        if {item.path: item.byte_size for item in lock.expected_files} != expected:
            return None
        try:
            scanned = self._scan_regular_files(project, final_parts, what="Dataset destination")
        except (UnsafeStorageError, _VerificationMismatch):
            return None
        if set(scanned) - {DOWNLOAD_LOCK_FILENAME} != set(expected):
            return None
        for item in lock.expected_files:
            if scanned.get(item.path) != item.byte_size:
                return None
            try:
                digest = self._hash_regular(
                    self._verified(
                        project,
                        tuple(final_parts) + _path_parts(item.path),
                        what="Dataset file",
                    ),
                    item.byte_size,
                    what=f"Dataset file {item.path!r}",
                )
            except (UnsafeStorageError, _VerificationMismatch):
                return None
            if digest != item.sha256:
                return None
        self._remove_partial(project, record.download_id)
        return lock.dataset_identity

    def _complete_unlocked_final(
        self, project: Path, record: DownloadRecord, final_parts: Sequence[str]
    ) -> str | None:
        """Finish an interrupted finalize when the tree provably holds our bytes."""

        if not self._final_is_ours(project, record, final_parts, expect_lock=False):
            return None
        try:
            verified = self._hash_final_tree(project, record, final_parts)
            lock = project_lock_from_catalog(
                record.catalog_entry,
                retrieved_at=utc_now_iso(),
                local_path="/".join(final_parts),
                expected_files=[item.model_dump(mode="json") for item in verified],
            )
            self._publish_lock(project, final_parts, lock)
        except (UnsafeStorageError, _VerificationMismatch, OSError, ValueError):
            return None
        return lock.dataset_identity

    def _final_is_ours(
        self,
        project: Path,
        record: DownloadRecord,
        final_parts: Sequence[str],
        *,
        expect_lock: bool,
    ) -> bool:
        """True when the destination holds exactly this transfer's files."""

        try:
            scanned = self._scan_regular_files(project, final_parts, what="Dataset destination")
        except (UnsafeStorageError, _VerificationMismatch):
            return False
        expected = self._expected_file_map(record)
        if expect_lock:
            expected = dict(expected)
            expected[DOWNLOAD_LOCK_FILENAME] = -1
        if set(scanned) != set(expected):
            return False
        return all(scanned[path] == size for path, size in expected.items() if size >= 0)

    def _handle_finalize_failure(
        self, project: Path, record: DownloadRecord, lock_identity: str | None
    ) -> None:
        """Recover the post-rename window deterministically.

        With a published lock the tree is proven complete, so the record
        simply retries its succeeded write (recovery adopts it otherwise).
        Without one, the owned tree returns to its unique staging location
        and the transfer parks as resumable with flags reset, so a later
        resume re-verifies from disk instead of trusting stale flags. An
        unprovable destination is never deleted or replaced.
        """

        final_parts = self._final_parts(record.catalog_entry)
        try:
            if lock_identity is not None:
                self._persist_succeeded(project, record, lock_identity)
                return
            self._reclaim_final_to_partial(project, record, final_parts)
            states = self._restat_partial(project, record)
            restated = record.model_copy(
                update={
                    "files": states,
                    "bytes_completed": sum(item.bytes_completed for item in states),
                    "updated_at": utc_now_iso(),
                }
            )
            self._persist_paused(project, restated, "finalize_failed")
        except Exception:
            # Nothing safe remains to write: leave the bytes untouched. A
            # later recovery re-examines the layout without inventing success.
            return

    def _reclaim_final_to_partial(
        self, project: Path, record: DownloadRecord, final_parts: Sequence[str]
    ) -> bool:
        """Move a provably-owned lockless tree back to its staging location.

        Returns False (leaving everything untouched) when the destination
        cannot be proven to hold exactly this transfer's files.
        """

        if not self._final_is_ours(project, record, final_parts, expect_lock=False):
            return False
        partial_parts = self._partial_parts(record.download_id)
        try:
            final = self._verified(project, final_parts, what="Dataset destination")
            self._makedirs_verified(project, partial_parts[:-1], what="Download staging directory")
            partial = self._verified(project, partial_parts, what="Download staging directory")
            if partial.exists():
                return False
            os.rename(final, partial)
        except (OSError, UnsafeStorageError, ValueError):
            return False
        return True

    def _restat_partial(
        self, project: Path, record: DownloadRecord
    ) -> tuple[DownloadFileState, ...]:
        partial_parts = self._partial_parts(record.download_id)
        return tuple(self._restat_file(project, partial_parts, item) for item in record.files)

    def _persist_succeeded(self, project: Path, record: DownloadRecord, lock_identity: str) -> None:
        states = tuple(
            item.model_copy(update={"bytes_completed": item.byte_size, "verified": True})
            for item in record.files
        )
        self._save_record(
            project,
            record.model_copy(
                update={
                    "files": states,
                    "bytes_completed": sum(item.bytes_completed for item in states),
                    "state": DownloadState.SUCCEEDED,
                    "failure": None,
                    "lock_identity": lock_identity,
                    "updated_at": utc_now_iso(),
                }
            ),
        )

    def _persist_failed(
        self, project: Path, record: DownloadRecord, code: str, message: str
    ) -> None:
        # Failed transfers are terminal, so their staged bytes are removed
        # through the guarded boundary only; nothing outside the project is
        # ever reached. Cancelled/paused transfers keep bytes for resume.
        self._remove_partial(project, record.download_id)
        self._save_record(
            project,
            record.model_copy(
                update={
                    "state": DownloadState.FAILED,
                    "failure": DownloadFailure(code=code, message=message),
                    "updated_at": utc_now_iso(),
                }
            ),
        )

    def _persist_paused(self, project: Path, record: DownloadRecord, code: str) -> None:
        self._save_record(
            project,
            record.model_copy(
                update={
                    "state": DownloadState.PAUSED,
                    "failure": DownloadFailure(
                        code=code,
                        message=_failure_message(code, "", record.attempt, record.max_attempts),
                    ),
                    "updated_at": utc_now_iso(),
                }
            ),
        )

    def _remove_partial(self, project: Path, download_id: str) -> None:
        parts = self._partial_parts(download_id)
        try:
            partial = self._verified(project, parts, what="Download staging directory")
        except (UnsafeStorageError, ValueError):
            return
        if partial.is_symlink() or not partial.is_dir():
            return
        shutil.rmtree(partial, ignore_errors=True)

    # -- recovery --------------------------------------------------------

    def _recover_record(self, project: Path, record: DownloadRecord) -> DownloadRecord:
        final_parts = self._final_parts(record.catalog_entry)
        adopted = self._adopt_verified_final(project, record, final_parts)
        if adopted is not None:
            self._persist_succeeded(project, record, adopted)
            return self._load_record(project, record.download_id)
        if self._final_exists(project, final_parts):
            if self._complete_unlocked_final(project, record, final_parts) is not None:
                lock = self._read_lock(project, final_parts)
                if lock is not None:
                    self._persist_succeeded(project, record, lock.dataset_identity)
                    return self._load_record(project, record.download_id)
            if self._reclaim_final_to_partial(project, record, final_parts):
                # Our tree without a usable lock comes home for resume; flags
                # reset so the pass re-verifies from disk instead of trusting
                # stale claims.
                states = self._restat_partial(project, record)
                recovered = record.model_copy(
                    update={
                        "files": states,
                        "bytes_completed": sum(item.bytes_completed for item in states),
                        "state": DownloadState.QUEUED,
                        "failure": None,
                        "lock_identity": None,
                        "updated_at": utc_now_iso(),
                    }
                )
                self._save_record(project, recovered)
                return recovered
            # A destination we cannot prove ours is never deleted or replaced.
            self._persist_paused(project, record, "destination_exists")
            return self._load_record(project, record.download_id)
        partial_parts = self._partial_parts(record.download_id)
        states = tuple(self._restat_file(project, partial_parts, item) for item in record.files)
        recovered = record.model_copy(
            update={
                "files": states,
                "bytes_completed": sum(item.bytes_completed for item in states),
                "state": DownloadState.QUEUED,
                "failure": None,
                "lock_identity": None,
                "updated_at": utc_now_iso(),
            }
        )
        self._save_record(project, recovered)
        return recovered

    def _restat_file(
        self, project: Path, partial_parts: Sequence[str], item: DownloadFileState
    ) -> DownloadFileState:
        try:
            target = self._verified(
                project, tuple(partial_parts) + _path_parts(item.path), what="Download staging file"
            )
            info = os.lstat(target)
        except (UnsafeStorageError, ValueError, OSError):
            return item.model_copy(update={"bytes_completed": 0, "verified": False})
        if not stat.S_ISREG(info.st_mode):
            return item.model_copy(update={"bytes_completed": 0, "verified": False})
        return item.model_copy(
            update={"bytes_completed": min(info.st_size, item.byte_size), "verified": False}
        )
