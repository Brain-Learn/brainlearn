# BrainLearn execution contracts (schema 1.0)

This document fixes the meaning of Step 4 run records before any worker,
scheduler, cache, or UI run control is built. The contracts live in
`packages/core/src/brainlearn_core/execution.py`; content identities live in
`packages/core/src/brainlearn_core/identity.py`. Nothing here executes.

## Records

| Record | Purpose |
|---|---|
| `RunRecord` | One workflow run: workflow identity, state, environment, node runs, ordered events, optional failure. |
| `NodeRunRecord` | One node's execution attempt: replay inputs, state, timestamps, artifacts, optional failure or review pause. |
| `ArtifactRecord` | Reference to one output file by project-relative path plus content hash. Never embeds bytes. |
| `EnvironmentRecord` | OS, architecture, Python version, pinned packages, accelerator label. |
| `RunEvent` | One ordered (`seq` 0..n-1), timestamped fact in the run's event stream, with an optional structured `attempt` naming the execution attempt it describes. |
| `FailureRecord` | Machine-readable `code`, human-readable `message`, optional node-run link, timestamp. |
| `ReviewPauseRecord` | Human-review checkpoint bound to the reviewed data via `input_identity`. |

Every record carries `schema_version: "1.0"`. Unknown versions fail through
the `migrate_*_dict` entry points with an actionable message; stored records
are never silently coerced.

## States

Run states: `queued`, `running`, `waiting_for_review`, `succeeded`,
`failed`, `cancelled`.

Node-run states add `dependency_skipped` and `cache_reused`. Skipped and
reused work never executed: `counts_as_execution` is False for `queued`,
`dependency_skipped`, and `cache_reused`, True for the remaining states.

Legal starts: `queued`. Legal transitions move forward through `running`
(optionally `waiting_for_review` while a review is pending) to exactly one
terminal state. Terminal run states are `succeeded`, `failed`, `cancelled`;
terminal node-run states add `dependency_skipped` and `cache_reused`. Later
slices implement the transitions; this slice only fixes the vocabulary and
the terminal sets (`RUN_TERMINAL_STATES`, `NODE_TERMINAL_STATES`).

## Invariants (enforced by validation)

- **Timestamps** are ISO-8601 strings with a timezone offset; naive
  timestamps are rejected. `queued` carries none; `running` and
  `waiting_for_review` carry `started_at` only; `succeeded` and `failed`
  carry both; `cancelled` always carries `finished_at`; `dependency_skipped`
  and `cache_reused` carry none. Chronology is enforced wherever both
  endpoints exist: `created_at <= started_at <= finished_at` for runs and
  node runs, `requested_at <= decided_at` for reviews. Equal instants pass,
  so equivalent offsets (`+00:00`, `Z`) compare correctly.
- **Failures**: `failure` is present if and only if the state is `failed`.
- **Artifacts**: `failed`, `cancelled`, and `dependency_skipped` work must
  list no successful artifacts. Partial outputs are never successful outputs;
  cancellation and failure handling in later slices must keep them out of
  artifact records.
- **Reviews**: `waiting_for_review` must reference a pending (undecided)
  review pause. A succeeded node with a review must record an approval
  decision with its timestamp. A resumed workflow may reuse a decision only
  when the current input artifact still matches `input_identity`.
- **Events**: `seq` values are exactly `0..n-1` in order, without gaps.
- **Artifact paths** are project-relative forward-slash paths with no empty,
  `.`, or `..` segments, no leading root, no drive qualifier (`C:`, `C:/`),
  and no UNC prefix. Windows separators are normalized to `/` on input so
  one canonical representation is stored on every OS. Session tokens and
  large data are never recorded.
- **Referential integrity**: node-run IDs are unique per run; every
  dependency names a node run in the same run exactly once (no duplicates),
  never itself, and dependency chains are acyclic. Event, failure, and
  review-pause node references must resolve to the containing node run, and
  artifacts must name their containing node run as producer.
- **Aggregate consistency**: a `succeeded` run contains only `succeeded` or
  `cache_reused` nodes; a `queued` run contains only `queued` nodes; a
  terminal run contains no nonterminal nodes.
