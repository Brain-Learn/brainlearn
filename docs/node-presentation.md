# Node presentation and motion (Step 4E.2)

Per-instance presentation customizes how a node looks without changing what it
computes. It is display-only metadata: safe to edit freely, preserved through
drafts and project save/open, and excluded from every computation identity and
cache decision.

## Fields and bounds

Each workflow node may carry an optional `presentation` object:

| Field | Type | Default (absent) | Bounds |
|---|---|---|---|
| `title` | string or null | manifest label | 1–80 characters after trimming; blank titles rejected |
| `accent` | allowlisted color | `teal` | one of `teal`, `blue`, `violet`, `amber`, `rose`, `slate` |
| `compact` | boolean | `false` (expanded) | — |
| `notes` | plain-text string | `""` | at most 2000 characters |

Absent `presentation` is the canonical manifest-default form for new and old
nodes: workflows saved before this feature load unchanged. The backend trims
surrounding title whitespace and rejects whitespace-only titles, so API and UI
entries behave identically. Unknown fields and out-of-range values are rejected
with an error naming the problem; the TypeScript wire type accepts an explicit
`presentation: null` from the API and treats it as absent.

## Editing, reset, and undo

Select a node to edit its presentation in the inspector:

- Custom title (committed on blur or Enter) and notes (committed on blur) each
  create one undo entry per completed edit; blurring an unchanged field creates
  none.
- Accent and compactness apply immediately, one undo entry per change.
- Reset to manifest defaults removes the stored overrides entirely.

Notes are plain text: markup is stored verbatim and rendered as text, never as
HTML.

## Identity and cache exclusion

`workflow_identity` and every node `content_identity` are computed from node
type and implementation version, inputs, parameters, environment, seed, and
settings only. Presentation fields are not inputs to those hashes, so renaming
a node, recoloring it, collapsing it, or editing notes never invalidates cached
work: rerunning a workflow after a presentation-only change reuses the cache
(`cache_reused`, attempt 0). Changing a scientific parameter still changes the
identity and invalidates exactly the affected node and its descendants.

## Reduced motion

Discrete changes (node insertion, selection, inspector content, validation
state, viewport fit, node-removal leaving state) use short CSS transitions;
direct pointer dragging is never animated. When the operating system requests
reduced motion (`prefers-reduced-motion: reduce`):

- all CSS transitions and entrance/removal animations are disabled,
- programmatic viewport fitting is instant (`duration: 0`),
- node removal applies immediately with no leaving state.

The TypeScript `matchesReducedMotion` / `usePrefersReducedMotion` policy and
`resolveFitViewDuration` helper in `apps/web/src/motion.ts` keep this behavior
tested; the Python contract lives in
`packages/core/src/brainlearn_core/schema.py` (`NodePresentation`).

## Contributor notes

- Add display-only per-instance fields to `NodePresentation`, never to the
  identity inputs in `identity.py` or `worker.workflow_identity`. Extend the
  invariance tests in `tests/test_presentation.py` (schema, identity, API echo)
  and the cache-reuse regression in `tests/test_cache.py`.
- Keep `extra="forbid"` on the model so unknown fields fail loudly.
- Frontend helpers live in `apps/web/src/graph.ts` (`defaultPresentation`,
  `resolvePresentation`, `updatePresentation`, `resetPresentation`,
  `hasPresentationOverrides`); normalization there must match the backend
  (trim titles, enforce bounds, allowlist accents). Cover new behavior in
  `graph.test.ts`, `motion.test.ts`, `canvas.test.tsx`, and `App.test.tsx`
  with behavioral assertions, not snapshots.
