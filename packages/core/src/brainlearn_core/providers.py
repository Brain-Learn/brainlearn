"""Provider-neutral read-only dataset source boundary (no networking/downloads).

This module defines the small asynchronous interface every dataset source
must implement to list public datasets and resolve one immutable snapshot
into a schema-``1.0`` :class:`CatalogEntry`. Providers return validated
records only: resolution always goes through ``CatalogEntry`` validation, so
a provider can never bypass checksums, URL, identity, or licensing rules,
and transfer endpoints, credentials, tokens, cookies, query signatures, or
raw provider responses are never persisted.

There is deliberately no download, extraction, resume/cancellation-of-
bytes, UI, or BIDS/MNE surface here. Cancellation in this module means
abandoning an in-flight metadata request (``asyncio`` cancellation
propagates unchanged); dataset download lifecycle arrives in a later unit.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from brainlearn_core.datasets import CatalogEntry

PROVIDER_SCHEMA_VERSION: Literal["1.0"] = "1.0"

MAX_LIST_FIRST = 50
DEFAULT_LIST_FIRST = 10


class ProviderError(Exception):
    """The provider failed to answer a metadata request."""


class ProviderTimeout(ProviderError):
    """The provider did not answer within its bounded timeout."""


class ProviderNotFound(ProviderError):
    """The requested dataset snapshot does not exist (or is not public)."""


class ProviderMalformed(ProviderError):
    """The provider answered, but the payload was missing or invalid."""


class RangeUnsupportedError(ValueError):
    """A source ignored a resume offset and answered a full stream.

    Engines must truncate the partial file and restart it from zero instead
    of appending, which would corrupt the bytes.
    """


@dataclass(frozen=True, slots=True)
class DatasetSearch:
    """One page of a provider listing request."""

    first: int = DEFAULT_LIST_FIRST
    after: str | None = None
    query: str | None = None
    modality: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderDatasetRef:
    """One lightweight listing hit; resolving needs an explicit snapshot tag."""

    provider: str
    dataset_id: str
    title: str
    public: bool
    latest_snapshot: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetPage:
    """One listing page with an opaque cursor for the next page."""

    items: tuple[ProviderDatasetRef, ...]
    next_cursor: str | None
    has_more: bool


class DatasetProvider(Protocol):
    """Read-only source of immutable public dataset snapshots.

    The contract is public-only: ``list_datasets`` never reports
    non-public records, and ``resolve_snapshot`` reports a non-public (or
    unknown) snapshot as :class:`ProviderNotFound`.
    """

    @property
    def provider_name(self) -> str:
        """The canonical provider key (for example ``"openneuro"``)."""
        ...

    async def list_datasets(self, search: DatasetSearch) -> DatasetPage:
        """List public datasets, one bounded page at a time."""
        ...

    async def resolve_snapshot(self, dataset_id: str, snapshot_tag: str) -> CatalogEntry:
        """Resolve ``(dataset_id, snapshot_tag)`` to validated catalog data."""
        ...


def _opaque_cursor(offset: int) -> str:
    return f"mock-cursor-{offset}"


def _parse_opaque_cursor(after: str | None) -> int:
    if after is None:
        return 0
    prefix = "mock-cursor-"
    if not after.startswith(prefix):
        raise ProviderMalformed(f"Unknown listing cursor {after!r}.")
    try:
        offset = int(after[len(prefix) :])
    except ValueError as exc:
        raise ProviderMalformed(f"Unknown listing cursor {after!r}.") from exc
    if offset < 0:
        raise ProviderMalformed(f"Unknown listing cursor {after!r}.")
    return offset


class MockDatasetProvider:
    """Deterministic in-memory provider for ordinary (offline) tests.

    ``entries`` may hold validated :class:`CatalogEntry` records or raw
    mappings; raw mappings are validated on resolution so malformed
    provider metadata surfaces as :class:`ProviderMalformed` instead of
    bypassing the contract. The mock models the public-only rule: listing
    reports confirmed-public entries only (raw mappings must carry
    ``access == "public"``), and resolving a matched non-public entry
    raises :class:`ProviderNotFound`, mirroring the OpenNeuro adapter.
    ``fail_mode`` injects one deterministic failure across both methods:

    - ``"none"``: serve entries normally.
    - ``"error"``: raise :class:`ProviderError`.
    - ``"timeout"``: wait out ``delay_s`` (cancellable) then raise
      :class:`ProviderTimeout`.
    - ``"malformed"``: raise :class:`ProviderMalformed`.
    """

    def __init__(
        self,
        entries: Sequence[CatalogEntry | Mapping[str, Any]],
        *,
        provider_name: str = "mock-archive",
        delay_s: float = 0.0,
        fail_mode: Literal["none", "error", "timeout", "malformed"] = "none",
        error_message: str = "Mock provider failure.",
        failures: Sequence[Exception] | None = None,
    ) -> None:
        self._entries: tuple[CatalogEntry | Mapping[str, Any], ...] = tuple(entries)
        self._provider_name = provider_name
        self._delay_s = delay_s
        self._fail_mode = fail_mode
        self._error_message = error_message
        self._failures: list[Exception] = list(failures) if failures is not None else []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def failures_remaining(self) -> int:
        """How many scripted provider failures are still queued."""
        return len(self._failures)

    async def _maybe_fail(self, what: str) -> None:
        if self._delay_s > 0:
            # Bare sleep: asyncio cancellation propagates to the caller
            # unchanged instead of becoming a provider error.
            await asyncio.sleep(self._delay_s)
        if self._failures:
            raise self._failures.pop(0)
        if self._fail_mode == "error":
            raise ProviderError(f"{self._error_message} ({what})")
        if self._fail_mode == "timeout":
            raise ProviderTimeout(f"Mock provider timed out ({what}).")
        if self._fail_mode == "malformed":
            raise ProviderMalformed(f"Mock provider returned malformed data ({what}).")

    @staticmethod
    def _is_public(entry: CatalogEntry | Mapping[str, Any]) -> bool:
        if isinstance(entry, CatalogEntry):
            return entry.access.value == "public"
        return entry.get("access") == "public"

    @staticmethod
    def _ref_title(entry: CatalogEntry | Mapping[str, Any]) -> str:
        if isinstance(entry, CatalogEntry):
            return entry.title
        title = entry.get("title")
        return title if isinstance(title, str) and title else str(entry.get("dataset_id", ""))

    @staticmethod
    def _ref_modality(entry: CatalogEntry | Mapping[str, Any]) -> str:
        if isinstance(entry, CatalogEntry):
            return entry.modality
        modality = entry.get("modality")
        return modality if isinstance(modality, str) else ""

    def _matches(
        self, entry: CatalogEntry | Mapping[str, Any], *, query: str | None, modality: str | None
    ) -> bool:
        if modality is not None and self._ref_modality(entry).lower() != modality.lower():
            return False
        if query is not None:
            needle = query.lower()
            if isinstance(entry, CatalogEntry):
                haystacks = (entry.dataset_id.lower(), entry.title.lower())
            else:
                dataset_id = entry.get("dataset_id")
                haystacks = (
                    dataset_id.lower() if isinstance(dataset_id, str) else "",
                    self._ref_title(entry).lower(),
                )
            if not any(needle in haystack for haystack in haystacks):
                return False
        return True

    def _ref_for(self, entry: CatalogEntry | Mapping[str, Any]) -> ProviderDatasetRef:
        if isinstance(entry, CatalogEntry):
            return ProviderDatasetRef(
                provider=entry.provider,
                dataset_id=entry.dataset_id,
                title=entry.title,
                public=entry.access.value == "public",
                latest_snapshot=entry.snapshot,
            )
        dataset_id = entry.get("dataset_id")
        provider = entry.get("provider", self._provider_name)
        snapshot = entry.get("snapshot")
        access = entry.get("access")
        return ProviderDatasetRef(
            provider=provider if isinstance(provider, str) else self._provider_name,
            dataset_id=dataset_id if isinstance(dataset_id, str) else "",
            title=self._ref_title(entry),
            public=access == "public",
            latest_snapshot=snapshot if isinstance(snapshot, str) else None,
        )

    async def list_datasets(self, search: DatasetSearch) -> DatasetPage:
        """Return one filtered page of public listing hits with an opaque cursor.

        Non-public entries are excluded before pagination, so cursors always
        describe the public sequence and restricted records never leak into
        any page.
        """
        await self._maybe_fail("list")
        if not 1 <= search.first <= MAX_LIST_FIRST:
            raise ValueError(f"first must be within 1..{MAX_LIST_FIRST}, got {search.first}.")
        offset = _parse_opaque_cursor(search.after)
        matching = [
            self._ref_for(entry)
            for entry in self._entries
            if self._is_public(entry)
            and self._matches(entry, query=search.query, modality=search.modality)
        ]
        window = matching[offset : offset + search.first]
        end = offset + len(window)
        has_more = end < len(matching)
        return DatasetPage(
            items=tuple(window),
            next_cursor=_opaque_cursor(end) if has_more else None,
            has_more=has_more,
        )

    async def resolve_snapshot(self, dataset_id: str, snapshot_tag: str) -> CatalogEntry:
        """Validate and return the one public entry for ``(dataset_id, snapshot_tag)``."""
        await self._maybe_fail("resolve")
        for entry in self._entries:
            if isinstance(entry, CatalogEntry):
                if entry.dataset_id == dataset_id and entry.snapshot == snapshot_tag:
                    if not self._is_public(entry):
                        raise ProviderNotFound(
                            f"Dataset snapshot {dataset_id}:{snapshot_tag} is not public."
                        )
                    return entry
                continue
            if entry.get("dataset_id") == dataset_id and entry.get("snapshot") == snapshot_tag:
                try:
                    validated = CatalogEntry.model_validate(dict(entry))
                except Exception as exc:
                    raise ProviderMalformed(
                        f"Mock provider metadata for {dataset_id}:{snapshot_tag} "
                        f"failed catalog validation: {exc}."
                    ) from exc
                if not self._is_public(validated):
                    raise ProviderNotFound(
                        f"Dataset snapshot {dataset_id}:{snapshot_tag} is not public."
                    )
                return validated
        raise ProviderNotFound(f"Unknown dataset snapshot {dataset_id}:{snapshot_tag}.")


class FileDownloadSource(Protocol):
    """Minimum download-source abstraction: deterministic streamed bytes.

    Sources yield raw bytes; they never write files, verify checksums, or
    persist anything. Transport failures surface as :class:`ProviderError`
    subclasses (``ProviderTimeout`` for timeouts); unknown files surface as
    :class:`ProviderNotFound`. When ``offset`` is nonzero and the server
    answers a full stream instead of a range, sources raise
    :class:`RangeUnsupportedError` so engines restart the file instead of
    appending mismatched bytes. Iterators may be synchronous generators:
    engines drive them from worker threads.
    """

    @property
    def source_name(self) -> str:
        """The canonical provider key this source serves bytes for."""
        ...

    @property
    def supports_resume(self) -> bool:
        """Whether nonzero offsets are honored (else engines restart files)."""
        ...

    def stream_file(
        self, dataset_id: str, snapshot: str, path: str, offset: int
    ) -> Iterator[bytes]:
        """Yield one file's bytes starting exactly at ``offset``."""
        ...


