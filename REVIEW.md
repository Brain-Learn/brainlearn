# BrainLearn monitored implementation review

Review date: 2026-09-13

## Current review

Scope: second monitoring review of Step 4E.1 direct canvas manipulation and its
two requested repairs.

Status: **approved**. The complete Step 4E.1 worktree satisfies the direct
interaction gate and may be committed. The top-level Step 4 remains open for
motion/customization, demonstration fixtures, run controls, and the complete
workflow gate.

## Repairs independently verified

- A drag is bound to the canonical `Workflow` object present at drag start.
  Replacement with different or overlapping node IDs discards the stale
  gesture, creates no undo entry, and immediately renders the latest workflow.
- Every non-commit drag stop explicitly rebuilds transient nodes from the latest
  workflow, selection, and validation props, closing the suppressed-effect
  boundary even for a no-movement gesture.
- The parent commit callback repeats the workflow-object check before changing
  history, providing a second stale-write guard.
- Failure of React Flow's authoritative `screenToFlowPosition` conversion now
  rejects the drop without inserting a node. The fabricated identity-viewport
  fallback and its unused types were removed.
- Component regressions cover different and overlapping replacement IDs,
  no-movement selection resynchronization, ordinary single commit, converter
  failure, and transformed-viewport coordinates.

## Step 4E.1 behavior approved

- Existing nodes follow pointer position continuously through controlled,
  transient React Flow state.
- A completed movement writes one workflow revision and one undo entry; a click
  without movement writes neither. Undo restores the original position.
- Palette nodes can be added by click, Enter/Space, or drag-and-drop. All paths
  use the same node construction code, and new nodes are selected.
- Drop coordinates go through the active React Flow transform. Invalid,
  missing, unknown, non-finite, or failed-conversion drops do not alter the
  workflow.
- Draft and project persistence continue to store committed workflow positions;
  direct pointer tracking is not animated or persisted per movement event.

## Verification reproduced

- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 38 files clean.
- `uv run mypy packages/core/src packages/server/src`: 18 source files clean.
- `uv run pytest -q`: 273 passed; two upstream Starlette/AnyIO warnings.
- `npm --prefix apps/web run lint`: passed.
- `npm --prefix apps/web run format:check`: passed.
- `npm --prefix apps/web test -- --run`: 39 passed in 6 files.
- `npm --prefix apps/web run build`: passed; 1,837 modules transformed.
- `npm --prefix apps/web audit --omit=dev`: 0 vulnerabilities.
- Live browser probe: existing-node drag was continuous, one Undo restored the
  starting position, zoom remained operable, and palette drop after zoom landed
  at the converted pointer location and selected the new node.
- `git diff --check`: passed before this approval update.

## Next assignment

Proceed only with Step 4E.2 motion and presentation customization as specified
in `docs/implementation-plan.md`. Do not begin demonstration fixtures, run
controls, scientific processing, or dataset access. Leave the Step 4 heading
unchecked and request monitoring review before committing the next unit.
