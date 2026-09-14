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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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

    provider_name: str

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
    ) -> None:
        self._entries: tuple[CatalogEntry | Mapping[str, Any], ...] = tuple(entries)
        self._provider_name = provider_name
        self._delay_s = delay_s
        self._fail_mode = fail_mode
        self._error_message = error_message

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def _maybe_fail(self, what: str) -> None:
        if self._delay_s > 0:
            # Bare sleep: asyncio cancellation propagates to the caller
            # unchanged instead of becoming a provider error.
            await asyncio.sleep(self._delay_s)
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
