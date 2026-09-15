# BrainLearn monitored implementation review

Review date: 2026-09-15

## Current review

Scope: final monitoring review of Step 5A.4 after the two residual
download-lifecycle repairs.

Status: **approved**. Both residual findings are repaired, all prior integrity
repairs remain present, and the complete repository gate passes. No blocking
finding remains.

## Findings resolved

1. Resume verification now hashes an existing prefix incrementally in chunks no
   larger than `VERIFY_CHUNK_BYTES` while retaining only an integer byte count.
   A vanished/shrunk prefix has one descriptor owner, restarts once from zero,
   and cannot recurse or loop indefinitely. The recording-hasher regression
   verifies both bounded updates and the final bytes.
2. Existing-transfer discovery now has ownership separate from Start, Cancel,
   and Resume. Selection and transfer guards prevent a late adoption response
   from overwriting a user-installed transfer, while view and project changes
   invalidate discovery. Deferred tests cover both response orders and project
   crossover. A further same-batch diagnostic ordering also passed before the
   temporary diagnostic was removed.
3. The prior storage-containment, catalog-binding, recovery-verification, and
   post-rename reconciliation repairs remain intact. Focused backend/core tests
   pass 51 cases, including the adversarial symlink, corruption, non-regular
   file, crash-window, retry, cancellation, and resume scenarios.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 57 files clean.
- `uv run mypy packages/core/src packages/server/src`: 24 source files clean.
- `uv run pytest -q`: 592 passed, 1 opt-in live smoke skipped, with two upstream
  Starlette/AnyIO warnings.
- Focused download-record and dataset-download suites: 51 passed with the same
  two upstream warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- Focused dataset library/client/download suites: 31 passed in 3 files.
- Complete Vitest suite: 152 passed in 16 files.
- `npm --prefix apps/web run build`: passed, 1,843 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this review update.

## Scope decision

Step 5A.4 is approved. The download-lifecycle checklist item may be checked and
the work committed. Step 5 remains open. The next work unit is only Step 5A.5:
redirect and archive-extraction hardening; do not begin local/private import,
the pinned EEG fixture, MNE/MNE-BIDS, or later scientific work in that unit.