- **Attempts** count executions: `queued`, `dependency_skipped`, and
  `cache_reused` records must carry `attempt: 0`; every other state requires
  a positive attempt number. Recovery records the voided attempt
  structurally on the appended `node_queued` event, so the next execution
  can compute its fresh attempt number without parsing message text.
- **Identity shape**: recorded identities must match
  `brainlearn-v1:<domain>:<sha256 hex>` with the domain matching the field
  (`node`, `environment`, `workflow`, `artifact`); input values accept any
  domain; `sha256` fields must be 64 lowercase hex characters.
- **Identity truth**: each node run's `content_identity` is recomputed from
  its declared fields and a mismatch is rejected, so stale parameters cannot
  reuse a previous cache entry. Every node run must reference the environment
  identity computed from the containing run's `EnvironmentRecord`; per-node
  environments require persisted environment records (a later-slice design).

## Content identities

`content_identity(domain, payload)` canonicalizes the payload to compact JSON
with sorted keys, encodes UTF-8, and returns
`brainlearn-v1:<domain>:<sha256>`. Domains are `workflow`, `node`,
`environment`, and `artifact`.

`node_content_identity` accepts only computation-changing inputs: node type
and implementation version, input identities keyed by port, parameter values,
environment identity, seed, and execution settings. Timestamps, canvas
positions, display labels, and absolute project paths are excluded by
construction — the helper has no parameters for them. Mapping insertion
order never changes an identity; list order is caller-defined and
significant. Numerically equal numbers share one representation
(`1`/`1.0`, `-0.0`/`0`, including nested values), so JSON, Python, and
generated code agree; genuinely different floats keep distinct hashes, and
non-finite floats are rejected. The embedded `schema_version` marker always
wins over caller fields, so it cannot be spoofed under the trusted prefix.

Cache reuse (Step 4D) compares these identities: equal identities mean the
same computation and may reuse stored outputs; any declared input change
must change the hash and therefore invalidate dependent work.

## Restart expectations for later slices

- Only nonterminal runs may resume after a service restart; terminal records
  are immutable history.
- A resumed run replays from persisted records, never from in-memory state.
- `waiting_for_review` survives restart as waiting; its persisted decision,
  once recorded, applies only to the matching `input_identity`.
- Cancellation resumes only from an explicit valid checkpoint (defined with
  the training/checkpoint contracts in later slices).

## Run store and scheduling (Step 4B)

Run records persist inside their owning project at
`<project-root>/runs/<run-id>/run.json`, written atomically through the same
explicit-root authorization, session token, and Host/Origin checks as
projects. Run IDs start with a letter or digit and contain only letters,
digits, dots, underscores, and dashes, so they cannot escape the runs
directory. Run operations require the exact authorized project root with its
manifest and workflow files present; subdirectories are rejected even when
they sit inside an authorized root.

Terminal runs are immutable: any save over a succeeded, failed, or cancelled
record is rejected before writing, preserving the stored file byte-for-byte.
Overlapping creates and saves for one run are serialized by a per-run lock
held across each read-check-write sequence, so a terminal write always wins
over a stale concurrent write and duplicate creates cannot both succeed. The
locks are process-wide (shared by request handlers, recovery, and future
worker threads using `RunStore`); they do not serialize separate processes.

Discovery never follows symlinks and ignores entries without a `run.json`
or with directory names outside the run-ID grammar. A discovered `run.json`
is authorized before reading, and its record ID must equal its directory
name. Unparseable, unmigratable, or invalid run files raise an actionable
error naming the path and cause instead of silently dropping history.

## Worker lifecycle (Step 4C)

Work runs in daemon threads outside request handlers; only the allowlisted
`demo.*` adapters may execute, and unknown node types fail deterministically
as `unsupported_node_type`. Every mutation goes through `RunStore`, so
request, recovery, and worker paths share the serialized writer.

