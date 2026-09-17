# BrainLearn monitored implementation review

Review date: 2026-09-16

## Current review

Scope: final monitoring review of Step 5A.5 after the redirect/archive
implementation and two rounds of archive-limit repairs on PR #9.

Status: **approved**. Redirect validation, archive containment, declared and
live extraction limits, cancellation/retry behavior, verified finalization,
and the repaired ZIP/tar edge cases all pass. No blocking finding remains.

## Findings resolved

1. Valid large ZIPs are no longer rejected from total byte size. A bounded
   EOCD/ZIP64 tail probe seeks to the real file tail and checks member count
   before `ZipFile` construction; `infolist()` retains the authoritative
   post-parse check. Regressions prove a 5 MB one-member ZIP is accepted and
   an over-member archive is rejected without constructing the ZIP parser.
2. Tar and tar.gz enforce compression ratio from declared member sizes before
   extraction or discard and from actual bytes while streaming. Skipped
   members share the same accounting, so a highly compressed member cannot
   bypass the ratio limit during resume. Both written and skipped ~924x bombs
   reject before output bytes land.
3. The original redirect and extraction guarantees remain intact: approved
   HTTPS hosts and bounded hops only; no credential, signed-target, or scheme
   downgrade redirects; portable allowlisted members only; links, traversal,
   duplicates, conflicts, special files, oversized expansion, and writes
   outside staging are refused; successful finalization still requires the
   exact verified catalog tree.

## Verification reproduced

- Direct probes: the 5,000,118-byte ZIP reports one member from the bounded
  pre-parser, and a ~924x skipped tar.gz member is rejected as
  `ratio-exceeded`.
- Focused archive/redirect/download suites: 95 passed with two upstream
  Starlette/AnyIO warnings.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 62 files clean.
- `uv run mypy packages/core/src packages/server/src`: 25 source files clean.
- `uv run pytest -q`: 653 passed, 1 opt-in live smoke skipped, with the same
  two upstream warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- Complete Vitest suite: 152 passed in 16 files.
- `npm --prefix apps/web run build`: passed, 1,843 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed before this review update.
- Required GitHub CI jobs passed on implementation head `bf82a4b`.

## Scope decision

Step 5A.5 is approved. The redirect/archive-hardening checklist item is checked;
Step 5 remains open. The next work unit is only Step 5A.6: preserve local/private
dataset import as an equal offline path and record an immutable local identity.
Do not begin the pinned integration fixture, MNE/MNE-BIDS, BIDS discovery, signal
inspection, or later scientific work in that unit.

## Post-merge CI follow-up

The first `main` run after PR #9 exposed a pre-existing run-review scheduler
race: a driver holding a stale pre-decision record could enter settlement after
an approval was saved and overwrite the durable decision by parking the run as
`waiting_for_review`. GitHub Actions run 35098055658 reproduced the failure in
`test_two_runs_reuse_trunk_and_reexecute_zero_output_nodes` while every other
Python test and the complete frontend job passed.

The repair serializes review decisions with every parking transition, reloads
the run under that guard before settlement, and redrives newly ready work rather
than treating it as a scheduler stall. A deterministic regression blocks the
driver immediately before settlement, records an approval, and proves the stale
snapshot cannot overwrite it; the run succeeds with exactly one
`review_decided` event.

Local verification for the repair branch: Ruff and format passed (62 files),
strict mypy passed (25 source files), pytest passed 654 tests with 1 opt-in live
smoke skipped and 2 upstream warnings, ESLint and Prettier passed, Vitest passed
152 tests in 16 files, the production build passed (1,843 modules), the
production npm audit found 0 vulnerabilities, and `git diff --check` passed.
PR #10 passed both required hosted checks, received a monitored approval
comment, and was squash-merged as `7a72f54`. The resulting `main` run
35102093017 passed both Python and frontend jobs, including the test that had
failed before the repair. No Step 5A.6 work has started.

## Step 5A.6 first monitoring review

Review date: 2026-09-17

Scope: PR #12, local/private offline dataset import and immutable local
identity.

Status: **changes requested**. The complete baseline gate passes, but five
contract and integrity findings remain:

1. A same-size in-place mutation can evade verification when the writer
   restores `st_mtime_ns`. A direct 128 KiB probe modified the second half
   during hashing, restored mtime, and was accepted because descriptor,
   pathname, and tree snapshots omit `st_ctime_ns`.
2. `LocalImportRecord` does not bind a ready record to its embedded lock. A
   record whose `local_path` is `source-b` accepts a valid lock naming
   `source-a`, and the record does not require the local provider/snapshot,
   restricted access, or null catalog identity.
3. `DatasetLock` exempts every `provider="local"` record from public metadata
   requirements without enforcing the documented local invariants. A directly
   constructed, correctly re-identified record accepted public access, a
   non-local snapshot, EEG/task/participant/template claims, and MIT metadata.
4. The browser guard accepts state-inconsistent records and incomplete locks,
   including a ready record with no lock and expected-file entries without
   path, size, or hash validation.
5. The claimed location-independent identity still includes the final source
   directory name via `dataset_id`; identical bytes and explicit metadata in
   `folder-a` and `folder-b` produced different identities.

The monitor reproduced Ruff and format success (64 files), strict mypy success
(26 source files), pytest 699 passed with 1 opt-in smoke skipped and 2 upstream
warnings, ESLint and Prettier success, Vitest 159 passed in 17 files, production
build success (1,844 modules), production audit with 0 vulnerabilities, and a
clean diff check. Both required hosted CI jobs are also green on `f69c7e7`.
These passing gates do not cover the direct probes above. PR #12 remains open;
Step 5A.6 stays unchecked and no later Step 5 work may begin.
