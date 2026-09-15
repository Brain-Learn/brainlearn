"""OpenNeuro public metadata adapter (read-only, metadata only, no downloads).

The adapter sits behind :class:`DatasetProvider` and translates the
official OpenNeuro GraphQL API into schema-``1.0`` catalog records.
Transport and mapping are separate: any ``GraphQLTransport`` can serve
bytes (the default uses only the standard library with bounded timeouts
and response sizes), while :func:`map_openneuro_snapshot_to_catalog` is a
pure function over an already-fetched ``snapshot`` payload, which is what
the fixture tests exercise. Ordinary CI never touches the network.

Provenance and limits are documented in ``docs/openneuro-provider.md``:

- Endpoint ``https://openneuro.org/crn/graphql`` per
  ``https://docs.openneuro.org/api.html``; observed server version
  ``5.6.0`` on 2026-09-14.
- Only the ``datasets`` listing and the ``snapshot(datasetId, tag)``
  snapshot query are used. The ``search`` field currently resolves to
  ``null`` and ``advancedSearch`` errors on edge cursors, so free-text
  ``query`` filtering is page-local and documented as such.
- Only immutable snapshots (explicit ``tag``) are mapped, and only
  public ones. License, citation, checksums, and template compatibility
  stay pending curator verification; nothing infers approval.
- File ``urls`` are never requested and transfer endpoints, credentials,
  tokens, cookies, and raw responses are never persisted.
- Snapshot file bytes stream from
  ``https://openneuro.org/crn/datasets/<id>/snapshots/<tag>/files/<path>``
  (same host only; cross-host redirects are refused). The endpoint ignores
  ``Range`` requests (probed 2026-09-14: ``200`` with the full body), so
  resume restarts the in-progress file from zero.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from typing import Any, Protocol

from brainlearn_core.datasets import CatalogEntry, catalog_entry_identity
from brainlearn_core.providers import (
    DatasetPage,
    DatasetSearch,
    ProviderDatasetRef,
    ProviderError,
    ProviderMalformed,
    ProviderNotFound,
    ProviderTimeout,
    RangeUnsupportedError,
)

OPENNEURO_PROVIDER = "openneuro"
OPENNEURO_ENDPOINT = "https://openneuro.org/crn/graphql"
OPENNEURO_DOCS_URL = "https://docs.openneuro.org/api.html"
OPENNEURO_OBSERVED_VERSION = "5.6.0"
OPENNEURO_USER_AGENT = "BrainLearn/5A.2 (read-only metadata; no downloads)"
OPENNEURO_DOWNLOAD_USER_AGENT = "BrainLearn/5A.4 (verified dataset download)"
OPENNEURO_FILES_URL_TEMPLATE = (
    "https://openneuro.org/crn/datasets/{dataset_id}/snapshots/{snapshot}/files/{path}"
)
DOWNLOAD_TIMEOUT_S = 30.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_S = 10.0
MAX_RESPONSE_BYTES = 1_000_000
MAX_REQUEST_BYTES = 65_536
MAX_OPENNEURO_PAGE = 25

_LIST_QUERY = """query($first: Int, $after: String, $modality: String) {
  datasets(first: $first, after: $after, modality: $modality) {
    edges { cursor node { id public name latestSnapshot { tag } } }
    pageInfo { hasNextPage endCursor }
  }
}"""

_SNAPSHOT_QUERY = """query($datasetId: ID!, $tag: String!) {
  snapshot(datasetId: $datasetId, tag: $tag) {
    id tag created hexsha size
    dataset { id public name }
    description { Name DatasetDOI License Authors ReferencesAndLinks BIDSVersion }
    summary { modalities primaryModality tasks size totalFiles subjects }
    files { filename size directory }
  }
}"""

_DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DOI_PRECHECK = re.compile(r"^10\.\d{4,}/\S+$")


class GraphQLTransport(Protocol):
    """Async source of decoded GraphQL ``data`` objects."""

    async def execute(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """POST one query and return the ``data`` object (errors raise)."""
        ...


class UrllibGraphQLTransport:
    """Standard-library GraphQL transport with bounded time and size.

    No authentication is ever attached: only public metadata is readable.
    Oversized bodies are rejected before JSON parsing so a large file tree
    can never become an in-memory surprise.
    """

    def __init__(
        self,
        *,
        endpoint: str = OPENNEURO_ENDPOINT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_bytes: int = MAX_RESPONSE_BYTES,
        user_agent: str = OPENNEURO_USER_AGENT,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s}.")
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive, got {max_bytes}.")
        self._endpoint = endpoint
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes
        self._user_agent = user_agent

    async def execute(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """POST ``query`` and return its ``data`` object."""
        body = json.dumps({"query": query, "variables": dict(variables)}).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise ProviderMalformed("GraphQL request exceeds the metadata-only size bound.")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        try:
            raw = await asyncio.to_thread(self._post, body, headers)
        except TimeoutError:
            raise ProviderTimeout(
                f"OpenNeuro did not answer within {self._timeout_s:g}s."
            ) from None
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"OpenNeuro answered HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            # urllib commonly wraps a socket timeout as URLError; TimeoutError
            # already covers socket.timeout (same class since Python 3.10).
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout(
                    f"OpenNeuro did not answer within {self._timeout_s:g}s."
                ) from exc
            raise ProviderError(f"OpenNeuro request failed: {exc.reason}.") from exc
        if len(raw) > self._max_bytes:
            raise ProviderMalformed(
                f"OpenNeuro response exceeded the {self._max_bytes}-byte metadata bound."
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderMalformed(f"OpenNeuro response was not valid JSON: {exc}.") from exc
        if not isinstance(payload, dict):
            raise ProviderMalformed("OpenNeuro response was not a JSON object.")
        errors = payload.get("errors")
        if errors:
            message = errors[0].get("message") if isinstance(errors[0], dict) else None
            detail = str(message)[:160] if message else "unknown GraphQL error"
            raise ProviderError(f"OpenNeuro query failed: {detail}.")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderMalformed("OpenNeuro response carried no data object.")
        return data

    def _post(self, body: bytes, headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(self._endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ProviderError(f"OpenNeuro answered HTTP {status}.")
            raw: bytes = response.read(self._max_bytes + 1)
            return raw


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse file-download redirects that leave the approved host.

    Full redirect hardening (allowlist policy, redirect chains, archive
    endpoints) belongs to the later hardening slice; this guard keeps the
    downloader on ``https://openneuro.org`` in the meantime.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        approved = urllib.parse.urlsplit(OPENNEURO_ENDPOINT)
        if (
            parts.scheme != "https"
            or (parts.hostname or "").lower() != (approved.hostname or "").lower()
        ):
            raise ProviderError("OpenNeuro redirected the file request outside the approved host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OpenNeuroDownloadSource:
    """Streams public snapshot file bytes from the OpenNeuro file endpoint.

    The endpoint honors no ``Range`` requests (probed 2026-09-14), so
    ``supports_resume`` is ``False``: engines restart the in-progress file
    from zero, keeping already completed files. Bytes stream in bounded
    chunks; nothing is buffered, verified, or persisted here.
    """

    def __init__(
        self,
        *,
        timeout_s: float = DOWNLOAD_TIMEOUT_S,
        chunk_size: int = DOWNLOAD_CHUNK_BYTES,
        user_agent: str = OPENNEURO_DOWNLOAD_USER_AGENT,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s}.")
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}.")
        self._timeout_s = timeout_s
        self._chunk_size = chunk_size
        self._user_agent = user_agent
        self._opener = urllib.request.build_opener(_SameHostRedirectHandler)

    @property
    def source_name(self) -> str:
        return OPENNEURO_PROVIDER

    @property
    def supports_resume(self) -> bool:
        return False

    def stream_file(
        self, dataset_id: str, snapshot: str, path: str, offset: int
    ) -> Iterator[bytes]:
        """Yield one snapshot file's bytes starting exactly at ``offset``."""
        if _DATASET_ID_PATTERN.match(dataset_id) is None:
            raise ValueError(f"dataset_id must be a portable identifier, got {dataset_id!r}.")
        if _TAG_PATTERN.match(snapshot) is None:
            raise ValueError(f"snapshot must be a portable identifier, got {snapshot!r}.")
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or ".." in path.split("/")
            or offset < 0
        ):
            raise ValueError(f"Refusing unsafe file request for {path!r}.")
        url = OPENNEURO_FILES_URL_TEMPLATE.format(
            dataset_id=dataset_id,
            snapshot=snapshot,
            path=urllib.parse.quote(path, safe="/"),
        )
        headers = {"User-Agent": self._user_agent, "Accept": "application/octet-stream"}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            response = self._opener.open(request, timeout=self._timeout_s)
        except TimeoutError:
            raise ProviderTimeout(
                f"OpenNeuro file request did not answer within {self._timeout_s:g}s."
            ) from None
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"OpenNeuro file request failed with HTTP {exc.code}.") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout(
                    f"OpenNeuro file request did not answer within {self._timeout_s:g}s."
                ) from exc
            raise ProviderError("OpenNeuro file request failed.") from exc
        with response:
            status = getattr(response, "status", 200)
            if offset > 0:
                content_range = response.headers.get("Content-Range", "")
                if status != 206 or not content_range.startswith(f"bytes {offset}-"):
                    raise RangeUnsupportedError(
                        f"OpenNeuro ignored the resume offset for {path!r}; "
                        "restart the file from zero."
                    )
            elif status != 200:
                raise ProviderError(f"OpenNeuro file request failed with HTTP {status}.")
            while True:
                try:
                    chunk = response.read(self._chunk_size)
                except TimeoutError as exc:
                    raise ProviderTimeout("OpenNeuro file stream timed out.") from exc
                if not chunk:
                    break
                yield chunk


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ProviderMalformed(f"OpenNeuro field {field_name!r} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ProviderMalformed(f"OpenNeuro field {field_name!r} must be an integer.")


def _require_text(mapping: Mapping[str, Any], key: str, owner: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderMalformed(f"OpenNeuro {owner} has no usable {key!r}.")
    return value.strip()


def parse_dataset_connection(connection: Mapping[str, Any]) -> DatasetPage:
    """Translate one ``datasets`` connection into public listing hits.

    The listing is public-only: nodes whose ``public`` flag is not exactly
    ``True`` are dropped (fail closed, including a missing flag) and never
    surface as hits. Server pagination passes through untouched, so dropping
    nodes cannot invalidate the next cursor.

    Raises :class:`ProviderMalformed` when edges or pagination are absent
    or misshapen, so callers never silently accept a partial listing.
    """
    edges = connection.get("edges")
    page_info = connection.get("pageInfo")
    if not isinstance(edges, list) or not isinstance(page_info, dict):
        raise ProviderMalformed("OpenNeuro listing has no edges/pageInfo connection.")
    items: list[ProviderDatasetRef] = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise ProviderMalformed("OpenNeuro listing edge is not an object.")
        cursor = edge.get("cursor")
        node = edge.get("node")
        if not isinstance(cursor, str) or not cursor:
            raise ProviderMalformed("OpenNeuro listing edge has no cursor.")
        if not isinstance(node, dict):
            raise ProviderMalformed("OpenNeuro listing edge has no node.")
        dataset_id = node.get("id")
        name = node.get("name")
        public = node.get("public")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ProviderMalformed("OpenNeuro listing node has no dataset id.")
        if public is not True:
            continue
        latest = node.get("latestSnapshot")
        tag: str | None = None
        if isinstance(latest, dict):
            raw_tag = latest.get("tag")
            tag = raw_tag if isinstance(raw_tag, str) and raw_tag else None
        items.append(
            ProviderDatasetRef(
                provider=OPENNEURO_PROVIDER,
                dataset_id=dataset_id,
                title=name if isinstance(name, str) and name.strip() else dataset_id,
                public=public is True,
                latest_snapshot=tag,
            )
        )
    has_more = page_info.get("hasNextPage")
    end_cursor = page_info.get("endCursor")
    if not isinstance(has_more, bool):
        raise ProviderMalformed("OpenNeuro listing pageInfo has no hasNextPage flag.")
    next_cursor = end_cursor if isinstance(end_cursor, str) and end_cursor else None
    if has_more and next_cursor is None:
        raise ProviderMalformed("OpenNeuro listing claims another page but names no cursor.")
    return DatasetPage(items=tuple(items), next_cursor=next_cursor, has_more=has_more)


def _normalize_doi(raw: Any) -> tuple[str | None, str | None]:
    """Return ``(doi_or_none, dropped_note)`` for one provider DOI value."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    candidate = raw.strip()
    if candidate[:4].lower() == "doi:":
        candidate = candidate[4:].strip()
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        return None, f"Provider DOI {raw[:80]!r} did not parse; citation is pending review."
    if _DOI_PRECHECK.match(candidate) is None:
        return None, f"Provider DOI {raw[:80]!r} did not parse; citation is pending review."
    return candidate, None


def map_openneuro_snapshot_to_catalog(snapshot: Mapping[str, Any]) -> CatalogEntry:
    """Map one ``snapshot`` payload to validated, pending catalog data.

    The payload is the ``data.snapshot`` object: identity, dataset block,
    description, summary, and the root file listing. Every field the
    contract requires but the provider does not authoritatively supply
    (checksums, SPDX, template compatibility, approval) stays pending in
    ``limitations`` for a curator to verify. Anything structurally wrong
    raises :class:`ProviderMalformed`; contract violations raise the same
    after ``CatalogEntry`` validation rejects them.
    """
    if not isinstance(snapshot, dict):
        raise ProviderMalformed("OpenNeuro snapshot is not an object.")
    dataset = snapshot.get("dataset")
    if not isinstance(dataset, dict):
        raise ProviderMalformed("OpenNeuro snapshot names no dataset.")
    dataset_id = dataset.get("id")
    if not isinstance(dataset_id, str) or _DATASET_ID_PATTERN.match(dataset_id) is None:
        raise ProviderMalformed("OpenNeuro snapshot names no usable dataset id.")
    public = dataset.get("public")
    if public is False:
        raise ProviderNotFound(
            f"OpenNeuro dataset {dataset_id} is not public; only public snapshots resolve."
        )
    if public is not True:
        raise ProviderMalformed(f"OpenNeuro dataset {dataset_id} hides its public flag.")
    tag = snapshot.get("tag")
    if not isinstance(tag, str) or _TAG_PATTERN.match(tag) is None:
        raise ProviderMalformed("OpenNeuro snapshot names no usable tag.")
    snapshot_id = snapshot.get("id")
    if isinstance(snapshot_id, str) and snapshot_id != f"{dataset_id}:{tag}":
        raise ProviderMalformed("OpenNeuro snapshot id does not match its dataset and tag.")

    description = snapshot.get("description")
    if not isinstance(description, dict):
        raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} has no description.")
    title = _require_text(description, "Name", "description")
    license_raw = description.get("License")
    license_name = (
        license_raw.strip()
        if isinstance(license_raw, str) and license_raw.strip()
        else "Unverified OpenNeuro license (pending curator review)"
    )
    authors = description.get("Authors")
    author_note = ""
    if isinstance(authors, list) and authors:
        names = [name.strip() for name in authors if isinstance(name, str) and name.strip()]
        if names:
            first = names[0]
            author_note = f" Authors: {first}" + (" et al." if len(names) > 1 else "") + "."
    references = description.get("ReferencesAndLinks")
    references_note = ""
    if isinstance(references, list) and any(
        isinstance(link, str) and link.strip() for link in references
    ):
        references_note = " Provider references/links need curator review before citation."

    summary = snapshot.get("summary")
    if not isinstance(summary, dict):
        raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} has no summary.")
    modalities = summary.get("modalities")
    primary = summary.get("primaryModality")
    if isinstance(primary, str) and primary.strip():
        modality = primary.strip().upper()
    elif isinstance(modalities, list):
        names = [m.strip().upper() for m in modalities if isinstance(m, str) and m.strip()]
        if not names:
            raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} names no modality.")
        modality = names[0]
    else:
        raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} names no modality.")
    tasks = summary.get("tasks")
    task = ""
    if isinstance(tasks, list):
        for candidate in tasks:
            if isinstance(candidate, str) and candidate.strip():
                task = candidate.strip()
                break
    subjects = summary.get("subjects")
    participants = len(subjects) if isinstance(subjects, list) else 0
    try:
        total_bytes = _as_int(summary.get("size"), "summary.size")
    except ProviderMalformed as exc:
        raise ProviderMalformed(
            f"OpenNeuro snapshot {dataset_id}:{tag} reports no usable size."
        ) from exc
    if total_bytes <= 0:
        raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} reports no usable size.")

    files = snapshot.get("files")
    if not isinstance(files, list):
        raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} lists no files.")
    expected: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} lists a bad file.")
        if entry.get("directory") is True:
            continue
        filename = entry.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} lists a bad file.")
        try:
            size = _as_int(entry.get("size"), "file.size")
        except ProviderMalformed as exc:
            raise ProviderMalformed(
                f"OpenNeuro snapshot {dataset_id}:{tag} lists a bad file size."
            ) from exc
        if size < 0:
            raise ProviderMalformed(f"OpenNeuro snapshot {dataset_id}:{tag} lists a bad file size.")
        # CatalogFile carries an optional checksum the provider never publishes;
        # record its absence explicitly so the identity matches stored form.
        expected.append({"path": filename.strip(), "byte_size": size, "sha256": None})
    if not expected:
        raise ProviderMalformed(
            f"OpenNeuro snapshot {dataset_id}:{tag} lists no root files to expect."
        )
    if sum(item["byte_size"] for item in expected) > total_bytes:
        raise ProviderMalformed(
            f"OpenNeuro snapshot {dataset_id}:{tag} root files exceed its total size."
        )

    doi, doi_note = _normalize_doi(description.get("DatasetDOI"))
    created = snapshot.get("created")
    if isinstance(created, str) and created.strip():
        created_note = created.strip()
    else:
        created_note = "date unknown"
    hexsha = snapshot.get("hexsha")
    hexsha_note = (
        f" Git commit {hexsha.strip()[:12]}." if isinstance(hexsha, str) and hexsha.strip() else ""
    )
    bids = description.get("BIDSVersion")
    bids_note = (
        f" BIDS version {bids.strip()} per provider metadata."
        if isinstance(bids, str) and bids.strip()
        else ""
    )
    limitations = (
        "Unverified OpenNeuro public metadata (pending curator review): license, citation,"
        " checksums, and template compatibility are not approved. Root file listing only;"
        " recursive trees, per-file checksums, and download verification belong to the"
        f" retrieval unit.{author_note}{references_note}{hexsha_note}{bids_note}"
        + (f" {doi_note}" if doi_note else "")
    )[:2000]
    reuse_statement = (
        f"OpenNeuro {dataset_id}:{tag} public metadata (snapshot {created_note});"
        " reuse terms pending curator verification against the landing page."
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "catalog_identity": catalog_entry_identity(
            provider=OPENNEURO_PROVIDER,
            dataset_id=dataset_id,
            snapshot=tag,
            modality=modality,
            task=task,
            participants=participants,
            formats=["BIDS"],
            approximate_total_bytes=total_bytes,
            expected_total_bytes=total_bytes,
            expected_files=expected,
            access="public",
            license_name=license_name,
            license_spdx=None,
            reuse_statement=reuse_statement,
            citations=[
                {"title": title, "doi": doi, "url": None},
            ],
            landing_page=f"https://openneuro.org/datasets/{dataset_id}/versions/{tag}",
            compatible_templates=[],
        ),
        "provider": OPENNEURO_PROVIDER,
        "dataset_id": dataset_id,
        "snapshot": tag,
        "title": title,
        "modality": modality,
        "task": task,
        "participants": participants,
        "formats": ["BIDS"],
        "approximate_total_bytes": total_bytes,
        "expected_total_bytes": total_bytes,
        "expected_files": expected,
        "access": "public",
        "license_name": license_name,
        "license_spdx": None,
        "reuse_statement": reuse_statement,
        "citations": [
            {"title": title, "doi": doi, "url": None},
        ],
        "landing_page": f"https://openneuro.org/datasets/{dataset_id}/versions/{tag}",
        "compatible_templates": [],
        "curator": "",
        "review_status": "pending",
        "reviewed_at": None,
        "limitations": limitations,
    }
    try:
        return CatalogEntry.model_validate(payload)
    except Exception as exc:
        raise ProviderMalformed(
            f"OpenNeuro snapshot {dataset_id}:{tag} failed catalog validation: {exc}."
        ) from exc


class OpenNeuroProvider:
    """Read-only OpenNeuro source behind the provider-neutral interface."""

    def __init__(self, transport: GraphQLTransport) -> None:
        self._transport = transport
        self._provider_name = OPENNEURO_PROVIDER

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def list_datasets(self, search: DatasetSearch) -> DatasetPage:
        """List one page of public datasets, newest first.

        Only public records appear: non-public nodes are dropped before
        paging results reach the caller, while the server cursor passes
        through untouched. ``modality`` maps to the server filter
        (lowercased); ``query`` is a page-local case-insensitive substring
        match on dataset id and title because the documented ``search``
        field resolves to ``null``.
        """
        if not 1 <= search.first <= MAX_OPENNEURO_PAGE:
            raise ValueError(f"first must be within 1..{MAX_OPENNEURO_PAGE}, got {search.first}.")
        variables: dict[str, Any] = {"first": search.first}
        if search.after is not None:
            variables["after"] = search.after
        if search.modality is not None and search.modality.strip():
            variables["modality"] = search.modality.strip().lower()
        data = await self._transport.execute(_LIST_QUERY, variables)
        connection = data.get("datasets")
        if not isinstance(connection, dict):
            raise ProviderMalformed("OpenNeuro listing carried no datasets connection.")
        page = parse_dataset_connection(connection)
        if search.query is not None and search.query.strip():
            needle = search.query.strip().lower()
            kept = tuple(
                item
                for item in page.items
                if needle in item.dataset_id.lower() or needle in item.title.lower()
            )
            return DatasetPage(items=kept, next_cursor=page.next_cursor, has_more=page.has_more)
        return page

    async def resolve_snapshot(self, dataset_id: str, snapshot_tag: str) -> CatalogEntry:
        """Resolve one immutable public snapshot tag to pending catalog data.

        The response is bound to the request: a payload describing any other
        dataset or tag raises :class:`ProviderMalformed` instead of resolving
        the wrong immutable snapshot.
        """
        if _DATASET_ID_PATTERN.match(dataset_id) is None:
            raise ValueError(f"dataset_id must be a portable identifier, got {dataset_id!r}.")
        if _TAG_PATTERN.match(snapshot_tag) is None:
            raise ValueError(f"snapshot_tag must be a portable identifier, got {snapshot_tag!r}.")
        data = await self._transport.execute(
            _SNAPSHOT_QUERY, {"datasetId": dataset_id, "tag": snapshot_tag}
        )
        snapshot = data.get("snapshot")
        if snapshot is None:
            raise ProviderNotFound(
                f"Unknown OpenNeuro snapshot {dataset_id}:{snapshot_tag} "
                "(unknown id/tag, or not public)."
            )
        if not isinstance(snapshot, dict):
            raise ProviderMalformed("OpenNeuro snapshot is not an object.")
        entry = map_openneuro_snapshot_to_catalog(snapshot)
        if entry.dataset_id != dataset_id or entry.snapshot != snapshot_tag:
            raise ProviderMalformed(
                f"OpenNeuro answered {entry.dataset_id}:{entry.snapshot} for requested "
                f"{dataset_id}:{snapshot_tag}; refusing the substituted snapshot."
            )
        return entry