- New runs start from a workflow (`POST /api/runs/start`): node runs are
  created queued with content identities over upstream computation identities,
  then driven in topological order. Existing nonterminal runs resume the same
  way; terminal runs reject resume.
- Attempts come from record plus structured event history
  (`max(node, events) + 1`): fresh nodes start at 1, post-recovery retries
  continue past voided numbers.
- Failures mark the node failed, skip the transitive downstream closure as
  `dependency_skipped`, and fail the run; `demo.review` pauses with a pending
  review that `POST /api/runs/review` approves (resume driving) or rejects
  (fail like any failure).
- Cancellation is cooperative: queued/parked work transitions immediately,
  running adapters observe a flag, repeated or post-terminal cancels are
  idempotent, and the cancel call waits only a bounded join.
- Outputs stage under `runs/<id>/staging/<node>/attempt-<n>/` and promote
  atomically to `runs/<id>/artifacts/` only on success; failed, cancelled,
  or interrupted staging is quarantined, never promoted, and prior history
  is never deleted. Staging reservation and input resolution run inside the
  node-attempt failure boundary, so a setup containment refusal produces a
  structured node failure with downstream skips and a terminal failed run
  without following symlinks.
- `GET /api/runs/events` streams `text/event-stream` progress with `after`
  replay for reconnects.
- Unexpected adapter crashes fail the node as `adapter_crash` and are filed
  to the date-folder/hour-file fault log; unhandled request faults are filed
  there too.

### Promotion containment and atomicity (Step 4C repairs)

Every staged output validates before anything is read or moved: a canonical
relative path with no empty, dot, parent, drive, or root segments; source
and destination resolved inside their expected roots with symlinked files
and path components rejected; unique declared ports and destinations; every
required manifest output emitted; no reserved `complete/` prefix; existing
regular-file sources only. Worker-owned assembly, artifact, and quarantine
directories are reserved before use and refuse symlinked components; an
adapter must never precreate the assembly directory, and an occupied attempt
directory fails the node instead of overwriting history. Validated files
assemble in a `complete/` directory that renames into
`artifacts/<node>/attempt-<n>/` with one atomic `os.replace`. A
cancellation or crash after promotion quarantines the unreferenced attempt
to the first free `*-uncommitted` name (never overwriting a symlink or
earlier quarantine) or removes it by a contained non-symlink operation when
no free name exists, before the terminal record transition, so the final
tree never holds unreferenced attempts. Recovery additionally moves any
final attempt directory no successful record references into the first free
`*-orphaned` quarantine name, removing it only when quarantine cannot
complete.

### Recovery, resume, and cancellation accounting (Step 4C repairs)

`POST /api/runs/recover` runs worker-level recovery: leftover attempt
staging quarantines first to the first free `*-interrupted` name (or is
safely removed when quarantine cannot complete), records reconcile through
the Step 4B path, and
every reconciled run with schedulable queued work resumes driving while
runs parked on valid reviews stay parked. Cancellation before execution
records `attempt: 0` with no start timestamp; cancellation during execution
keeps the positive attempt and start time. Repeated or post-terminal
cancellation is idempotent. The execution-count predicate
`counts_as_execution(state, attempt)` requires the record attempt and
returns False for queued, skipped,
reused, and attempt-0 cancelled records, and True for running, waiting,
succeeded, failed, and positively-attempted cancelled records.

### Workflow identity scope and adapter manifest (Step 4C repairs)

The workflow identity covers node types, ports, parameter values, and edge
endpoints only. Edge IDs, node labels, descriptions, canvas positions, and
list insertion order never affect it; endpoint, topology, parameter, or port
changes always do. One authoritative `DemoNodeManifest` per adapter declares
its version and required versus optional input and output ports
(`DEMO_NODES` is the single source of truth). Run construction validates
every workflow node against it (unknown ports, direction mismatches, missing
required input/output declarations, and disconnected required inputs fail
the start), persists declared outputs on each node run, and promotion
accepts only outputs declared by both the workflow node and the manifest
while rejecting a successful result that omits a required output.
Downstream inputs resolve to the exact promoted artifact bytes recorded on
the run.

### Workflow identity scope (Step 4C repairs)

