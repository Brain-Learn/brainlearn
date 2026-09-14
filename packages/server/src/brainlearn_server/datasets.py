"""Read-only dataset-library sources for the local API (no downloads).

This module wires the approved provider-neutral protocol
(:class:`DatasetProvider`) into the local service. Endpoints expose bounded
listing/search and explicit immutable snapshot resolution only. There is no
download, extraction, lock writing, transfer URL, or credential surface
anywhere in this module: responses carry validated catalog metadata and
nothing else.

Error responses never include provider exception text: upstream messages can
echo fields, URLs, credentials, query signatures, or raw fragments, so HTTP
details are stable generic strings selected from the exception class, plus
only identifiers that already passed the API's strict validation. Nothing
provider-supplied is written to logs either: handled dataset errors never
reach the fault logger, whose policy excludes secrets.

The default source is the OpenNeuro public metadata adapter, built lazily
on first request so importing or starting the service never touches the
network. Tests inject a deterministic :class:`MockDatasetProvider` through
:func:`set_dataset_providers_for_tests`, which keeps the ordinary suite
fully offline.
"""

from __future__ import annotations

import re

from brainlearn_core import (
    OPENNEURO_PROVIDER,
    CatalogEntry,
    DatasetPage,
    DatasetProvider,
    DatasetSearch,
    OpenNeuroProvider,
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
    UrllibGraphQLTransport,
)
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_LIST_TIMEOUT_DETAIL = (
    "The dataset source timed out. Retry the request; no local state was changed."
)
_LIST_MALFORMED_DETAIL = (
    "The dataset source returned unusable metadata. Try again later or choose another source."
)
_LIST_FAILED_DETAIL = "The dataset source request failed. Try again later or choose another source."
_LIST_INVALID_DETAIL = (
    "Invalid dataset listing request. Check the page size and cursor, then try again."
)
_RESOLVE_TIMEOUT_DETAIL = (
    "The dataset source timed out. Retry the request; no local state was changed."
)
_RESOLVE_MALFORMED_DETAIL = (
    "The dataset source returned unusable metadata for this snapshot. Try again later."
)
_RESOLVE_FAILED_DETAIL = "The dataset source request failed for this snapshot. Try again later."
_RESOLVE_INVALID_DETAIL = (
    "Invalid dataset identifier. Check the dataset id and snapshot tag, then try again."
)

_openneuro_provider: OpenNeuroProvider | None = None
_providers_override: dict[str, DatasetProvider] | None = None


def set_dataset_providers_for_tests(providers: dict[str, DatasetProvider] | None) -> None:
    """Inject provider sources for tests; ``None`` restores the lazy default."""

    global _providers_override
    _providers_override = providers


def get_dataset_provider(name: str) -> DatasetProvider:
    """Return the provider source for ``name`` without performing any I/O."""

    if _providers_override is not None:
        provider = _providers_override.get(name)
        if provider is None:
            raise ProviderNotFound(f"Unknown dataset provider {name!r}.")
        return provider
    if name == OPENNEURO_PROVIDER:
        global _openneuro_provider
        if _openneuro_provider is None:
            _openneuro_provider = OpenNeuroProvider(UrllibGraphQLTransport())
        return _openneuro_provider
    raise ProviderNotFound(f"Unknown dataset provider {name!r}.")


class DatasetListItem(BaseModel):
    """One public listing hit; resolving needs an explicit snapshot tag."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    public: bool
    latest_snapshot: str | None = None


class DatasetListResponse(BaseModel):
    """One bounded page of public listing hits with an opaque cursor."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    items: list[DatasetListItem]
    next_cursor: str | None = None
    has_more: bool


def validate_provider_name(name: str) -> str:
    """Strip and strictly validate a provider key.

    Only validated names may be echoed back in 404 responses; anything else
    is rejected here with 422 before any provider runs.
    """

    text = name.strip() if isinstance(name, str) else ""
    if not text or len(text) > 64 or _PROVIDER_NAME_PATTERN.match(text) is None:
        raise HTTPException(
            status_code=422,
            detail="Dataset provider must be 1..64 characters of lowercase letters, "
            "digits, and hyphens.",
        )
    return text


async def list_dataset_page(provider_name: str, search: DatasetSearch) -> DatasetListResponse:
    """List one bounded page of public datasets from ``provider_name``.

    HTTP details are static strings chosen from the exception class: provider
    exception text is never surfaced (it can carry secrets or URLs) and is
    never logged (handled errors stay out of the fault log). ``from None``
    keeps secret-bearing tracebacks off anything serializable.
    """

    try:
        provider = get_dataset_provider(provider_name)
    except ProviderNotFound:
        # provider_name passed validate_provider_name, so echoing it is safe.
        raise HTTPException(
            status_code=404, detail=f"Unknown dataset provider {provider_name}."
        ) from None
    try:
        page: DatasetPage = await provider.list_datasets(search)
    except ProviderNotFound:
        # Unreachable for the shipped providers (listing never reports a
        # missing snapshot), but a defensive 404 beats a misleading 502.
        raise HTTPException(
            status_code=404, detail=f"Unknown dataset provider {provider_name}."
        ) from None
    except ProviderTimeout:
        raise HTTPException(status_code=504, detail=_LIST_TIMEOUT_DETAIL) from None
    except ProviderMalformed:
        raise HTTPException(status_code=502, detail=_LIST_MALFORMED_DETAIL) from None
    except ProviderError:
        raise HTTPException(status_code=502, detail=_LIST_FAILED_DETAIL) from None
    except ValueError:
        raise HTTPException(status_code=422, detail=_LIST_INVALID_DETAIL) from None
    return DatasetListResponse(
        provider=provider.provider_name,
        items=[
            DatasetListItem(
                provider=item.provider,
                dataset_id=item.dataset_id,
                title=item.title,
                public=item.public,
                latest_snapshot=item.latest_snapshot,
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


async def resolve_dataset_snapshot(
    provider_name: str, dataset_id: str, snapshot: str
) -> CatalogEntry:
    """Resolve one explicit immutable snapshot to validated catalog data."""

    try:
        provider = get_dataset_provider(provider_name)
    except ProviderNotFound:
        # provider_name passed validate_provider_name, so echoing it is safe.
        raise HTTPException(
            status_code=404, detail=f"Unknown dataset provider {provider_name}."
        ) from None
    try:
        return await provider.resolve_snapshot(dataset_id, snapshot)
    except ProviderNotFound:
        # dataset_id/snapshot passed API identifier validation, so echo is safe.
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown dataset snapshot {dataset_id}:{snapshot}. "
                "It may not exist, may not be public, or the tag may be wrong."
            ),
        ) from None
    except ProviderTimeout:
        raise HTTPException(status_code=504, detail=_RESOLVE_TIMEOUT_DETAIL) from None
    except ProviderMalformed:
        raise HTTPException(status_code=502, detail=_RESOLVE_MALFORMED_DETAIL) from None
    except ProviderError:
        raise HTTPException(status_code=502, detail=_RESOLVE_FAILED_DETAIL) from None
    except ValueError:
        raise HTTPException(status_code=422, detail=_RESOLVE_INVALID_DETAIL) from None
