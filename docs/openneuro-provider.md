# OpenNeuro public metadata provider (Step 5A.2)

This document covers the read-only provider boundary only: listing public
datasets and resolving one immutable snapshot tag into schema-`1.0`
catalog data. File downloads, archive extraction, resume/cancellation of
bytes, dataset UI, and MNE/BIDS inspection are explicitly out of scope and
arrive in later Step 5A units.

## Provenance

- Public GraphQL endpoint: `https://openneuro.org/crn/graphql`, per the
  official API documentation at `https://docs.openneuro.org/api.html`.
- Server version observed during development: `5.6.0` (response
  `extensions.openneuro.version` on 2026-09-14). The adapter does not pin
  or require this version; it is recorded so future breakage can be
  bisected against a known-good server.
- Only two operations are used, both public and unauthenticated:
  - `datasets(first, after, modality)` listing (`DatasetConnection` with
    `edges { cursor node { id public name latestSnapshot { tag } } }` and
    `pageInfo { hasNextPage endCursor }`).
  - `snapshot(datasetId, tag)` detail with `description`, `summary`,
    root `files`, `created`, `hexsha`, `size`, and the nested
    `dataset { id public name }` block.
- The top-level `search(q, ...)` field currently resolves to `null` and
  `advancedSearch` currently fails edge-cursor validation server-side
  (both probed 2026-09-14), so neither is used. Free-text `query` is a
  page-local substring filter on dataset id and title; it is not a corpus
  search and the interface documents it as such.

## Mapping rules (all results stay `pending`)

Every resolution goes through `CatalogEntry` validation; the provider
cannot bypass checksums, URL, identity, or licensing rules.

| Provider fact | Catalog field |
|---|---|
| `dataset.id`, `snapshot.tag` | `provider: "openneuro"`, `dataset_id`, `snapshot` (explicit tag only; drafts and mutable `latest` pointers are never mapped) |
| `description.Name` | `title`, citation title |
| `summary.primaryModality` (upper-cased) else first `modalities` entry | `modality` (`"eeg"` becomes `"EEG"`) |
| First `summary.tasks` entry, else `""` | `task` |
| `len(summary.subjects)` | `participants` |
| Constant `["BIDS"]` (OpenNeuro hosts BIDS datasets; version noted in `limitations`) | `formats` |
| `summary.size` | `approximate_total_bytes`, `expected_total_bytes` |
| Non-directory root `files` (`filename`, `size`) | `expected_files` with `sha256: None` (provider publishes no per-file hashes) |
| `description.License` verbatim, else an explicit unverified placeholder; `license_spdx` always `None` | `license_name`, `license_spdx` (SPDX is never inferred; the curator maps e.g. `CC0` during verification) |
| Authors plus `Name`; `DatasetDOI` with a `doi:` prefix stripped, dropped with a `limitations` note when unparseable; citation `url` always `None` | `citations` (single entry) |
| `https://openneuro.org/datasets/<id>/versions/<tag>` | `landing_page` |
| `[]` | `compatible_templates` (no compatibility claim is inferred) |
| `dataset.public is True` required; `False` resolves to `ProviderNotFound` | `access: "public"` |
| Requested `(dataset_id, tag)` must equal the response pair, else `ProviderMalformed` | request/response binding (a stale cache, server defect, or wrong transport can never silently resolve the wrong snapshot) |

`limitations` always states that the record is unverified public
metadata pending curator review, that only the root file listing is
represented, that checksums/compatibility are unapproved, and (when
known) the snapshot date, git short hash, BIDS version, and author
summary. File `urls`, transfer endpoints, credentials, tokens, cookies,
query signatures, and raw provider responses are never requested or
persisted.

## Listing is public-only

The provider-neutral contract (`brainlearn_core.providers.DatasetProvider`)
is public-only: `list_datasets` never reports non-public records, and
`resolve_snapshot` reports a non-public snapshot as `ProviderNotFound`.

- `parse_dataset_connection` drops every node whose `public` flag is not
  exactly `True` (fail closed, including a missing flag) before hits are
  built. Server pagination passes through untouched, so dropping nodes
  cannot invalidate the next cursor; a page may simply carry fewer items
  than requested. The page-local `query` substring filter applies after
  the public filter.
- `MockDatasetProvider` models the same observable rule: listing reports
  confirmed-public entries only, paginated after exclusion so cursors
  always describe the public sequence, and resolving a matched
  non-public entry raises `ProviderNotFound`.

## Rate, error, and size behavior

- `UrllibGraphQLTransport` (standard library only) enforces a bounded
  timeout (default 10 s) and a response-size bound (default 1 MiB) before
  JSON parsing. Timeouts surface as `ProviderTimeout`, including
  `urllib.error.URLError` values that wrap a socket timeout; other URL
  failures stay `ProviderError`. HTTP failures and
  GraphQL `errors` are `ProviderError`; missing/misshapen payloads and
  contract rejections are `ProviderMalformed`; unknown or non-public
  snapshots are `ProviderNotFound`.
- There is no automatic retry: callers decide when a metadata fetch is
  worth repeating, so a tight loop can never hammer the public API.
- Listing pages are bounded (`first` within 1..25 for OpenNeuro, 1..50
  for the mock). Listing cursors are opaque: mock cursors encode nothing
  but an offset, and OpenNeuro cursors pass through untouched.
- `asyncio` cancellation propagates unchanged through both the mock and
  the transport; a cancelled metadata request never becomes a provider
  error.

## No-download boundary

The metadata adapter never requests per-file `urls`, never derives transfer
endpoints, and never writes bytes. A later retrieval unit will resolve
transfer endpoints ephemerally from `(provider, dataset_id, snapshot)`,
fetch into staging, verify sizes/checksums plus BIDS structure, and only
then write a `DatasetLock` whose identity is recomputed from verified
facts.

## Tests

- `tests/test_openneuro_provider.py` (offline): mock pagination, empty
  results, query/modality filtering, public-only listing with stable
  pagination, resolve/not-found (including non-public)/malformed/error/
  timeout/cancellation behavior, request/response binding for substituted
  dataset ids and tags, fixture mapping to a pending catalog record with
  stable identity and round-trip, DOI handling, transport bounds/headers/
  direct-and-wrapped timeout/error mapping plus cancellation propagation.
- Sanitized fixtures: `tests/fixtures/openneuro-list-min.json` (two
  listing edges) and `tests/fixtures/openneuro-snapshot-min.json`
  (minimal snapshot shaped on the documented `ds000001:1.0.0` example
  with synthetic authors and small sizes). Both are hand-written and
  contain no credentials or transfer URLs.
- Opt-in live smoke (`BRAINLEARN_LIVE_OPENNEURO=1`): lists one EEG
  dataset and resolves the documented `ds000001:1.0.0` snapshot. The
  normal suite never touches the network.
