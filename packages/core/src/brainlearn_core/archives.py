"""Bounded, hardened archive extraction (no networking, no engine).

Only the ZIP and tar containers a dataset provider can legitimately serve
are supported (``.zip``, ``.tar``, ``.tar.gz``/``.tgz``), detected by magic
bytes rather than file suffix. Every member path crosses the same
conservative portable grammar as catalog files; absolute paths, parent
traversal, duplicates, directory/file conflicts, links, and special files
are rejected before or as they appear. Declared limits are enforced before
any byte is written (pre-scan) and re-enforced live while streaming, so
lying headers cannot escape the budget. Extraction streams member by member
with bounded memory, honors cancellation, fsyncs landed files, and never
follows a symlink at any step.
"""

from __future__ import annotations

import errno
import os
import stat
import tarfile
import threading
import zipfile
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from brainlearn_core.datasets import validate_relative_path

ARCHIVE_MAGIC_ZIP = b"PK\x03\x04"
ARCHIVE_MAGIC_ZIP_EMPTY = b"PK\x05\x06"
ARCHIVE_MAGIC_ZIP_SPANNED = b"PK\x07\x08"
ARCHIVE_MAGIC_GZIP = b"\x1f\x8b"
ARCHIVE_TAR_MAGIC_OFFSET = 257
ARCHIVE_TAR_MAGIC = b"ustar"
VERIFY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Declared bounds enforced before and during extraction."""

    max_members: int = 100_000
    max_file_bytes: int = 8 * 1024 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024 * 1024
    max_ratio: float = 100.0
    max_depth: int = 32
    max_name_length: int = 1024
    max_component_length: int = 255


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


class ArchiveRejectedError(ValueError):
    """An archive or member failed validation; carries a stable code."""

    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.retryable = retryable


class ArchiveCancelled(Exception):
    """Cancellation observed during extraction; partial members remain."""


def detect_archive_format(head: bytes) -> str:
    """Identify ``zip``, ``tar.gz``, or ``tar`` from magic bytes."""

    if head[:4] in (ARCHIVE_MAGIC_ZIP, ARCHIVE_MAGIC_ZIP_EMPTY, ARCHIVE_MAGIC_ZIP_SPANNED):
        return "zip"
    if head[:2] == ARCHIVE_MAGIC_GZIP:
        return "tar.gz"
    if len(head) >= ARCHIVE_TAR_MAGIC_OFFSET + len(ARCHIVE_TAR_MAGIC) and (
        head[ARCHIVE_TAR_MAGIC_OFFSET : ARCHIVE_TAR_MAGIC_OFFSET + 5] == ARCHIVE_TAR_MAGIC
    ):
        return "tar"
    raise ArchiveRejectedError("unsupported-format", "Unrecognized archive container.")


def _check_member_name(name: str, limits: ArchiveLimits) -> str:
    """Normalize one member name or reject it with a stable code."""

    if len(name) > limits.max_name_length:
        raise ArchiveRejectedError("name-too-long", "Archive member name is too long.")
    if "\\" in name:
        # Backslash is a legal Unix filename character but a Windows
        # separator; the portable grammar refuses it everywhere.
        raise ArchiveRejectedError("unsafe-name", "Archive member name is not portable.")
    if name.startswith("/") or name[1:2] == ":" and name[0].isalpha():
        raise ArchiveRejectedError("absolute-path", "Archive member must be relative.")
    if ".." in name.replace("\\", "/").split("/"):
        raise ArchiveRejectedError("traversal", "Archive member escapes its directory.")
    try:
        canonical = validate_relative_path(name, "Archive member")
    except ValueError as exc:
        raise ArchiveRejectedError("unsafe-name", "Archive member name is not portable.") from exc
    parts = canonical.split("/")
    if len(parts) > limits.max_depth:
        raise ArchiveRejectedError("depth-exceeded", "Archive member is nested too deep.")
    if any(len(part) > limits.max_component_length for part in parts):
        raise ArchiveRejectedError("name-too-long", "Archive path component is too long.")
    return canonical


def _check_cancelled(cancel: Callable[[], bool] | None) -> None:
    if cancel is not None and cancel():
        raise ArchiveCancelled("Archive extraction cancelled.")


def _refuse_symlink(path: Path, *, what: str) -> None:
    if path.is_symlink():
        raise ArchiveRejectedError("link", f"{what} must not be a symlink.")


def _ensure_dest_dir(dest: Path, rel: str) -> Path:
    """Create one member parent chain, refusing every symlink on the way."""

    current = dest
    for part in rel.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            raise ArchiveRejectedError("link", "Archive parent directory must not be a symlink.")
        if current.exists():
            if not current.is_dir():
                raise ArchiveRejectedError(
                    "conflicting-path", "Archive directory conflicts with a file."
                )
            continue
        try:
            current.mkdir(mode=0o755)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ArchiveRejectedError(
                "disk-full" if exc.errno == errno.ENOSPC else "io-error",
                "Archive parent directory could not be created.",
            ) from exc
        if current.is_symlink() or not current.is_dir():
            raise ArchiveRejectedError("link", "Archive parent directory is not safe.")
    return current


def _chunked_reader(read: Callable[[int], bytes]) -> Iterator[bytes]:
    while True:
        chunk = read(65536)
        if not chunk:
            return
        yield chunk


def _write_member(
    dest: Path,
    rel: str,
    stream: object,
    *,
    expected_size: int | None,
    budget: list[int],
    limits: ArchiveLimits,
    cancel: Callable[[], bool] | None,
) -> int:
    """Stream one member to disk; return bytes written."""

    target = dest / Path(*rel.split("/"))
    _refuse_symlink(target, what="Archive member")
    if target.exists():
        raise ArchiveRejectedError("duplicate-path", "Archive member collides.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except FileExistsError:
        raise ArchiveRejectedError("duplicate-path", "Archive member collides.") from None
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            raise ArchiveRejectedError("link", "Archive member must not be a symlink.") from None
        if getattr(exc, "errno", None) == errno.ENOSPC:
            raise ArchiveRejectedError("disk-full", "No space left for archive member.") from None
        raise ArchiveRejectedError("io-error", "Archive member could not be created.") from exc
    written = 0
    try:
        read = getattr(stream, "read", None)
        if callable(read):
            iterator: Iterable[bytes] = _chunked_reader(read)
        elif isinstance(stream, Iterable):
            iterator = stream
        else:
            raise ArchiveRejectedError("corrupt-archive", "Archive member is unreadable.")
        for chunk in iterator:
            _check_cancelled(cancel)
            if not isinstance(chunk, (bytes, bytearray)):
                raise ArchiveRejectedError("corrupt-archive", "Archive member is unreadable.")
            written += len(chunk)
            if expected_size is not None and written > expected_size:
                raise ArchiveRejectedError(
                    "file-too-large", "Archive member exceeds its expected size."
                )
            if written > limits.max_file_bytes:
                raise ArchiveRejectedError(
                    "file-too-large", "Archive member exceeds the file size limit."
                )
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise ArchiveRejectedError(
                    "total-too-large", "Archive expansion exceeds its budget."
                )
            view = memoryview(chunk)
            while view:
                try:
                    done = os.write(fd, view)
                except OSError as exc:
                    if getattr(exc, "errno", None) == errno.ENOSPC:
                        raise ArchiveRejectedError(
                            "disk-full", "No space left for archive member."
                        ) from exc
                    raise ArchiveRejectedError(
                        "io-error", "Archive member could not be written."
                    ) from exc
                view = view[done:]
        os.fsync(fd)
    finally:
        os.close(fd)
    if target.is_symlink():
        # Defense in depth: the leaf must still be the regular file created
        # above, whatever the container metadata claimed.
        raise ArchiveRejectedError("link", "Extracted member must not be a symlink.")
    return written


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


_ZIP_EOCD_MAGIC = b"PK\x05\x06"
_ZIP64_LOCATOR_MAGIC = b"PK\x06\x07"
_ZIP64_EOCD_MAGIC = b"PK\x06\x06"
_ZIP_EOCD_TAIL_READ = 22 + 65535
_ZIP64_END_RECORD_SIZE = 56


def _zip_end_central_count(fd: int) -> int | None:
    """Best-effort central-directory entry count from the ZIP end record.

    Reads only the file tail, so the member count is bounded before the
    central directory is parsed into memory. Returns ``None`` when no
    consistent end record is found; callers fall through to full parsing,
    which still enforces every limit.
    """

    try:
        size = os.fstat(fd).st_size
    except OSError:
        return None
    tail_len = min(size, _ZIP_EOCD_TAIL_READ)
    if tail_len < 22:
        return None
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        tail = b""
        while len(tail) < tail_len:
            chunk = os.read(fd, tail_len - len(tail))
            if not chunk:
                break
            tail += chunk
    except OSError:
        return None
    if len(tail) != tail_len:
        return None
    # The end record is the last one whose comment length lines up exactly
    # with the end of file; earlier magic bytes may be file content.
    offset = tail_len
    while True:
        at = tail.rfind(_ZIP_EOCD_MAGIC, 0, offset)
        if at < 0:
            return None
        comment_len = int.from_bytes(tail[at + 20 : at + 22], "little")
        if at + 22 + comment_len == tail_len:
            break
        offset = at
    count = int.from_bytes(tail[at + 8 : at + 10], "little")
    total = int.from_bytes(tail[at + 10 : at + 12], "little")
    entries = max(count, total)
    if entries != 0xFFFF:
        return entries
    if at < 20 or tail[at - 20 : at - 16] != _ZIP64_LOCATOR_MAGIC:
        return None
    zip64_off = int.from_bytes(tail[at - 12 : at - 4], "little")
    try:
        os.lseek(fd, zip64_off, os.SEEK_SET)
        record = b""
        while len(record) < _ZIP64_END_RECORD_SIZE:
            chunk = os.read(fd, _ZIP64_END_RECORD_SIZE - len(record))
            if not chunk:
                break
            record += chunk
    except OSError:
        return None
    if len(record) != _ZIP64_END_RECORD_SIZE or record[:4] != _ZIP64_EOCD_MAGIC:
        return None
    return int.from_bytes(record[32:40], "little")


def _extract_zip(
    archive_path: Path,
    dest: Path,
    expected: Mapping[str, int] | None,
    skipped: set[str],
    limits: ArchiveLimits,
    budget_bytes: int,
    cancel: Callable[[], bool] | None,
) -> tuple[str, ...]:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveRejectedError(
            "corrupt-archive", "Unreadable zip container.", retryable=True
        ) from exc
    with archive:
        # Bound the member count from the end record before parsing the
        # central directory into memory; an unparseable end record falls
        # through to full parsing, which still enforces every limit.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            count_fd = os.open(archive_path, flags)
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                raise ArchiveRejectedError("link", "Archive must not be a symlink.") from None
            raise ArchiveRejectedError("corrupt-archive", "Archive cannot be read.") from exc
        try:
            pre_count = _zip_end_central_count(count_fd)
        finally:
            os.close(count_fd)
        if pre_count is not None and pre_count > limits.max_members:
            raise ArchiveRejectedError("too-many-members", "Archive holds too many members.")
        infos = archive.infolist()
        if len(infos) > limits.max_members:
            raise ArchiveRejectedError("too-many-members", "Archive holds too many members.")
        planned: list[tuple[str, zipfile.ZipInfo | None]] = []
        seen: set[str] = set()
        declared_compressed = 0
        for info in infos:
            _check_cancelled(cancel)
            if info.is_dir():
                rel = _check_member_name(info.filename.rstrip("/"), limits)
                if rel in seen:
                    raise ArchiveRejectedError("duplicate-path", "Archive repeats a path.")
                seen.add(rel)
                planned.append((rel, None))
                continue
            rel = _check_member_name(info.filename, limits)
            if rel in seen:
                raise ArchiveRejectedError("duplicate-path", "Archive repeats a path.")
            seen.add(rel)
            if info.flag_bits & 0x1:
                raise ArchiveRejectedError(
                    "encrypted-archive", "Encrypted archives are never opened."
                )
            if _zip_is_symlink(info):
                raise ArchiveRejectedError("link", "Archive symlinks are never extracted.")
            if expected is not None and rel not in expected:
                raise ArchiveRejectedError("unexpected-member", "Archive holds an unlisted member.")
            declared_compressed += info.compress_size
            planned.append((rel, info))
        _enforce_prescan_totals(
            [(rel, info.file_size if info else 0) for rel, info in planned],
            expected,
            declared_compressed,
            limits,
            budget_bytes,
        )
        extracted: list[str] = []
        budget = [budget_bytes]
        expanded = 0
        for rel, planned_info in planned:
            _check_cancelled(cancel)
            if planned_info is None:
                _ensure_dir_member(dest, rel)
                continue
            if rel in skipped:
                continue
            cap = expected[rel] if expected is not None else (planned_info.file_size or None)
            _ensure_dest_dir(dest, rel)
            try:
                with archive.open(planned_info, "r") as stream:
                    written = _write_member(
                        dest,
                        rel,
                        stream,
                        expected_size=cap,
                        budget=budget,
                        limits=limits,
                        cancel=cancel,
                    )
            except (zipfile.BadZipFile, OSError) as exc:
                raise ArchiveRejectedError(
                    "corrupt-archive", "Archive member is unreadable.", retryable=True
                ) from exc
            if cap is not None and written != cap:
                raise ArchiveRejectedError(
                    "corrupt-archive", "Archive member ended early.", retryable=True
                )
            expanded += written
            _enforce_ratio(expanded, declared_compressed, limits)
            extracted.append(rel)
        if expected is not None:
            missing = set(expected) - set(extracted) - skipped
            if missing:
                raise ArchiveRejectedError(
                    "incomplete-archive", "Archive is missing expected members.", retryable=True
                )
        return tuple(extracted)


def _ensure_dir_member(dest: Path, rel: str) -> None:
    current = dest
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise ArchiveRejectedError("link", "Archive directory must not be a symlink.")
        if current.exists():
            if not current.is_dir():
                raise ArchiveRejectedError(
                    "conflicting-path", "Archive directory conflicts with a file."
                )
            continue
        try:
            current.mkdir(mode=0o755)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ArchiveRejectedError(
                "disk-full" if exc.errno == errno.ENOSPC else "io-error",
                "Archive directory could not be created.",
            ) from exc
        if current.is_symlink() or not current.is_dir():
            raise ArchiveRejectedError("link", "Archive directory is not safe.")


def _enforce_prescan_totals(
    planned: list[tuple[str, int]],
    expected: Mapping[str, int] | None,
    declared_compressed: int,
    limits: ArchiveLimits,
    budget_bytes: int,
) -> None:
    declared_total = sum(size for _, size in planned)
    if expected is not None and declared_total > sum(expected.values()):
        raise ArchiveRejectedError("total-too-large", "Archive declares more bytes than expected.")
    if declared_total > limits.max_total_bytes or declared_total > budget_bytes:
        raise ArchiveRejectedError("total-too-large", "Archive declares more bytes than allowed.")
    _enforce_ratio(declared_total, declared_compressed, limits)
    for _, size in planned:
        if size > limits.max_file_bytes:
            raise ArchiveRejectedError(
                "file-too-large", "Archive member exceeds the file size limit."
            )


def _enforce_ratio(expanded: int, compressed: int, limits: ArchiveLimits) -> None:
    if compressed <= 0 or expanded <= 0:
        return
    if expanded / compressed > limits.max_ratio:
        raise ArchiveRejectedError("ratio-exceeded", "Archive compression ratio exceeds its limit.")


def _extract_tar(
    archive_path: Path,
    dest: Path,
    expected: Mapping[str, int] | None,
    skipped: set[str],
    limits: ArchiveLimits,
    budget_bytes: int,
    cancel: Callable[[], bool] | None,
) -> tuple[str, ...]:
    try:
        archive = tarfile.open(archive_path, mode="r|*")
    except (tarfile.TarError, OSError) as exc:
        raise ArchiveRejectedError(
            "corrupt-archive", "Unreadable tar container.", retryable=True
        ) from exc
    # Streaming tar has no central directory, so the container file size is
    # the compression baseline: expanded bytes beyond ratio × container can
    # only come from a bomb. Plain tar matches its own size and never trips.
    try:
        container_bytes = os.lstat(archive_path).st_size
    except OSError as exc:
        raise ArchiveRejectedError("corrupt-archive", "Archive cannot be read.") from exc
    extracted: list[str] = []
    seen: set[str] = set()
    created_dirs: set[str] = set()
    budget = [budget_bytes]
    expanded = 0
    with archive:
        for member in archive:
            _check_cancelled(cancel)
            if len(seen) + len(created_dirs) >= limits.max_members:
                raise ArchiveRejectedError("too-many-members", "Archive holds too many members.")
            if member.isdir() and member.name.endswith("/"):
                raw_name = member.name.rstrip("/")
            else:
                raw_name = member.name
            rel = _check_member_name(raw_name, limits)
            if rel in seen or rel in created_dirs:
                raise ArchiveRejectedError("duplicate-path", "Archive repeats a path.")
            if member.isdir():
                created_dirs.add(rel)
                _ensure_dir_member(dest, rel)
                continue
            if member.issym() or member.islnk():
                raise ArchiveRejectedError("link", "Archive links are never extracted.")
            if not member.isfile():
                raise ArchiveRejectedError(
                    "special-file", "Archive special files are never extracted."
                )
            if expected is not None and rel not in expected:
                raise ArchiveRejectedError("unexpected-member", "Archive holds an unlisted member.")
            seen.add(rel)
            if rel in skipped:
                # Presence is proven; bytes stay untouched on disk. The
                # sequential tar stream still has to be consumed boundedly.
                _discard_member(archive, member, budget, limits, cancel)
                continue
            _ensure_dest_dir(dest, rel)
            reader = archive.extractfile(member)
            if reader is None:
                raise ArchiveRejectedError("corrupt-archive", "Archive member is unreadable.")
            cap = expected[rel] if expected is not None else (member.size or None)
            with reader:
                written = _write_member(
                    dest,
                    rel,
                    reader,
                    expected_size=cap,
                    budget=budget,
                    limits=limits,
                    cancel=cancel,
                )
            if cap is not None and written != cap:
                raise ArchiveRejectedError(
                    "corrupt-archive", "Archive member ended early.", retryable=True
                )
            expanded += written
            if expanded > limits.max_ratio * container_bytes:
                raise ArchiveRejectedError(
                    "ratio-exceeded", "Archive compression ratio exceeds its limit."
                )
            extracted.append(rel)
    if expected is not None:
        missing = set(expected) - set(extracted) - skipped
        if missing:
            raise ArchiveRejectedError(
                "incomplete-archive", "Archive is missing expected members.", retryable=True
            )
    return tuple(extracted)


def _discard_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    budget: list[int],
    limits: ArchiveLimits,
    cancel: Callable[[], bool] | None,
) -> None:
    """Consume one streamed tar member without writing, still bounded."""

    reader = archive.extractfile(member)
    if reader is None:
        raise ArchiveRejectedError("corrupt-archive", "Archive member is unreadable.")
    with reader:
        while True:
            _check_cancelled(cancel)
            chunk = reader.read(65536)
            if not chunk:
                break
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise ArchiveRejectedError(
                    "total-too-large", "Archive expansion exceeds its budget."
                )


def _probe_head(archive_path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(archive_path, flags)
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            raise ArchiveRejectedError("link", "Archive must not be a symlink.") from None
        raise ArchiveRejectedError("corrupt-archive", "Archive cannot be opened.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ArchiveRejectedError("corrupt-archive", "Archive is not a regular file.")
        head = b""
        while len(head) < 512:
            chunk = os.read(fd, 512 - len(head))
            if not chunk:
                break
            head += chunk
        return head
    except OSError as exc:
        raise ArchiveRejectedError("corrupt-archive", "Archive cannot be read.") from exc
    finally:
        os.close(fd)


def extract_archive(
    archive_path: Path,
    dest_dir: Path,
    *,
    expected: Mapping[str, int] | None = None,
    skip: Collection[str] = (),
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
    budget_bytes: int | None = None,
    cancel: Callable[[], bool] | threading.Event | None = None,
) -> tuple[str, ...]:
    """Extract one archive into an existing directory with hard bounds.

    ``dest_dir`` must already exist as a real directory; members land only
    inside it. When ``expected`` maps relative paths to exact byte sizes,
    extraction is an allowlist: unlisted members abort, missing members fail
    at the end, and per-member sizes are enforced while streaming. Paths in
    ``skip`` must still be present in the archive but are not rewritten, so
    resume keeps verified members. Without ``expected`` the generic limits
    alone bound the expansion. ``cancel`` is either a predicate or a
    threading event; cancellation raises :class:`ArchiveCancelled` and
    leaves already-written members in place for the caller to clean up.
    Returns the written member paths.
    """

    if dest_dir.is_symlink() or not dest_dir.is_dir():
        raise ArchiveRejectedError("link", "Archive destination must be a real directory.")
    skipped = set(skip)
    if expected is not None and not skipped <= set(expected):
        raise ArchiveRejectedError("unexpected-member", "Skip set names unlisted members.")
    head = _probe_head(archive_path)
    if len(head) == 0:
        raise ArchiveRejectedError("corrupt-archive", "Archive is empty.")
    kind = detect_archive_format(head)
    budget = limits.max_total_bytes if budget_bytes is None else budget_bytes
    should_cancel: Callable[[], bool] | None
    if cancel is None:
        should_cancel = None
    elif isinstance(cancel, threading.Event):
        should_cancel = cancel.is_set
    else:
        should_cancel = cancel
    if kind == "zip":
        return _extract_zip(
            archive_path, dest_dir, expected, skipped, limits, budget, should_cancel
        )
    return _extract_tar(archive_path, dest_dir, expected, skipped, limits, budget, should_cancel)


__all__ = [
    "ArchiveCancelled",
    "ArchiveLimits",
    "ArchiveRejectedError",
    "DEFAULT_ARCHIVE_LIMITS",
    "detect_archive_format",
    "extract_archive",
]