@runtime_checkable
class SnapshotArchiveSource(Protocol):
    """Optional whole-snapshot archive delivery for a download source.

    A source implementing this capability serves the snapshot's files
    packed in one archive (ZIP or tar container, detected by magic bytes).
    Engines prefer this path when present: they stream the archive with a
    bounded budget, extract exactly the catalog-listed members, and verify
    each member against the catalog before the shared atomic finalization.
    Per-file streaming stays the path for sources without this capability.
    """

    def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Iterator[bytes]:
        """Yield one snapshot archive from its first byte."""
        ...


class ScriptedDownloadSource:
    """Deterministic in-memory byte source for offline download tests.

    ``files`` maps relative paths to exact bytes. ``failures`` maps a path
    to a queue of exceptions raised from successive stream attempts, which
    models retryable faults and retry exhaustion. ``truncate_at`` maps a
    path to a shorter length that is actually yielded, modelling short
    reads. ``chunk_size`` bounds memory per yield. ``gate`` (a threading
    event) is waited on before every chunk when set, modelling slow streams
    for cancellation tests. ``honor_range`` mirrors servers that ignore
    Range requests, such as the observed OpenNeuro file endpoint.
    """

    def __init__(
        self,
        files: Mapping[str, bytes],
        *,
        source_name: str = "mock-archive",
        supports_resume: bool = True,
        honor_range: bool = True,
        chunk_size: int = 65536,
        failures: Mapping[str, list[Exception]] | None = None,
        truncate_at: Mapping[str, int] | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self._files = dict(files)
        self._source_name = source_name
        self._supports_resume = supports_resume
        self._honor_range = honor_range
        self._chunk_size = chunk_size
        self._failures: dict[str, list[Exception]] = (
            {path: list(items) for path, items in failures.items()} if failures else {}
        )
        self._truncate_at = dict(truncate_at) if truncate_at else {}
        self._gate = gate
        self.requests: list[dict[str, Any]] = []

    @property
    def source_name(self) -> str:
        return self._source_name

    @property
    def supports_resume(self) -> bool:
        return self._supports_resume

    def stream_file(
        self, dataset_id: str, snapshot: str, path: str, offset: int
    ) -> Iterator[bytes]:
        """Yield scripted bytes for ``path`` starting at ``offset``."""
        if offset < 0:
            raise ValueError(f"Resume offset must not be negative, got {offset}.")
        self.requests.append(
            {"dataset_id": dataset_id, "snapshot": snapshot, "path": path, "offset": offset}
        )
        if path not in self._files:
            raise ProviderNotFound(f"Unknown source file {path!r}.")
        if offset > 0 and not self._honor_range:
            raise RangeUnsupportedError(
                f"Source {self._source_name!r} ignored the resume offset for {path!r}."
            )
        pending = self._failures.get(path)
        if pending:
            raise pending.pop(0)
        data = self._files[path]
        if offset > len(data):
            raise ProviderMalformed(f"Resume offset {offset} exceeds source file {path!r}.")
        limit = self._truncate_at.get(path, len(data))
        blob = data[offset:limit]
        for start in range(0, len(blob), self._chunk_size):
            if self._gate is not None:
                self._gate.wait(timeout=30.0)
            yield blob[start : start + self._chunk_size]

    def failures_remaining(self, path: str) -> int:
        """How many scripted failures are still queued for ``path``."""

        return len(self._failures.get(path, []))


class ScriptedSnapshotArchive:
    """Deterministic whole-snapshot archive source for offline tests.

    ``members`` maps relative paths to exact bytes; the archive container
    is built eagerly with the real stdlib writers so engines parse genuine
    ZIP/tar bytes. ``failures`` queues exceptions raised from successive
    archive stream attempts, modelling retryable faults and exhaustion.
    ``gate`` is waited on before every chunk when set, modelling slow
    archives for cancellation tests.
    """

    def __init__(
        self,
        members: Mapping[str, bytes],
        *,
        format: Literal["zip", "tar.gz"] = "zip",
        source_name: str = "mock-archive",
        failures: list[Exception] | None = None,
        chunk_size: int = 65536,
        gate: threading.Event | None = None,
    ) -> None:
        import io
        import tarfile
        import zipfile

        self._members = dict(members)
        self._source_name = source_name
        self._failures = list(failures) if failures else []
        self._chunk_size = chunk_size
        self._gate = gate
        self.requests: list[dict[str, Any]] = []
        buffer = io.BytesIO()
        if format == "zip":
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                for path, blob in self._members.items():
                    archive.writestr(path, blob)
        elif format == "tar.gz":
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for path, blob in self._members.items():
                    info = tarfile.TarInfo(path)
                    info.size = len(blob)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(blob))
        else:
            raise ValueError(f"Unsupported scripted archive format {format!r}.")
        self._blob = buffer.getvalue()

    @property
    def source_name(self) -> str:
        return self._source_name

    @property
    def supports_resume(self) -> bool:
        return False

    def stream_file(
        self, dataset_id: str, snapshot: str, path: str, offset: int
    ) -> Iterator[bytes]:
        """Archive-only doubles never serve individual files."""

        raise ProviderNotFound(f"Archive source has no individual file {path!r}.")

    def stream_snapshot_archive(self, dataset_id: str, snapshot: str) -> Iterator[bytes]:
        """Yield the scripted snapshot archive from its first byte."""

        self.requests.append({"dataset_id": dataset_id, "snapshot": snapshot})
        if self._failures:
            raise self._failures.pop(0)
        for start in range(0, len(self._blob), self._chunk_size):
            if self._gate is not None:
                self._gate.wait(timeout=30.0)
            yield self._blob[start : start + self._chunk_size]

    def failures_remaining(self) -> int:
        """How many scripted archive failures are still queued."""

        return len(self._failures)
