# BrainLearn monitored implementation review

Review date: 2026-09-14

## Current review

Scope: final monitoring review of Step 5A.3, the authenticated read-only dataset
API and searchable library/details interface.

Status: **approved**. All three findings from the first review are repaired, the
focused regressions pass, and the complete repository gate is green.

## Findings

No blocking findings remain.

1. Dataset-provider failures now map to stable class-specific HTTP details. A
   strict provider-name validator prevents unsafe names from reaching lookup or
   response text, and only validated provider/dataset/snapshot identifiers are
   echoed. Hostile exception regressions cover all mapped classes on both routes
   and expose no injected token, signed URL, or upstream message.
2. Pagination now uses the immutable criteria that produced its cursor. Editing
   query or modality controls without submitting cannot alter a continuation;
   submitting a fresh search replaces the result set.
3. Results/details transitions now manage real focus. Successful resolution
   focuses the details heading, failure focuses the announced Back recovery
   control, Back and Escape restore the originating result, and project changes
   preserve or relocate focus according to whether controls remain available.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 52 files clean.
- `uv run mypy packages/core/src packages/server/src`: 22 source files clean.
- `uv run pytest -q`: 541 passed, 1 opt-in live smoke skipped, with two upstream
  Starlette/AnyIO warnings.
- Focused dataset API suite: 11 passed with the same two upstream warnings.
- `npm --prefix apps/web run lint`: passed with zero warnings.
- `npm --prefix apps/web run format:check`: passed.
- Focused dataset UI/client suites: 20 passed in 2 files.
- `npm --prefix apps/web test -- --run`: 141 passed in 15 files.
- `npm --prefix apps/web run build`: passed, 1,843 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this review update.

## Scope decision

Authentication and authorized-project isolation remain intact; OpenNeuro is
constructed lazily; normal tests remain offline; pending metadata is presented
without scientific inference; and this unit adds no download, extraction, lock,
credential, transfer URL, or MNE/BIDS behavior. The searchable dataset-library
and details-panel checklist item is approved. Step 5 remains open.
