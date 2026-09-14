"""Step 5A.3: read-only dataset-library API endpoints, fully offline.

Every test injects a deterministic ``MockDatasetProvider``; no test in this
file may open a socket. Project awareness is enforced through the same
explicit-root authorization as run records.
"""

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from brainlearn_core import (
    CatalogEntry,
    DatasetPage,
    DatasetSearch,
    MockDatasetProvider,
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
    catalog_entry_identity,
)
from brainlearn_server import project_store as store_module
from brainlearn_server.app import app, store
from brainlearn_server.auth import reset_session_token_for_tests
from brainlearn_server.datasets import set_dataset_providers_for_tests
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"

TEST_TOKEN = "step5a3-test-token-0123456789abcdef"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_session_token_for_tests(TEST_TOKEN)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(store_module, "_default_state_dir", lambda: state_dir)
    store.state_dir = state_dir
    store.allowed_roots.clear()
    set_dataset_providers_for_tests(None)
    yield
    store.allowed_roots.clear()
    set_dataset_providers_for_tests(None)
    reset_session_token_for_tests(TEST_TOKEN)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _make_entry(
    dataset_id: str,
    snapshot: str = "2026-09-01",
    *,
    access: str = "public",
    modality: str = "EEG",
    title: str | None = None,
) -> CatalogEntry:
    expected_files = [{"path": "dataset_description.json", "byte_size": 512, "sha256": None}]
    identity = catalog_entry_identity(
        provider="mock-archive",
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
            "provider": "mock-archive",
            "dataset_id": dataset_id,
            "snapshot": snapshot,
            "title": title or f"Mock {dataset_id}",
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
            "limitations": "Mock entry for library tests.",
        }
    )


def _make_project(tmp_path: Path, name: str = "library") -> Path:
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "brainlearn.project.json").write_text(
        json.dumps({"project_schema_version": "1.0", "id": name, "name": name}),
        encoding="utf-8",
    )
    store.register_root(project)
    return project


def _use_mock(entries: list[Any], **kwargs: Any) -> None:
    set_dataset_providers_for_tests({"mock-archive": MockDatasetProvider(entries, **kwargs)})


