"""Local/private offline dataset import (no upload, no mutation of source data).

A researcher selects a directory that already lives inside the active
authorized project.  This service scans every regular file through the same
no-follow storage boundary used by DownloadService, hashes each file in
bounded chunks, and produces an immutable DatasetLock.  Source data is never
uploaded, relocated, or mutated.

Identity is derived deterministically from the canonical verified file list,
total bytes, and explicit user-declared metadata (title, limitations,
formats, citations), excluding local path and scan timestamp.

Safety properties:

- Source directories cannot overlap BrainLearn-owned mutable storage or metadata
  (local-imports, downloads, datasets, runs, staging, cache, project.json, etc.).
- The import directory must be a subdirectory inside the project, not the project root.
- Every path crosses the storage boundary (_verified) before any open/read.
- Symlinks, hard links (st_nlink > 1), special files (FIFOs, devices, sockets),
  unreadable entries, and non-regular objects are refused anywhere in the walk.
- Every relative path is validated with the shared portable-path grammar.
- Depth, file-count, and total-byte bounds prevent runaway scans.
- Descriptor identity and metadata (dev, ino, size, mtime) are recorded and
  verified before and after chunked hashing to detect same-size mutation and replacement.
- Cancellation is checked cooperatively between files and inside the chunked hash loop.
- The complete tree is rescanned and verified after hashing to detect concurrent mutation.
- All failure messages are static strings; no server paths, exception text, or
  server internals are ever persisted or returned.
- Import records are stored under local-imports/<import_id>/import.json
  inside the project, written atomically.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from brainlearn_core import (
    LOCAL_PROVIDER,
    DatasetCitation,
    DatasetLock,
    LocalImportFailure,
    LocalImportRecord,
    VerifiedFile,
    dataset_lock_identity,
    local_import_lock,
    migrate_local_import_dict,
    validate_relative_path,
)
from brainlearn_core.projects import utc_now_iso

from brainlearn_server.downloads import (
    MAX_SCAN_DEPTH,
    MAX_SCAN_FILES,
    VERIFY_CHUNK_BYTES,
    CancelledByUser,
    UnsafeStorageError,
    _VerificationMismatch,
)
from brainlearn_server.project_store import (
    ProjectStore,
    atomic_write_json,
    canonicalize_project_path,
)

LOCAL_IMPORT_SCHEMA_VERSION: Literal["1.0"] = "1.0"
LOCAL_IMPORT_ID_PATTERN = re.compile(r"^li-[0-9a-f]{12}$")
LOCAL_IMPORTS_DIRNAME = "local-imports"
LOCAL_IMPORT_FILENAME = "import.json"

# Maximum total bytes that may be hashed in one import (10 GiB).
MAX_IMPORT_BYTES = 10 * 1024 * 1024 * 1024

# Reserved project storage and metadata directories/files that must never be chosen as source data.
RESERVED_PROJECT_DIRS = frozenset(
    {"local-imports", "downloads", "datasets", "runs", "staging", "cache", ".brainlearn"}
)
RESERVED_PROJECT_FILES = frozenset(
    {"project.json", "workflow.json", "previous_workflow.json", "recent.json"}
)


class LocalImportNotFoundError(FileNotFoundError):
    """No such local import record exists."""


class LocalImportConflictError(ValueError):
    """A local import cannot start or transition in its current state."""


def _record_parts(import_id: str) -> tuple[str, ...]:
    if LOCAL_IMPORT_ID_PATTERN.match(import_id) is None:
        raise ValueError(f"Invalid local import id {import_id!r}.")
    return (LOCAL_IMPORTS_DIRNAME, import_id, LOCAL_IMPORT_FILENAME)


# ── service ───────────────────────────────────────────────────────────────────


@dataclass
class LocalImportService:
    """Owns local import records, scan threads, and recovery for projects."""

    projects: ProjectStore
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _threads: dict[str, threading.Thread] = field(default_factory=dict)
    _cancel: dict[str, threading.Event] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    # -- storage boundary (mirrors DownloadService) ----------------------------

    def _verified(self, project: Path, parts: tuple[str, ...], *, what: str) -> Path:
        """Prove one project-relative path is contained and symlink-free."""

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

    def _makedirs_verified(self, project: Path, parts: tuple[str, ...], *, what: str) -> Path:
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

    def _open_no_follow(self, path: Path, flags: int, *, what: str) -> int:
        try:
            return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            raise _VerificationMismatch(f"{what} is no longer present.") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise UnsafeStorageError(f"{what} must not be a symlink.") from None
            raise _VerificationMismatch(f"{what} could not be opened.") from exc

    def _hash_regular(
        self,
        path: Path,
        expected_size: int,
        expected_dev: int,
        expected_ino: int,
        expected_mtime_ns: int,
        expected_ctime_ns: int,
        cancel_event: threading.Event,
        *,
        what: str,
    ) -> str:
        """Hash one regular file through a no-follow descriptor, checking descriptor state."""

        fd = self._open_no_follow(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0), what=what)
        try:
            info_before = os.fstat(fd)
            if not stat.S_ISREG(info_before.st_mode):
                raise _VerificationMismatch(f"{what} is not a regular file.")
            if info_before.st_nlink > 1:
                raise _VerificationMismatch(f"{what} contains a hard link.")
            if info_before.st_size != expected_size:
                raise _VerificationMismatch(f"{what} does not have its expected size.")
            if info_before.st_dev != expected_dev or info_before.st_ino != expected_ino:
                raise _VerificationMismatch(f"{what} changed inode before hashing.")
            if (
                info_before.st_mtime_ns != expected_mtime_ns
                or info_before.st_ctime_ns != expected_ctime_ns
            ):
                raise _VerificationMismatch(f"{what} changed timestamps before hashing.")

            digest = hashlib.sha256()
            remaining = expected_size
            while remaining > 0:
                if cancel_event.is_set():
                    raise CancelledByUser("Local import cancelled.")
                try:
                    chunk = os.read(fd, min(VERIFY_CHUNK_BYTES, remaining))
                except OSError as exc:
                    raise _VerificationMismatch(f"{what} could not be read.") from exc
                if not chunk:
                    raise _VerificationMismatch(f"{what} ended before its expected size.")
                digest.update(chunk)
                remaining -= len(chunk)

            # Check for trailing bytes.
            if os.read(fd, 1):
                raise _VerificationMismatch(f"{what} grew past its expected size.")

            # Stat descriptor again after reading to detect in-place mutation.
            info_after = os.fstat(fd)
            if (
                info_after.st_dev != info_before.st_dev
                or info_after.st_ino != info_before.st_ino
                or info_after.st_size != info_before.st_size
                or info_after.st_mtime_ns != info_before.st_mtime_ns
                or info_after.st_ctime_ns != info_before.st_ctime_ns
            ):
                raise _VerificationMismatch(f"{what} mutated during hashing.")

            # Stat path on filesystem to detect unlinking and replacement.
            info_path = os.lstat(path)
            if (
                info_path.st_dev != info_before.st_dev
                or info_path.st_ino != info_before.st_ino
                or info_path.st_size != info_before.st_size
                or info_path.st_mtime_ns != info_before.st_mtime_ns
                or info_path.st_ctime_ns != info_before.st_ctime_ns
            ):
                raise _VerificationMismatch(f"{what} was replaced during hashing.")

            return digest.hexdigest()
        finally:
            os.close(fd)

    def _scan_regular_files(
        self, project: Path, dir_parts: tuple[str, ...], *, what: str
    ) -> dict[str, tuple[int, int, int, int, int]]:
        """Map every regular file under a verified tree to (size, dev, ino, mtime_ns, ctime_ns).

        Refuses symlinks, hard links (st_nlink > 1), devices, FIFOs, sockets,
        unreadable files, and non-regular objects anywhere in the walk.
        Stays strictly bounded in depth, file count, and total bytes.
        """

        root = self._verified(project, dir_parts, what=what)
        if not root.is_dir():
            raise _VerificationMismatch(f"{what} is not a directory.")
        found: dict[str, tuple[int, int, int, int, int]] = {}
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            depth = len(Path(current).relative_to(root).parts)
            if depth > MAX_SCAN_DEPTH:
                raise _VerificationMismatch(f"{what} is nested too deep to scan.")
            base = Path(current)
            for name in list(dirnames):
                child_dir = base / name
                if child_dir.is_symlink():
                    raise UnsafeStorageError(f"{what} contains a symlinked directory.")
            for name in filenames:
                child = base / name
                if child.is_symlink():
                    raise UnsafeStorageError(f"{what} contains a symlink.")
                try:
                    info = child.lstat()
                except OSError as exc:
                    raise _VerificationMismatch(f"{what} contains an unreadable file.") from exc
                if not stat.S_ISREG(info.st_mode):
                    raise _VerificationMismatch(f"{what} contains a non-regular file.")
                if info.st_nlink > 1:
                    raise _VerificationMismatch(f"{what} contains a hard link.")
                rel = str(child.relative_to(root)).replace(os.sep, "/")
                validate_relative_path(rel, "Import file path")
                if len(found) >= MAX_SCAN_FILES:
                    raise _VerificationMismatch(f"{what} holds too many files to import.")
                found[rel] = (
                    info.st_size,
                    info.st_dev,
                    info.st_ino,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )

        total_bytes = sum(meta[0] for meta in found.values())
        if total_bytes > MAX_IMPORT_BYTES:
            raise _VerificationMismatch(
                f"{what} exceeds the maximum import size of {MAX_IMPORT_BYTES} bytes."
            )
        return found

    # -- locking ---------------------------------------------------------------

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _cancel_event(self, project: Path, import_id: str) -> threading.Event:
        with self._guard:
            key = f"{project}::{import_id}"
            event = self._cancel.get(key)
            if event is None:
                event = threading.Event()
                self._cancel[key] = event
            return event

    def _thread_alive(self, project: Path, import_id: str) -> bool:
        with self._guard:
            key = f"{project}::{import_id}"
            thread = self._threads.get(key)
            return thread is not None and thread.is_alive()

    # -- records ---------------------------------------------------------------

    def _load_record(self, project: Path, import_id: str) -> LocalImportRecord:
        parts = _record_parts(import_id)
        path = self._verified(project, parts, what="Local import record")
        try:
            raw = migrate_local_import_dict(json.loads(path.read_text(encoding="utf-8")))
            record = LocalImportRecord.model_validate(raw)
        except FileNotFoundError:
            raise LocalImportNotFoundError(f"Unknown local import {import_id}.") from None
        except ValueError as exc:
            raise ValueError(f"Unreadable local import record {import_id}: {exc}.") from exc
        if record.import_id != import_id:
            raise ValueError(f"Local import record {import_id} names another import.")
        if record.lock is not None:
            recomputed = dataset_lock_identity(
                provider=record.lock.provider,
                dataset_id=record.lock.dataset_id,
                snapshot=record.lock.snapshot,
                access=record.lock.access.value,
                title=record.lock.title,
                modality=record.lock.modality,
                task=record.lock.task,
                participants=record.lock.participants,
                formats=list(record.lock.formats),
                citations=[item.model_dump(mode="json") for item in record.lock.citations],
                compatible_templates=list(record.lock.compatible_templates),
                landing_page=record.lock.landing_page,
                limitations=record.lock.limitations,
                expected_total_bytes=record.lock.expected_total_bytes,
                expected_files=[
                    item.model_dump(mode="json") for item in record.lock.expected_files
                ],
                license_name=record.lock.license_name,
                license_spdx=record.lock.license_spdx,
                reuse_statement=record.lock.reuse_statement,
            )
            if recomputed != record.lock.dataset_identity:
                raise ValueError(
                    f"Local import record {import_id} lock identity does not match contents."
                )
        return record

    def _save_record(self, project: Path, record: LocalImportRecord) -> None:
        parts = _record_parts(record.import_id)
        path = self._verified(project, parts, what="Local import record")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise UnsafeStorageError("Local import record path is blocked.")
        self._makedirs_verified(project, parts[:-1], what="Local import record directory")
        atomic_write_json(path, record.model_dump(mode="json"))

    def _iter_records(self, project: Path) -> list[LocalImportRecord]:
        records: list[LocalImportRecord] = []
        root = project / LOCAL_IMPORTS_DIRNAME
        if root.is_symlink():
            raise UnsafeStorageError("The local-imports directory must not be a symlink.")
        if not root.is_dir():
            return records
        for child in sorted(root.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            if LOCAL_IMPORT_ID_PATTERN.match(child.name) is None:
                continue
            try:
                records.append(self._load_record(project, child.name))
            except UnsafeStorageError:
                raise
            except ValueError as exc:
                raise ValueError(f"Unreadable local import record {child.name}: {exc}.") from exc
        return records

    # -- authorization ---------------------------------------------------------

    def _authorize(self, raw_path: str) -> Path:
        candidate = canonicalize_project_path(raw_path)
        return self.projects.require_allowed(candidate.resolve(strict=False))

    def _authorize_source_dir(self, project: Path, relative_dir: str) -> tuple[str, ...]:
        """Validate and split a researcher-supplied relative directory path.

        The path must be project-relative (no leading slash, no drive, no
        ``..`` segments) and must not escape the project root. It must not
        target the project root itself or any BrainLearn-owned storage.
        """

        if not relative_dir or not relative_dir.strip():
            raise ValueError("The import path must be a non-empty relative directory path.")
        normalized = relative_dir.strip().replace("\\", "/")
        if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
            raise ValueError("The import path must be relative to the project root, not absolute.")
        if normalized == ".":
            raise ValueError(
                "The import path must target a subdirectory inside the project, "
                "not the project root."
            )
        # Validate against portable relative path grammar.
        validate_relative_path(normalized, "The import path")
        parts = tuple(normalized.split("/"))
        for part in parts:
            if not part or part in (".", "..") or "\x00" in part:
                raise ValueError("The import path must not contain empty, '.', or '..' segments.")
        # Refuse directories overlapping BrainLearn-owned mutable storage or metadata.
        first_segment = parts[0].lower()
        if first_segment in RESERVED_PROJECT_DIRS or first_segment in RESERVED_PROJECT_FILES:
            raise ValueError(
                "The import path must not target BrainLearn-owned storage or metadata."
            )
        # Verify the path is inside the project and is an existing directory.
        root = self._verified(project, parts, what="Import source directory")
        if not root.is_dir():
            raise ValueError("The import path must target an existing directory.")
        return parts

    # -- scan and hash ---------------------------------------------------------

    def _scan_and_hash(
        self,
        project: Path,
        dir_parts: tuple[str, ...],
        import_id: str,
    ) -> list[VerifiedFile]:
        """Scan a directory, hash every regular file, return VerifiedFile list.

        Raises _VerificationMismatch if any file is added, removed, or mutated
        between the scan pass and the hash pass, or during hashing.
        """

        what = "Import source directory"
        cancel_event = self._cancel_event(project, import_id)

        # Phase 1: scan — enumerate all regular files and their sizes/descriptors.
        scanned = self._scan_regular_files(project, dir_parts, what=what)

        verified: list[VerifiedFile] = []

        # Phase 2: hash — open each file through the no-follow boundary.
        for rel_path, (
            expected_size,
            expected_dev,
            expected_ino,
            expected_mtime_ns,
            expected_ctime_ns,
        ) in sorted(scanned.items()):
            if cancel_event.is_set():
                raise CancelledByUser(f"Local import {import_id} cancelled.")

            # Re-verify the file path before opening.
            file_parts = dir_parts + tuple(rel_path.replace("\\", "/").split("/"))
            file_path = self._verified(project, file_parts, what=f"Import file {rel_path!r}")

            digest = self._hash_regular(
                file_path,
                expected_size,
                expected_dev,
                expected_ino,
                expected_mtime_ns,
                expected_ctime_ns,
                cancel_event,
                what=f"Import file {rel_path!r}",
            )
            verified.append(VerifiedFile(path=rel_path, byte_size=expected_size, sha256=digest))

        # Phase 3: complete tree revalidation — detect concurrent mutation.
        rescanned = self._scan_regular_files(project, dir_parts, what=what)
        if rescanned != scanned:
            raise _VerificationMismatch("The import directory changed during scanning.")

        return verified

    # -- driver ----------------------------------------------------------------

    def _drive(
        self,
        project_raw: str,
        import_id: str,
        dir_parts: tuple[str, ...],
        title: str,
        limitations: str,
        citations: list[dict[str, Any]],
        formats: list[str],
        dataset_id: str,
        local_path: str,
    ) -> None:
        """Worker thread: scan, hash, build lock, persist ready record."""

        project = Path(project_raw)
        key = f"{project}::{import_id}"
        lock_instance: DatasetLock | None = None
        failure: LocalImportFailure | None = None
        state: Literal["ready", "failed", "cancelled"] = "ready"

        try:
            verified = self._scan_and_hash(project, dir_parts, import_id)
            lock_instance = local_import_lock(
                dataset_id=dataset_id,
                title=title,
                limitations=limitations,
                citations=citations,
                formats=formats,
                retrieved_at=utc_now_iso(),
                local_path=local_path,
                verified_files=verified,
            )
        except CancelledByUser:
            state = "cancelled"
            failure = LocalImportFailure(
                code="cancelled",
                message="The local import was cancelled.",
            )
        except UnsafeStorageError:
            state = "failed"
            failure = LocalImportFailure(
                code="unsafe_storage",
                message="The import storage layout is blocked by a symlink or non-directory.",
            )
        except _VerificationMismatch:
            state = "failed"
            failure = LocalImportFailure(
                code="verification_failed",
                message="The import directory contents could not be verified safely.",
            )
        except PermissionError:
            state = "failed"
            failure = LocalImportFailure(
                code="permission_denied",
                message="The import directory contains unreadable files.",
            )
        except Exception:
            state = "failed"
            failure = LocalImportFailure(
                code="import_failed",
                message="The local import could not be completed.",
            )

        with self._lock(key):
            try:
                current = self._load_record(project, import_id)
            except Exception:
                current = None
            if current is not None and current.state == "cancelled":
                return
            updated = LocalImportRecord(
                schema_version="1.0",
                import_id=import_id,
                state=state,
                local_path=local_path,
                lock=lock_instance,
                failure=failure,
                created_at=current.created_at if current is not None else utc_now_iso(),
                updated_at=utc_now_iso(),
            )
            try:
                self._save_record(project, updated)
            except Exception:
                pass
            finally:
                with self._guard:
                    self._threads.pop(key, None)
                    self._cancel.pop(key, None)

    # -- public API ------------------------------------------------------------

    def import_local_dataset(
        self,
        raw_path: str,
        relative_dir: str,
        title: str,
        limitations: str = "",
        citations: list[dict[str, Any]] | None = None,
        formats: list[str] | None = None,
    ) -> LocalImportRecord:
        """Start an import scan for a local/private dataset directory."""

        project = self._authorize(raw_path)
        dir_parts = self._authorize_source_dir(project, relative_dir)
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Dataset title must not be blank.")
        clean_limitations = limitations.strip()
        clean_citations = citations or []
        clean_formats = formats or []

        # Validate citation models early if supplied.
        for item in clean_citations:
            DatasetCitation.model_validate(item)

        # Local provider datasets use constant LOCAL_PROVIDER dataset_id so identity
        # is independent of local filesystem naming and directory locations.
        dataset_id = LOCAL_PROVIDER

        import_id = f"li-{secrets.token_hex(6)}"
        key = f"{project}::{import_id}"
        local_path = "/".join(dir_parts)
        now = utc_now_iso()

        initial_record = LocalImportRecord(
            schema_version="1.0",
            import_id=import_id,
            state="scanning",
            local_path=local_path,
            lock=None,
            failure=None,
            created_at=now,
            updated_at=now,
        )

        with self._lock(key):
            self._save_record(project, initial_record)

        thread = threading.Thread(
            target=self._drive,
            args=(
                str(project),
                import_id,
                dir_parts,
                clean_title,
                clean_limitations,
                clean_citations,
                clean_formats,
                dataset_id,
                local_path,
            ),
            daemon=True,
            name=f"local-import-{import_id}",
        )
        with self._guard:
            self._threads[key] = thread
            self._cancel[key] = threading.Event()
        thread.start()

        return initial_record

    def get_import(self, raw_path: str, import_id: str) -> LocalImportRecord:
        """Return one local import record for the authorized project."""

        project = self._authorize(raw_path)
        return self._load_record(project, import_id)

    def list_imports(self, raw_path: str) -> list[LocalImportRecord]:
        """List local import records for the authorized project."""

        project = self._authorize(raw_path)
        return self._iter_records(project)

    def cancel_import(self, raw_path: str, import_id: str) -> LocalImportRecord:
        """Signal a running local import scan to stop."""

        project = self._authorize(raw_path)
        key = f"{project}::{import_id}"
        with self._lock(key):
            record = self._load_record(project, import_id)
            if record.state == "cancelled":
                return record
            if record.state in ("ready", "failed"):
                raise LocalImportConflictError(
                    f"Local import {import_id} is already {record.state}."
                )

            # Signal cancellation to the scanning thread.
            self._cancel_event(project, import_id).set()

            # Wait briefly for the worker thread to exit cleanly.
            with self._guard:
                thread = self._threads.get(key)
            if thread is not None:
                thread.join(timeout=0.2)

            # Check fresh record state; if still scanning, mark cancelled directly.
            fresh = self._load_record(project, import_id)
            if fresh.state == "scanning":
                cancelled_record = fresh.model_copy(
                    update={
                        "state": "cancelled",
                        "failure": LocalImportFailure(
                            code="cancelled",
                            message="The local import was cancelled.",
                        ),
                        "updated_at": utc_now_iso(),
                    }
                )
                self._save_record(project, cancelled_record)
                return cancelled_record
            return fresh

    def recover_project(self, raw_path: str) -> list[LocalImportRecord]:
        """Reconcile import records after a restart.

        Scanning records whose thread is no longer alive are marked failed
        (the scan did not complete). Ready and terminal records are verified
        and returned.
        """

        project = self._authorize(raw_path)
        reconciled: list[LocalImportRecord] = []
        for record in self._iter_records(project):
            import_id = record.import_id
            key = f"{project}::{import_id}"
            with self._lock(key):
                fresh = self._load_record(project, import_id)
                if fresh.state == "scanning" and not self._thread_alive(project, import_id):
                    repaired = fresh.model_copy(
                        update={
                            "state": "failed",
                            "failure": LocalImportFailure(
                                code="interrupted",
                                message="The service stopped before the import finished.",
                            ),
                            "updated_at": utc_now_iso(),
                        }
                    )
                    try:
                        self._save_record(project, repaired)
                    except UnsafeStorageError:
                        pass
                    reconciled.append(repaired)
                else:
                    reconciled.append(fresh)
        return reconciled
