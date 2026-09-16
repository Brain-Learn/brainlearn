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
