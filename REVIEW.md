# BrainLearn monitored implementation review

Review date: 2026-09-14

## Current review

Scope: final monitoring review of Step 5A.2 after the three focused
provider-boundary repairs.

Status: **complete**. No blocking findings remain. The provider-neutral
read-only source boundary and OpenNeuro public metadata adapter are approved.

## Approved behavior

- `DatasetProvider` defines an asynchronous, provider-neutral public listing and
  immutable-snapshot resolution boundary; `MockDatasetProvider` supplies fully
  offline deterministic pagination, filtering, failure, timeout, malformed-data,
  and cancellation behavior.
- OpenNeuro transport, connection parsing, and snapshot mapping are separated.
  The standard-library transport bounds request/response size and timeout, never
  attaches authentication, and keeps ordinary tests offline.
- OpenNeuro listing is public-only and fail closed. Non-public nodes never reach
  results, while provider-owned pagination cursors remain untouched. The mock
  applies its public filter before pagination and refuses non-public resolution.
- Snapshot resolution requires an explicit immutable tag and binds the returned
  dataset id and tag to the requested pair. Substituted responses raise
  `ProviderMalformed` and cannot be returned or persisted.
- Direct and urllib-wrapped socket timeouts surface as `ProviderTimeout`; other
  URL failures remain `ProviderError`; asyncio cancellation propagates unchanged.
- Every resolved snapshot passes through schema-`1.0` `CatalogEntry` validation
  and remains review-pending. No transfer URLs, secrets, raw provider responses,
  inferred SPDX value, checksum approval, or workflow-compatibility claim is
  persisted.
- The unit contains no downloads, extraction, dataset UI, MNE/BIDS dependency,
  credential flow, or scientific processing.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 50 files clean.
- `uv run mypy packages/core/src packages/server/src`: 21 source files clean.
- `uv run pytest -q`: 530 passed, 1 opt-in live smoke skipped, with two upstream
  Starlette/AnyIO warnings.
- Focused provider suite: 37 passed, 1 opt-in live smoke skipped.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 121 passed in 13 files.
- `npm --prefix apps/web run build`: passed, 1,841 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this final review update.
- Independent direct probes refused separate dataset-id and tag substitutions,
  omitted a private listing node while preserving the server cursor, classified
  a wrapped timeout as `ProviderTimeout`, and retained `ProviderError` for a
  non-timeout URL failure.

## Checklist decision

The OpenNeuro-first provider item is approved and checked. Step 5 remains open
for the read-only dataset library UI, download lifecycle and hardening, offline
local import, pinned EEG fixture, and scientific BIDS/MNE inspection work.

## Next assignment

Implement only Step 5A.3 from `docs/implementation-plan.md`: expose the approved
provider catalogue through authenticated, project-aware read-only local API
endpoints and add the searchable dataset library/details UI. Show all required
pre-download metadata and pending-review limitations. Do not download or extract
files, write dataset locks, add MNE/BIDS dependencies, or add credentials.
