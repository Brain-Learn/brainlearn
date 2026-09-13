# Branched demonstration workflow (Step 4E.3)

`examples/demo-branched.workflow.json` (schema `1.0`) is the shared,
non-scientific fixture for the Step 4 interaction and execution gates. Every
node runs locally through the allowlisted worker adapters, but nothing it
produces has numerical or neuroscience validity: outputs are canned bytes and
review pauses carry no scientific judgment.

## Topology

| Node | Type | Role |
|---|---|---|
| `source` | `demo.copy` (`text="trunk-bytes"`) | Trunk start; its text seeds downstream identities |
| `bridge` | `demo.relay` | Forwards `source` output bytes; trunk middle |
| `sink` | `demo.copy` (`text="sink-bytes"`) | Trunk end; descendant of `source` and `bridge` |
| `timer` | `demo.delay` (`seconds=0.2`) | Independent branch with no edges; cancellation target |
| `gate` | `demo.review` | Standalone review pause; approval/rejection target |

Edges `source.output → bridge.in` and `bridge.output → sink.in` form the only
chain; all ports are `raw_eeg` so demo-to-demo connections validate. The graph
is acyclic. `demo.fail` is intentionally absent from the file so the trunk can
succeed; add it from the palette for failure-propagation exercises.

## Intended exercises

- **Success**: run the fixture and approve `gate`; trunk, timer, and gate all
  complete.
- **Failure propagation**: add `demo.fail` from the palette, connect it
  downstream, and run; the failure is controlled and reported, never a shell
  or traceback leak.
- **Review**: approve `gate` to finish the run; reject it to fail fast with a
  structured reason.
- **Cancellation**: cancel while `timer` is waiting; the delay adapter observes
  cancellation cooperatively and no partial success is recorded.
- **Restart recovery**: restart the service mid-run; queued work resumes from
  persisted records.
- **Cache reuse**: rerun unchanged; the three output-producing trunk nodes
  report `cache_reused` with attempt 0.
- **Identity stability without reuse**: `timer` and `gate` produce no outputs,
  so the cache contract never publishes entries for them. They keep stable
  content identities across identical reruns but execute again (attempt 1 each
  run) instead of reusing.
- **Exact invalidation**: change `source` text to invalidate `source`,
  `bridge`, and `sink` only; `timer` and `gate` keep their identities.

## Limitations

- Adapter outputs are fixed canned bytes; parameter changes alter bytes but
  never emulate signal processing.
- The `0.2` second delay keeps the suite fast; lengthen it locally for manual
  cancellation timing.
- Ports reuse the `raw_eeg` scientific type for connection checking only; the
  `Demonstration` category and per-node descriptions mark every demo node as
  non-scientific in the UI.
- The fixture pins behavior for the current adapter versions (`0.1.0`); a
  version bump invalidates dependent cache entries by design.

## Contributor notes

- Registry manifests for `demo.*` derive ports and versions from
  `brainlearn_server.demo_nodes.DEMO_NODES` (`demo_manifest`); never describe
  worker ports a second time. Display metadata states non-scientific behavior.
- Build fixture nodes with `instantiate_registered_node` so label, category,
  description, ports, parameters, and review behavior match the registry
  exactly; hand-edited JSON that drifts fails validation with
  `manifest_mismatch`.
- Keep the file acyclic with scientifically typed ports, and keep
  `tests/test_demo_fixture.py` covering load, round-trip, registry validation,
  topology, probe parameters, queued-run construction, exact invalidation, and
  API validation.
