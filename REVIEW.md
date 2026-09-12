# BrainLearn implementation review

Review date: 2026-09-12

## Current review

Scope: final monitoring review of Step 4B run persistence, pure scheduling,
authenticated run endpoints, and restart recovery.

Status: **approved**. All findings from both Step 4B monitoring reviews are
resolved. Independent probes reject the original security, identity,
scheduling, and concurrency failures, and the complete repository baseline
passes.

Step 4A remains approved in commit `25bf419`.

## Approved behavior

- Run records persist atomically at
  `<project-root>/runs/<run-id>/run.json` under the exact authorized project
  root.
- Run IDs, discovered directory names, and embedded record IDs agree.
  Discovery ignores symlinked directories and files, resolves and authorizes
  candidates before reading, and surfaces corrupt records with their cause.
- Authenticated create, open, save, list, and recover endpoints provide
  actionable 400, 403, 404, and 409 responses.
- Process-wide per-run locks serialize create, save, and recovery mutations
  across request handlers and future worker threads using `RunStore`.
  Duplicate creates cannot both succeed, and stale writes cannot replace a
  terminal record.
- Terminal run history is immutable and rejected writes preserve its file
  byte-for-byte.
- The pure scheduler validates unknown references, duplicate edges, cycles,
  missing graph-node states, and unknown downstream sources while producing
  deterministic topological order, ready-node sets, and transitive downstream
  closures.
- Restart recovery reconciles every nonterminal run, resets interrupted work
  to queued attempt zero, preserves voided attempts structurally, invalidates
  dependent reviews transitively, preserves independent pending reviews, and
  can reconcile queued, running, waiting, or all-completed aggregates.

The lock guarantee is intentionally in-process. Step 4C workers must perform
all persisted mutations through `RunStore` in the service process. Introducing
a separate writer process requires a cross-process lock or transactional
store before that process may write run records.

Nonterminal records already containing failed or cancelled nodes remain for
Step 4C to resolve. The worker owns failure/cancellation authorship, dependency
skip propagation, and the corresponding terminal run transition.

## Independent probes

The final monitor reproduced the previously failing cases:

- an unknown downstream source now raises and names `ghost`;
- an external symlinked run directory is ignored;
- a valid record stored under the wrong run directory raises with both IDs;
- overlapping terminal and stale saves finish with terminal history retained;
- the full focused test suite also covers symlinked run files, invalid
  directory names, mixed known/unknown sources, and duplicate create races.

## Verification

The complete baseline passed:

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 31 files already formatted.
- `uv run mypy`: passed for 15 source files.
- `uv run pytest -q`: 184 passed, with 2 upstream deprecation warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web run test`: 24 passed across 5 files.
- `npm --prefix apps/web run build`: passed; 1,837 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- `git diff --check`: passed.

## Next assignment

Step 4C is ready. Follow the ordered worker-lifecycle assignment in
`docs/implementation-plan.md`. Keep execution outside request handlers, route
all writes through `RunStore`, use only allowlisted internal demonstration
adapters, and stop for monitoring review before Step 4D.