The workflow identity covers node types, ports, parameter values, and edge
endpoints only. Edge IDs, node labels, descriptions, canvas positions, and
list insertion order never affect it; endpoint, topology, parameter, or port
changes always do.

### Content-addressed cache (Step 4D)

Each cache entry (`CacheEntry`/`CacheOutput`, schema `1.0`) is keyed by the
existing node content identity, which already binds node type and
implementation version, input identities, parameter values, environment
identity, seed, and execution settings; those fields repeat inside the entry
for audit. The model itself recomputes the identity from its repeated fields
and rejects a stale one wherever the entry is loaded, like `NodeRunRecord`.
Outputs carry port, canonical relative path, media type, size, and SHA-256;
large bytes are never embedded in JSON.

Entries live project-local at `<project>/cache/nodes/<sha>/` with
`entry.json` plus immutable `files/`. Publication stages to
`cache/staging/<sha>-<id>/` (verified size/hash copies plus the entry file)
and renames atomically into place under a per-process per-project lock, so
concurrent identical runs serialize: an existing complete entry wins and the
loser's staging is discarded; a corrupt, incomplete, non-directory, or
unsafe occupant is quarantined to `cache/quarantine/` before replacement. Interrupted
publications leave staging behind and `POST /api/runs/recover` quarantines
it the same way. Symlinked cache components are never followed, prior valid
history is never overwritten, and publish problems are fault-logged without
failing the already-succeeded run.

Before executing a ready node the worker looks up its exact content
identity. A clean miss (no entry) executes normally; a present-but-unusable
entry is a miss with a fault-log diagnostic naming the identity. A hit
requires the entry's repeated computation fields to recompute to its claimed
identity and match the queued node's replay fields exactly, every cached
port to be declared by the node's workflow outputs and allowed by the
current adapter manifest (including every required output), and every file
to resolve inside the entry with no symlinks and matching size and SHA-256.
The node then records `cache_reused` with attempt 0 and no timestamps,
staging every output in a worker-owned temporary tree, reverifying bytes at
the commit boundary, and renaming the complete tree atomically into the
per-run `cache-reused/` artifacts; any copy, hash, cancellation, or
persistence failure removes or quarantines both staging and final reuse
trees so no partial tree survives. Cancellation during reuse cleans both
trees and persists the normal pre-execution cancellation (attempt 0, no
timestamps, no artifacts, terminal cancelled run); any other reuse failure
falls back to ordinary execution, so nothing escapes to strand the record.
A `run_started` transition applies when the run was still queued, alongside the structured `cache_reused` event with
attempt 0. Failure, cancellation, review pauses, and zero-output control
nodes never publish. A regular file occupying a cache key is quarantined
before the replacement is published, so the key heals on the next run;
symlink occupants continue to be refused without touching external targets. Cross-platform reuse requires an exact
environment-identity match, so platform differences invalidate rather than
silently reuse; changing any of parameter, input, implementation version,
environment, seed, or setting input changes the identity and therefore
invalidates exactly the affected node and its transitive descendants.

The scheduler (`brainlearn_core.scheduler`) is pure dependency logic:
`topological_order` returns a deterministic execution order; `ready_node_ids`
returns queued nodes whose dependencies all completed (`succeeded` or
`cache_reused`); `downstream_ids` returns transitive dependents for skip
propagation after failures. All three entry points share one graph
validation rule (known references, unique edges, acyclicity) and one
state-key rule (every graph node must have a state; extra keys are ignored).
Downstream queries additionally reject unknown source IDs so a misspelled
failure cannot silently skip nothing.

Restart recovery (`RunStore.recover_runs`) voids interrupted `running` nodes
back to `queued` with `attempt` reset to 0 — queued work never carries an
attempt number — and records the voided attempt number in an appended
`node_queued` event; the next execution assigns a fresh positive attempt.
Pending reviews survive unchanged. The run follows its nodes: waiting when a
review is pending, running while completed siblings exist, queued only when
nothing ever completed (with a `run_queued` event). Terminal runs are never
modified.
