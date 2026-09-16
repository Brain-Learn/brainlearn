"""Step 5A.2: provider-neutral source boundary and OpenNeuro public metadata.

Ordinary tests are fully offline: the mock provider is deterministic and
the OpenNeuro adapter is exercised through sanitized minimal fixtures plus
a fake transport. Live network appears only in the explicitly opt-in smoke
test (``BRAINLEARN_LIVE_OPENNEURO=1``); nothing else in this file may open
a socket.
"""

import asyncio
import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    CatalogEntry,
    catalog_entry_identity,
)
from brainlearn_core.openneuro import (
    OPENNEURO_PROVIDER,
    OpenNeuroProvider,
    UrllibGraphQLTransport,
    map_openneuro_snapshot_to_catalog,
)
from brainlearn_core.providers import (
    DatasetSearch,
    MockDatasetProvider,
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_ENV_VAR = "BRAINLEARN_LIVE_OPENNEURO"
needs_live = pytest.mark.skipif(
    os.environ.get(LIVE_ENV_VAR) != "1",
    reason="Opt-in live OpenNeuro smoke test only.",
)


def _make_entry(
    dataset_id: str = "zz10mock-eeg",
    snapshot: str = "2026-09-01",
    *,
    provider: str = "mock-archive",
    modality: str = "EEG",
    title: str = "Mock EEG entry",
    access: str = "public",
) -> CatalogEntry:
    expected_files = [{"path": "dataset_description.json", "byte_size": 512, "sha256": None}]
    identity = catalog_entry_identity(
        provider=provider,
        dataset_id=dataset_id,
        snapshot=snapshot,
        modality=modality,
        task="rest",
        participants=2,
        formats=["BIDS"],
        approximate_total_bytes=512,
        expected_total_bytes=512,
        expected_files=expected_files,
        access=access,
        license_name="Mock public license",
        license_spdx=None,
        reuse_statement="Mock reuse statement.",
        citations=[{"title": "Mock citation.", "doi": None, "url": None}],
        landing_page=f"https://example.invalid/datasets/{dataset_id}",
        compatible_templates=[],
    )
    return CatalogEntry.model_validate(
        {
            "schema_version": "1.0",
            "catalog_identity": identity,
            "provider": provider,
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "title": title,
            "modality": modality,
            "task": "rest",
            "participants": 2,
            "formats": ["BIDS"],
            "approximate_total_bytes": 512,
            "expected_total_bytes": 512,
            "expected_files": expected_files,
            "access": access,
            "license_name": "Mock public license",
            "license_spdx": None,
            "reuse_statement": "Mock reuse statement.",
            "citations": [{"title": "Mock citation.", "doi": None, "url": None}],
            "landing_page": f"https://example.invalid/datasets/{dataset_id}",
            "compatible_templates": [],
            "curator": "",
            "review_status": "pending",
            "reviewed_at": None,
            "limitations": "Mock entry for provider tests.",
        }
    )


def _snapshot_payload() -> dict[str, Any]:
    raw = json.loads((FIXTURES / "openneuro-snapshot-min.json").read_text(encoding="utf-8"))
    assert isinstance(raw["snapshot"], dict)
    return dict(raw["snapshot"])


def _list_connection() -> dict[str, Any]:
    raw = json.loads((FIXTURES / "openneuro-list-min.json").read_text(encoding="utf-8"))
    assert isinstance(raw["datasets"], dict)
    return dict(raw["datasets"])


class FakeTransport:
    """In-memory transport returning canned ``data`` objects or raising."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def execute(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        self.calls.append({"query": query, "variables": dict(variables)})
        result = self._handler(query, dict(variables))
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, dict)
        return result


def test_mock_pagination_uses_opaque_cursors() -> None:
    entries = [_make_entry(f"zz10mock-{index:02d}") for index in range(3)]
    provider = MockDatasetProvider(entries)
    first = asyncio.run(provider.list_datasets(DatasetSearch(first=2)))
    assert [item.dataset_id for item in first.items] == ["zz10mock-00", "zz10mock-01"]
    assert first.has_more is True
    assert first.next_cursor is not None
    assert "zz10mock" not in first.next_cursor
    second = asyncio.run(provider.list_datasets(DatasetSearch(first=2, after=first.next_cursor)))
    assert [item.dataset_id for item in second.items] == ["zz10mock-02"]
    assert second.has_more is False
    assert second.next_cursor is None


def test_mock_empty_results() -> None:
    provider = MockDatasetProvider([_make_entry()])
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10, query="no-such-dataset")))
    assert page.items == ()
    assert page.has_more is False
    assert page.next_cursor is None


def test_mock_query_and_modality_filter() -> None:
    entries = [
        _make_entry("zz10mock-eeg", modality="EEG", title="Meditation EEG"),
        _make_entry("zz11mock-mri", modality="MRI", title="Meditation MRI"),
    ]
    provider = MockDatasetProvider(entries)
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10, modality="eeg")))
    assert [item.dataset_id for item in page.items] == ["zz10mock-eeg"]
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10, query="MEDITATION")))
    assert {item.dataset_id for item in page.items} == {"zz10mock-eeg", "zz11mock-mri"}
    page = asyncio.run(
        provider.list_datasets(DatasetSearch(first=10, query="meditation", modality="MRI"))
    )
    assert [item.dataset_id for item in page.items] == ["zz11mock-mri"]


def test_mock_listing_omits_non_public_with_stable_pagination() -> None:
    entries = [
        _make_entry("zz10pub-a", title="Public A"),
        _make_entry("zz11restricted-b", title="Restricted B", access="restricted"),
        _make_entry("zz12pub-c", title="Public C"),
    ]
    provider = MockDatasetProvider(entries)
    whole = asyncio.run(provider.list_datasets(DatasetSearch(first=10)))
    assert [item.dataset_id for item in whole.items] == ["zz10pub-a", "zz12pub-c"]
    assert all(item.public for item in whole.items)
    first = asyncio.run(provider.list_datasets(DatasetSearch(first=1)))
    assert [item.dataset_id for item in first.items] == ["zz10pub-a"]
    assert first.has_more is True
    assert first.next_cursor is not None
    second = asyncio.run(provider.list_datasets(DatasetSearch(first=1, after=first.next_cursor)))
    assert [item.dataset_id for item in second.items] == ["zz12pub-c"]
    assert second.has_more is False
    assert second.next_cursor is None
    # A query matching only the restricted title still reports nothing.
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10, query="Restricted B")))
    assert page.items == ()


def test_mock_resolve_non_public_is_not_found() -> None:
    provider = MockDatasetProvider([_make_entry("zz11restricted", access="restricted")])
    with pytest.raises(ProviderNotFound, match="not public"):
        asyncio.run(provider.resolve_snapshot("zz11restricted", "2026-09-01"))


def test_mock_resolve_returns_validated_entry() -> None:
    entry = _make_entry()
    provider = MockDatasetProvider([entry])
    resolved = asyncio.run(provider.resolve_snapshot(entry.dataset_id, entry.snapshot))
    assert resolved == entry
    assert resolved.catalog_identity == entry.catalog_identity


def test_mock_resolve_unknown_raises_not_found() -> None:
    provider = MockDatasetProvider([_make_entry()])
    with pytest.raises(ProviderNotFound, match="Unknown dataset snapshot"):
        asyncio.run(provider.resolve_snapshot("zz99missing", "2026-09-01"))


def test_mock_malformed_entry_raises_malformed() -> None:
    raw: dict[str, Any] = {
        "schema_version": "1.0",
        "catalog_identity": "brainlearn-v1:dataset:" + "0" * 64,
        "provider": "mock-archive",
        "dataset_id": "zz10broken",
        "snapshot": "2026-09-01",
        # title is missing, so catalog validation must fail on resolution.
        "modality": "EEG",
        "expected_total_bytes": 1,
        "expected_files": [],
        "license_name": "Mock",
        "reuse_statement": "Mock",
        "citations": [],
        "landing_page": "https://example.invalid/datasets/zz10broken",
    }
    provider = MockDatasetProvider([raw])
    with pytest.raises(ProviderMalformed, match="failed catalog validation"):
        asyncio.run(provider.resolve_snapshot("zz10broken", "2026-09-01"))


def test_mock_fail_modes_cover_list_and_resolve() -> None:
    for fail_mode, expected in [
        ("error", ProviderError),
        ("timeout", ProviderTimeout),
        ("malformed", ProviderMalformed),
    ]:
        provider = MockDatasetProvider([_make_entry()], fail_mode=fail_mode)  # type: ignore[arg-type]
        with pytest.raises(expected):
            asyncio.run(provider.list_datasets(DatasetSearch(first=1)))
        with pytest.raises(expected):
            asyncio.run(provider.resolve_snapshot("zz10mock-eeg", "2026-09-01"))


def test_mock_timeout_is_cancellable_not_silent() -> None:
    provider = MockDatasetProvider([_make_entry()], delay_s=30.0)

    async def _main() -> bool:
        task = asyncio.ensure_future(provider.list_datasets(DatasetSearch(first=1)))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(_main()) is True


def test_mock_rejects_bad_page_size_and_cursor() -> None:
    provider = MockDatasetProvider([_make_entry()])
    with pytest.raises(ValueError, match="first must be within"):
        asyncio.run(provider.list_datasets(DatasetSearch(first=0)))
    with pytest.raises(ValueError, match="first must be within"):
        asyncio.run(provider.list_datasets(DatasetSearch(first=51)))
    with pytest.raises(ProviderMalformed, match="Unknown listing cursor"):
        asyncio.run(provider.list_datasets(DatasetSearch(first=1, after="not-a-cursor")))


def test_mock_never_persists_secrets() -> None:
    raw: dict[str, Any] = {
        "schema_version": "1.0",
        "catalog_identity": "brainlearn-v1:dataset:" + "0" * 64,
        "provider": "mock-archive",
        "dataset_id": "zz10secret",
        "snapshot": "2026-09-01",
        "title": "Secret entry",
        "modality": "EEG",
        "expected_total_bytes": 1,
        "expected_files": [{"path": "a.txt", "byte_size": 1}],
        "license_name": "Mock",
        "reuse_statement": "Mock",
        "citations": [{"title": "Mock.", "doi": None, "url": None}],
        "landing_page": "https://example.invalid/datasets/zz10secret",
        # The contract forbids unknown fields, so secrets cannot persist.
        "download_url": "https://example.invalid/secret?token=abc",
        "api_token": "hunter2",
    }
    provider = MockDatasetProvider([raw])
    with pytest.raises(ProviderMalformed, match="failed catalog validation"):
        asyncio.run(provider.resolve_snapshot("zz10secret", "2026-09-01"))
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10)))
    assert "token" not in repr(page).lower()


def test_openneuro_snapshot_maps_to_pending_catalog() -> None:
    entry = map_openneuro_snapshot_to_catalog(_snapshot_payload())
    assert entry.provider == OPENNEURO_PROVIDER
    assert entry.dataset_id == "ds000001"
    assert entry.snapshot == "1.0.0"
    assert entry.review_status == "pending"
    assert entry.curator == ""
    assert entry.reviewed_at is None
    assert entry.title == "Balloon Analog Risk-taking Task"
    assert entry.modality == "MRI"
    assert entry.task == "balloon analog risk task"
    assert entry.participants == 2
    assert list(entry.formats) == ["BIDS"]
    assert entry.access == "public"
    assert entry.license_name == "CC0"
    assert entry.license_spdx is None
    assert entry.landing_page == "https://openneuro.org/datasets/ds000001/versions/1.0.0"
    assert [item.path for item in entry.expected_files] == [
        "CHANGES",
        "README",
        "dataset_description.json",
        "participants.tsv",
    ]
    assert all(item.sha256 is None for item in entry.expected_files)
    assert entry.expected_total_bytes == 4096
    assert "Unverified OpenNeuro" in entry.limitations
    assert "pending curator review" in entry.limitations
    assert entry.citations[0].doi == "10.18112/openneuro.ds000001.v1.0.0"
    # Identity recomputes from mapped facts; the record round-trips.
    assert entry.catalog_identity == catalog_entry_identity(
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
    assert CatalogEntry.model_validate_json(entry.model_dump_json()) == entry


def test_openneuro_mapping_never_persists_transfer_or_secrets() -> None:
    entry = map_openneuro_snapshot_to_catalog(_snapshot_payload())
    dumped = entry.model_dump_json()
    lowered = dumped.lower()
    for forbidden in ("token", "cookie", "secret", "signature", "download_url", '"urls"'):
        assert forbidden not in lowered
    assert entry.citations[0].url is None


def test_openneuro_doi_prefix_is_stripped() -> None:
    payload = _snapshot_payload()
    assert isinstance(payload["description"], dict)
    payload["description"] = dict(
        payload["description"], DatasetDOI="doi:10.18112/openneuro.ds000001.v1.0.0"
    )
    entry = map_openneuro_snapshot_to_catalog(payload)
    assert entry.citations[0].doi == "10.18112/openneuro.ds000001.v1.0.0"


def test_openneuro_unparseable_doi_is_dropped_with_a_note() -> None:
    payload = _snapshot_payload()
    assert isinstance(payload["description"], dict)
    payload["description"] = dict(payload["description"], DatasetDOI="not-a-doi")
    entry = map_openneuro_snapshot_to_catalog(payload)
    assert entry.citations[0].doi is None
    assert "did not parse" in entry.limitations


def test_openneuro_missing_doi_stays_pending() -> None:
    payload = _snapshot_payload()
    assert isinstance(payload["description"], dict)
    payload["description"] = dict(payload["description"], DatasetDOI=None)
    entry = map_openneuro_snapshot_to_catalog(payload)
    assert entry.citations[0].doi is None
    assert entry.review_status == "pending"


def test_openneuro_nonpublic_snapshot_is_not_found() -> None:
    payload = _snapshot_payload()
    assert isinstance(payload["dataset"], dict)
    payload["dataset"] = dict(payload["dataset"], public=False)
    with pytest.raises(ProviderNotFound, match="not public"):
        map_openneuro_snapshot_to_catalog(payload)


def test_openneuro_snapshot_id_mismatch_is_malformed() -> None:
    payload = _snapshot_payload()
    payload["id"] = "ds000001:9.9.9"
    with pytest.raises(ProviderMalformed, match="does not match"):
        map_openneuro_snapshot_to_catalog(payload)


def test_openneuro_empty_files_is_malformed() -> None:
    payload = _snapshot_payload()
    payload["files"] = [{"filename": "sub-01", "size": 0, "directory": True}]
    with pytest.raises(ProviderMalformed, match="no root files"):
        map_openneuro_snapshot_to_catalog(payload)


def test_openneuro_missing_size_is_malformed() -> None:
    payload = _snapshot_payload()
    assert isinstance(payload["summary"], dict)
    payload["summary"] = dict(payload["summary"], size=0)
    with pytest.raises(ProviderMalformed, match="no usable size"):
        map_openneuro_snapshot_to_catalog(payload)


def test_openneuro_missing_description_is_malformed() -> None:
    payload = _snapshot_payload()
    payload["description"] = None
    with pytest.raises(ProviderMalformed, match="no description"):
        map_openneuro_snapshot_to_catalog(payload)


def test_openneuro_provider_resolves_through_transport() -> None:
    transport = FakeTransport(lambda _q, _v: {"snapshot": _snapshot_payload()})
    provider = OpenNeuroProvider(transport)
    assert provider.provider_name == OPENNEURO_PROVIDER
    entry = asyncio.run(provider.resolve_snapshot("ds000001", "1.0.0"))
    assert entry.dataset_id == "ds000001"
    assert entry.snapshot == "1.0.0"
    assert transport.calls and "snapshot(datasetId" in transport.calls[0]["query"]
    assert transport.calls[0]["variables"] == {"datasetId": "ds000001", "tag": "1.0.0"}


def test_openneuro_provider_unknown_snapshot_is_not_found() -> None:
    transport = FakeTransport(lambda _q, _v: {"snapshot": None})
    provider = OpenNeuroProvider(transport)
    with pytest.raises(ProviderNotFound, match="Unknown OpenNeuro snapshot"):
        asyncio.run(provider.resolve_snapshot("ds000001", "9.9.9"))


def _substituted_payload(dataset_id: str, tag: str) -> dict[str, Any]:
    """An internally consistent payload describing the wrong snapshot."""
    payload = _snapshot_payload()
    assert isinstance(payload["dataset"], dict)
    payload["dataset"] = dict(payload["dataset"], id=dataset_id)
    payload["id"] = f"{dataset_id}:{tag}"
    payload["tag"] = tag
    return payload


def test_openneuro_provider_rejects_substituted_dataset_id() -> None:
    transport = FakeTransport(
        lambda _q, _v: {"snapshot": _substituted_payload("ds999999", "1.0.0")}
    )
    provider = OpenNeuroProvider(transport)
    with pytest.raises(ProviderMalformed, match="for requested ds000001:1.0.0"):
        asyncio.run(provider.resolve_snapshot("ds000001", "1.0.0"))


def test_openneuro_provider_rejects_substituted_tag() -> None:
    transport = FakeTransport(
        lambda _q, _v: {"snapshot": _substituted_payload("ds000001", "2.0.0")}
    )
    provider = OpenNeuroProvider(transport)
    with pytest.raises(ProviderMalformed, match="for requested ds000001:1.0.0"):
        asyncio.run(provider.resolve_snapshot("ds000001", "1.0.0"))


def test_openneuro_provider_rejects_bad_identifiers_without_network() -> None:
    transport = FakeTransport(lambda _q, _v: {"snapshot": None})
    provider = OpenNeuroProvider(transport)
    with pytest.raises(ValueError, match="portable identifier"):
        asyncio.run(provider.resolve_snapshot("has space", "1.0.0"))
    with pytest.raises(ValueError, match="portable identifier"):
        asyncio.run(provider.resolve_snapshot("ds000001", "../escape"))
    assert transport.calls == []


def test_openneuro_list_parses_fixture_connection() -> None:
    connection = _list_connection()
    transport = FakeTransport(lambda _q, _v: {"datasets": connection})
    provider = OpenNeuroProvider(transport)
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10)))
    assert [item.dataset_id for item in page.items] == ["ds000001", "ds001787"]
    assert page.items[0].latest_snapshot == "1.0.0"
    assert page.items[1].title == "EEG meditation study"
    assert all(item.provider == OPENNEURO_PROVIDER for item in page.items)
    assert page.has_more is True
    assert page.next_cursor == "eyJvZmZzZXQiOjJ9"


def test_openneuro_list_query_filters_page_locally() -> None:
    transport = FakeTransport(lambda _q, _v: {"datasets": _list_connection()})
    provider = OpenNeuroProvider(transport)
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10, query="meditation")))
    assert [item.dataset_id for item in page.items] == ["ds001787"]


def test_openneuro_list_modality_is_lowercased_server_side() -> None:
    transport = FakeTransport(lambda _q, _v: {"datasets": _list_connection()})
    provider = OpenNeuroProvider(transport)
    asyncio.run(provider.list_datasets(DatasetSearch(first=5, modality="EEG")))
    assert transport.calls[0]["variables"]["modality"] == "eeg"


def test_openneuro_list_rejects_bad_connection_and_page_size() -> None:
    provider = OpenNeuroProvider(FakeTransport(lambda _q, _v: {"datasets": {"edges": []}}))
    with pytest.raises(ProviderMalformed, match="edges/pageInfo"):
        asyncio.run(provider.list_datasets(DatasetSearch(first=5)))
    provider = OpenNeuroProvider(FakeTransport(lambda _q, _v: {"datasets": _list_connection()}))
    with pytest.raises(ValueError, match="first must be within"):
        asyncio.run(provider.list_datasets(DatasetSearch(first=26)))


def test_openneuro_listing_omits_non_public_nodes() -> None:
    connection = _list_connection()
    edges = list(connection["edges"])
    edges.insert(
        1,
        {
            "cursor": "cHJpdmF0ZQ==",
            "node": {
                "id": "ds009999",
                "public": False,
                "name": "Private meditation study",
                "latestSnapshot": {"tag": "1.0.0"},
            },
        },
    )
    connection = dict(connection, edges=edges)
    transport = FakeTransport(lambda _q, _v: {"datasets": connection})
    provider = OpenNeuroProvider(transport)
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10)))
    assert [item.dataset_id for item in page.items] == ["ds000001", "ds001787"]
    assert all(item.public for item in page.items)
    # Server pagination still passes through untouched.
    assert page.has_more is True
    assert page.next_cursor == "eyJvZmZzZXQiOjJ9"
    # A query matching only the private title reports nothing.
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=10, query="Private meditation")))
    assert page.items == ()


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _install_urlopen(
    monkeypatch: pytest.MonkeyPatch, body: bytes | BaseException, status: int = 200
) -> dict[str, Any]:
    # Patch at the opener level so the redirect-policy loop stays in the
    # exercised path; plain bodies answer 200 while error objects raise.
    seen: dict[str, Any] = {}

    def _fake(self: Any, request: Any, data: Any = None, timeout: Any = None) -> Any:
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        if isinstance(body, BaseException):
            raise body
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "injected", {}, None)
        return _FakeResponse(body, status)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _fake)
    return seen


def test_transport_sends_public_post_with_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps({"data": {"datasets": _list_connection()}}).encode()
    seen = _install_urlopen(monkeypatch, payload)
    transport = UrllibGraphQLTransport(timeout_s=7.5)
    data = asyncio.run(transport.execute("{ datasets { edges { node { id } } } }", {"first": 2}))
    assert list(data) == ["datasets"]
    assert seen["url"] == "https://openneuro.org/crn/graphql"
    lowered = {key.lower(): value for key, value in seen["headers"].items()}
    assert "authorization" not in lowered
    assert "cookie" not in lowered
    assert seen["timeout"] == 7.5


def test_transport_rejects_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"data": {"snapshot": None}}).encode()
    _install_urlopen(monkeypatch, body + b" " * 64)
    transport = UrllibGraphQLTransport(max_bytes=8)
    with pytest.raises(ProviderMalformed, match="exceeded"):
        asyncio.run(transport.execute("{ snapshot }", {}))


def test_transport_maps_http_timeout_and_graphql_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, TimeoutError("slow"))
    with pytest.raises(ProviderTimeout, match="did not answer"):
        asyncio.run(UrllibGraphQLTransport().execute("{ x }", {}))

    http_error = urllib.error.HTTPError("https://openneuro.org/crn/graphql", 500, "boom", {}, None)
    _install_urlopen(monkeypatch, http_error)
    with pytest.raises(ProviderError, match="HTTP 500"):
        asyncio.run(UrllibGraphQLTransport().execute("{ x }", {}))

    errors = json.dumps({"errors": [{"message": "bad query"}], "data": {}}).encode()
    _install_urlopen(monkeypatch, errors)
    with pytest.raises(ProviderError, match="bad query"):
        asyncio.run(UrllibGraphQLTransport().execute("{ x }", {}))


def test_transport_maps_wrapped_timeouts_to_provider_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_urlopen(monkeypatch, urllib.error.URLError(TimeoutError("timed out")))
    with pytest.raises(ProviderTimeout, match="did not answer"):
        asyncio.run(UrllibGraphQLTransport().execute("{ x }", {}))

    # socket.timeout is the same class as TimeoutError; pin the alias so the
    # review's wrapped-timeout case stays covered even if that ever changes.
    assert socket.timeout is TimeoutError
    _install_urlopen(
        monkeypatch,
        urllib.error.URLError(socket.timeout("timed out")),  # noqa: UP041
        # Intentionally the socket.timeout spelling: REVIEW.md requires a
        # wrapped-socket-timeout regression alongside the TimeoutError one.
    )
    with pytest.raises(ProviderTimeout, match="did not answer"):
        asyncio.run(UrllibGraphQLTransport().execute("{ x }", {}))

    _install_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    with pytest.raises(ProviderError, match="request failed"):
        asyncio.run(UrllibGraphQLTransport().execute("{ x }", {}))


def test_transport_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _slow(self: Any, request: Any, data: Any = None, timeout: Any = None) -> Any:
        time.sleep(2.0)
        return _FakeResponse(json.dumps({"data": {}}).encode())

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _slow)

    async def _main() -> bool:
        task = asyncio.ensure_future(UrllibGraphQLTransport().execute("{ x }", {}))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(_main()) is True


@needs_live
def test_live_openneuro_snapshot_smoke() -> None:
    """Opt-in live probe: resolve the documented ds000001:1.0.0 snapshot.

    Run only with ``BRAINLEARN_LIVE_OPENNEURO=1``; the normal suite never
    touches the network. The pinned snapshot comes straight from the
    official API documentation.
    """

    provider = OpenNeuroProvider(UrllibGraphQLTransport(timeout_s=20.0))
    page = asyncio.run(provider.list_datasets(DatasetSearch(first=1, modality="eeg")))
    assert len(page.items) == 1
    entry = asyncio.run(provider.resolve_snapshot("ds000001", "1.0.0"))
    assert entry.provider == OPENNEURO_PROVIDER
    assert entry.dataset_id == "ds000001"
    assert entry.snapshot == "1.0.0"
    assert entry.review_status == "pending"
    assert "token" not in entry.model_dump_json().lower()