def test_list_requires_token_and_project(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _use_mock([_make_entry("zz10a")])
    params = {"path": str(project), "provider": "mock-archive"}

    unauthorized = client.get("/api/datasets", params=params)
    assert unauthorized.status_code == 401

    outside = client.get(
        "/api/datasets", params={**params, "path": str(tmp_path / "nope")}, headers=AUTH_HEADERS
    )
    assert outside.status_code == 403
    assert "explicitly opened" in outside.json()["detail"]

    relative = client.get(
        "/api/datasets", params={**params, "path": "relative/path"}, headers=AUTH_HEADERS
    )
    assert relative.status_code == 400

    unregistered = client.get(
        "/api/datasets",
        params={**params, "path": str(tmp_path / "other")},
        headers=AUTH_HEADERS,
    )
    assert unregistered.status_code == 403


def test_list_returns_public_page_shape(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _use_mock(
        [
            _make_entry("zz10a", title="Alpha EEG"),
            _make_entry("zz11restricted", access="restricted"),
            _make_entry("zz12b", title="Beta EEG"),
        ]
    )
    response = client.get(
        "/api/datasets",
        params={"path": str(project), "provider": "mock-archive"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "mock-archive"
    assert [item["dataset_id"] for item in body["items"]] == ["zz10a", "zz12b"]
    assert all(item["public"] is True for item in body["items"])
    assert body["items"][0]["latest_snapshot"] == "2026-09-01"
    assert body["next_cursor"] is None
    assert body["has_more"] is False


def test_list_paginates_searches_and_filters(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _use_mock(
        [
            _make_entry("zz10meditation", modality="EEG", title="Meditation EEG"),
            _make_entry("zz11rest", modality="EEG", title="Rest EEG"),
            _make_entry("zz12bold", modality="EEG", title="Bold MRI"),
        ]
    )
    base = {"path": str(project), "provider": "mock-archive", "first": 1}

    first = client.get("/api/datasets", params=base, headers=AUTH_HEADERS)
    assert first.status_code == 200
    assert [item["dataset_id"] for item in first.json()["items"]] == ["zz10meditation"]
    assert first.json()["has_more"] is True
    cursor = first.json()["next_cursor"]
    assert cursor

    second = client.get("/api/datasets", params={**base, "after": cursor}, headers=AUTH_HEADERS)
    assert second.status_code == 200
    assert [item["dataset_id"] for item in second.json()["items"]] == ["zz11rest"]

    searched = client.get(
        "/api/datasets",
        params={**base, "query": "meditation"},
        headers=AUTH_HEADERS,
    )
    assert searched.status_code == 200
    assert [item["dataset_id"] for item in searched.json()["items"]] == ["zz10meditation"]

    modality = client.get(
        "/api/datasets",
        params={"path": str(project), "provider": "mock-archive", "modality": "eeg"},
        headers=AUTH_HEADERS,
    )
    assert modality.status_code == 200
    assert {item["dataset_id"] for item in modality.json()["items"]} == {
        "zz10meditation",
        "zz11rest",
        "zz12bold",
    }

    empty = client.get(
        "/api/datasets",
        params={**base, "query": "no-such-dataset"},
        headers=AUTH_HEADERS,
    )
    assert empty.status_code == 200
    assert empty.json() == {
        "provider": "mock-archive",
        "items": [],
        "next_cursor": None,
        "has_more": False,
    }


def test_list_rejects_bad_input(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _use_mock([_make_entry("zz10a")])
    base = {"path": str(project), "provider": "mock-archive"}

    assert (
        client.get("/api/datasets", params={**base, "first": 0}, headers=AUTH_HEADERS).status_code
        == 422
    )
    assert (
        client.get("/api/datasets", params={**base, "first": 51}, headers=AUTH_HEADERS).status_code
        == 422
    )
    long_query = client.get(
        "/api/datasets", params={**base, "query": "x" * 201}, headers=AUTH_HEADERS
    )
    assert long_query.status_code == 422

    unknown = client.get(
        "/api/datasets", params={**base, "provider": "no-such-provider"}, headers=AUTH_HEADERS
    )
    assert unknown.status_code == 404
    assert "Unknown dataset provider" in unknown.json()["detail"]


def test_list_maps_provider_failures_without_leaks(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    base = {"path": str(project), "provider": "mock-archive"}

    _use_mock([_make_entry("zz10a")], fail_mode="timeout")
    timeout = client.get("/api/datasets", params=base, headers=AUTH_HEADERS)
    assert timeout.status_code == 504
    assert "Retry" in timeout.json()["detail"]

    _use_mock([_make_entry("zz10a")], fail_mode="error")
    error = client.get("/api/datasets", params=base, headers=AUTH_HEADERS)
    assert error.status_code == 502

    _use_mock([_make_entry("zz10a")], fail_mode="malformed")
    malformed = client.get("/api/datasets", params=base, headers=AUTH_HEADERS)
    assert malformed.status_code == 502

    for body in (timeout.json(), error.json(), malformed.json()):
        lowered = json.dumps(body).lower()
        for forbidden in ("token", "secret", "cookie", "traceback", "urlopen", "raw_response"):
            assert forbidden not in lowered


def test_resolve_returns_pending_catalog_entry(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _use_mock([_make_entry("zz10a", title="Alpha EEG")])
    response = client.get(
        "/api/datasets/mock-archive/zz10a/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["provider"] == "mock-archive"
    assert body["dataset_id"] == "zz10a"
    assert body["snapshot"] == "2026-09-01"
    assert body["review_status"] == "pending"
    assert body["limitations"] == "Mock entry for library tests."
    assert body["expected_files"] == [
        {"path": "dataset_description.json", "byte_size": 512, "sha256": None}
    ]
    assert "download" not in json.dumps(body).lower()
    assert "token" not in json.dumps(body).lower()


def test_resolve_rejects_unknown_restricted_and_bad_ids(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _use_mock([_make_entry("zz11restricted", access="restricted")])

    missing = client.get(
        "/api/datasets/mock-archive/zz99missing/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert missing.status_code == 404

    restricted = client.get(
        "/api/datasets/mock-archive/zz11restricted/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert restricted.status_code == 404

    bad_id = client.get(
        "/api/datasets/mock-archive/has%20space/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert bad_id.status_code == 422
    assert "portable identifier" in bad_id.json()["detail"]

    no_token = client.get(
        "/api/datasets/mock-archive/zz11restricted/2026-09-01",
        params={"path": str(project)},
    )
    assert no_token.status_code == 401

    outside = client.get(
        "/api/datasets/mock-archive/zz11restricted/2026-09-01",
        params={"path": str(tmp_path / "elsewhere")},
        headers=AUTH_HEADERS,
    )
    assert outside.status_code == 403


def test_library_endpoints_stay_offline_and_write_nothing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _make_project(tmp_path)
    _use_mock([_make_entry("zz10a")])
    before = sorted(path.name for path in project.iterdir())

    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dataset library tests must stay offline")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    listed = client.get(
        "/api/datasets",
        params={"path": str(project), "provider": "mock-archive"},
        headers=AUTH_HEADERS,
    )
    resolved = client.get(
        "/api/datasets/mock-archive/zz10a/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert listed.status_code == 200
    assert resolved.status_code == 200
    assert sorted(path.name for path in project.iterdir()) == before


class _HostileProvider:
    """Provider double whose exception text carries secrets and URLs.

    Every HTTP response built from these failures must be a static message:
    nothing hostile may reach the client.
    """

    SECRET = "token=SUPERSECRET api_token=SUPERSECRET https://evil.invalid/x?sig=SUPERSECRET"

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    @property
    def provider_name(self) -> str:
        return "hostile-archive"

    async def list_datasets(self, search: DatasetSearch) -> DatasetPage:
        raise self._failure

    async def resolve_snapshot(self, dataset_id: str, snapshot_tag: str) -> CatalogEntry:
        raise self._failure


def _use_hostile(failure: Exception) -> None:
    set_dataset_providers_for_tests({"hostile-archive": _HostileProvider(failure)})


def _assert_leak_free(body: object) -> None:
    lowered = json.dumps(body).lower()
    assert "supersecret" not in lowered
    assert "evil.invalid" not in lowered


def test_hostile_list_failures_stay_static(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    params = {"path": str(project), "provider": "hostile-archive"}
    cases = [
        (ProviderError(_HostileProvider.SECRET), 502),
        (ProviderMalformed(_HostileProvider.SECRET), 502),
        (ProviderTimeout(_HostileProvider.SECRET), 504),
        (ProviderNotFound(_HostileProvider.SECRET), 404),
        (ValueError(_HostileProvider.SECRET), 422),
    ]
    for failure, status in cases:
        _use_hostile(failure)
        response = client.get("/api/datasets", params=params, headers=AUTH_HEADERS)
        assert response.status_code == status
        _assert_leak_free(response.json())


def test_hostile_resolve_failures_stay_static(client: TestClient, tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    url = "/api/datasets/hostile-archive/zz10a/2026-09-01"
    params = {"path": str(project)}
    cases = [
        (ProviderError(_HostileProvider.SECRET), 502),
        (ProviderMalformed(_HostileProvider.SECRET), 502),
        (ProviderTimeout(_HostileProvider.SECRET), 504),
        (ProviderNotFound(_HostileProvider.SECRET), 404),
        (ValueError(_HostileProvider.SECRET), 422),
    ]
    for failure, status in cases:
        _use_hostile(failure)
        response = client.get(url, params=params, headers=AUTH_HEADERS)
        assert response.status_code == status
        body = response.json()
        _assert_leak_free(body)
        if status == 404:
            # Only validated identifiers survive, naming the requested snapshot.
            assert body["detail"].startswith("Unknown dataset snapshot zz10a:2026-09-01.")


def test_hostile_provider_name_is_rejected_before_lookup(
    client: TestClient, tmp_path: Path
) -> None:
    project = _make_project(tmp_path)
    _use_mock([_make_entry("zz10a")])
    evil = "../../x?token=SUPERSECRET"
    listed = client.get(
        "/api/datasets",
        params={"path": str(project), "provider": evil},
        headers=AUTH_HEADERS,
    )
    assert listed.status_code == 422
    _assert_leak_free(listed.json())

    resolved = client.get(
        "/api/datasets/evil%20provider/zz10a/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert resolved.status_code == 422
    _assert_leak_free(resolved.json())

    slashed = client.get(
        f"/api/datasets/{evil}/zz10a/2026-09-01",
        params={"path": str(project)},
        headers=AUTH_HEADERS,
    )
    assert slashed.status_code == 404
    _assert_leak_free(slashed.json())
