# Canvas interaction, customization, and dataset access roadmap

This roadmap refines the user-interface and data-acquisition work requested
after the first live Step 4C smoke test. It does not displace the active Step
4C repair gate. Implementation remains ordered as Step 4C repairs, Step 4D
cache/invalidation, Step 4E canvas and demonstration UX, then Step 5 dataset
acquisition and BIDS EEG inspection.

## Current interaction diagnosis

The React Flow canvas is controlled from the persisted `Workflow`, but the UI
only handles `onNodeDragStop`. It does not apply `onNodesChange` while the
pointer moves. Existing nodes therefore cannot track the pointer smoothly and
may appear immovable or jump at the end of a gesture.

The registry buttons only call `addManifestNode`, which chooses a generated
grid position. There is no drag payload, `onDragOver`, `onDrop`, or conversion
from screen coordinates to flow coordinates. A user therefore cannot drag a
registry item to a chosen position on the canvas.

React Flow documents both requirements: controlled graphs apply node changes
during interaction, while sidebar drag-and-drop uses pointer/native drag events
and `screenToFlowPosition` to calculate the drop position:

- <https://reactflow.dev/learn/concepts/adding-interactivity>
- <https://reactflow.dev/examples/interaction/drag-and-drop>

## Step 4E.1 — Direct manipulation and smooth motion

Implement the interaction foundation before visual decoration:

1. Add transient canvas node state that applies React Flow node changes during
   a drag. Persist the final position to `Workflow` once at drag end so one
   gesture creates one undo entry and does not trigger backend validation on
   every pointer event.
2. Make registry items draggable into the canvas. Use pointer events as the
   common mouse/touch path where practical, translate the release point with
   `screenToFlowPosition`, instantiate exactly one manifest-backed node there,
   select it, and create one undo entry.
3. Preserve click-to-add and keyboard activation as accessible alternatives.
   Dropping outside the canvas changes nothing. Escape cancels a pending drag.
4. Show a drag preview and a clear canvas drop target. Prevent accidental text
   selection, duplicate drops, and placement underneath fixed side panels.
5. Keep direct pointer tracking immediate. Animate discrete layout changes:
   node insertion/removal, inspector and run-drawer transitions, validation
   badges, and programmatic viewport fitting. React Flow supports a duration
   for animated `fitView`; use short motion rather than a new animation
   dependency initially.
6. Respect `prefers-reduced-motion`. Disable nonessential movement when it is
   requested and never animate scientific plots in a way that obscures values.
7. Add optional alignment/grid snapping only after ordinary drag behavior is
   stable. Do not copy React Flow Pro-only examples or code unless their license
   is reviewed and accepted.

Acceptance gate:

- A palette node follows the pointer and lands within a small tolerance of the
  release point at 50%, 100%, and 150% zoom.
- An existing node tracks the pointer continuously, persists after save/open,
  and undo/redo treats the full gesture as one action.
- Click and keyboard addition still work.
- Mouse and touch/pointer tests cover drop, cancel, outside-canvas release, and
  duplicate-event prevention.
- Motion has no large layout shift, does not delay direct dragging, and is
  disabled under reduced-motion preference.

## Step 4E.2 — Safe node customization

Separate three kinds of customization so visual preferences cannot silently
change scientific behavior:

### Instance presentation

Allow a researcher to change the instance label, notes, accent color from an
accessible palette, collapsed/expanded state, and optional tags. Preserve
these fields in project files and exports, but exclude them from node content
identity and cache invalidation.

### Scientific parameters

Continue generating controls from the manifest parameter schema. Add explicit
basic/advanced sections, units, defaults, allowed ranges/options, inline help,
reset-to-default, and visible validation. Parameter changes must affect content
identity and invalidate only the appropriate downstream cache.

### Node definition

Keep manifest ID, implementation version, port types, review behavior,
capability requirements, citations, and license metadata immutable for an
ordinary node instance. Creating a new scientific operation is plugin work and
must follow the contributor review path; it is not a free-form “custom code”
field in the canvas.

Add duplicate-node and save-as-preset actions after the model above is stable.
A preset references a manifest version plus parameter values and presentation
defaults. It cannot override ports, execution code, citations, or licensing.

Acceptance gate:

- Presentation edits round-trip through save/open and do not change computation
  identity.
- Parameter edits are typed, validated, undoable, and change computation
  identity.
- Attempts to alter manifest-governed fields are rejected by both UI and core
  validation.
- Custom labels and colors remain readable in light/dark and high-contrast
  conditions; color is never the only status signal.

## Step 5A — Curated free-access dataset library

Start with a curated BrainLearn catalog instead of arbitrary URL downloads.
The catalog is metadata, not bundled research data. Each entry records:

- provider and stable dataset identifier;
- immutable release/snapshot version;
- title, modality, task, participants, formats, and approximate byte size;
- access class and exact license/reuse statement;
- dataset DOI/citation and provider landing page;
- expected files, checksums where the provider exposes them, and the compatible
  BrainLearn template/version;
- curator, review date, and known limitations.

Provider order:

1. **OpenNeuro first.** It is aligned with the initial BIDS EEG milestone,
   exposes search metadata through its API and download tooling, and states
   that published datasets are available free of charge and released into the
   public domain. Use pinned snapshots, never a mutable latest version.
   <https://docs.openneuro.org/>
2. **DANDI second, when NWB/electrophysiology support is scheduled.** Its REST
   API supports dataset and asset discovery and public data can be downloaded
   through the client/API/S3 mechanisms. Preserve each Dandiset's own license
   metadata. <https://docs.dandiarchive.org/api/rest-api/>
3. **PhysioNet open-access records only in the first integration.** PhysioNet
   distinguishes open, restricted, and credentialed access. BrainLearn must
   not bypass an account, credentialing, or data-use agreement; restricted and
   credentialed entries should open the provider instructions and remain
   unavailable to the automatic downloader until a separate credential design
   is approved. <https://physionet.org/about/database/>

## Dataset-library user flow

1. Open **Datasets** and search/filter by modality, task, format, size, license,
   and compatible workflow.
2. Inspect a detail panel showing scientific context, subjects/sessions,
   download size, disk-space requirement, exact version, license, citation,
   and limitations before enabling download.
3. Choose a destination under the active authorized project. The Python service
   performs the transfer directly to local storage; the browser never proxies
   dataset bytes and BrainLearn does not upload them elsewhere.
4. Show queued/downloading/verifying/ready/failed/cancelled states with byte
   progress, speed, remaining size, retry, pause/cancel when supported, and an
   actionable error. Browser closure must not lose the transfer record.
5. Download to an attempt-specific partial directory. Verify expected size and
   available checksums, validate safe paths and BIDS structure, then atomically
   promote the completed snapshot. Quarantine or clean incomplete downloads.
6. Write a dataset lock record containing provider, dataset ID, exact snapshot,
   retrieval time, local relative path, file/content identities, license, and
   citations. Workflow input nodes reference this record.
7. Offer **Open local dataset** beside the catalog so offline, private, and
   manually obtained data remain first-class.

## Dataset security and reliability gate

- Allowlist provider hosts and schemes; require HTTPS; revalidate every
  redirect; apply connection/read timeouts and bounded retries.
- Never accept an archive path containing absolute, drive, empty, dot, parent,
  or symlink escape components. Set file-count, expanded-size, and compression
  ratio limits before extraction.
- Check free disk space with a safety margin and obtain explicit confirmation
  for large downloads. Never write outside the selected project dataset root.
- Do not store provider credentials in workflow/project JSON or logs. Public
  downloads require none. A future credential store needs its own threat model.
- Never treat “free to download” as permission to redistribute. Display and
  persist the dataset-specific license and citation.
- Unit tests use a local mock provider with interrupted, resumed, corrupt,
  redirected, oversized, traversal, and checksum-mismatch responses. Scheduled
  integration tests may query provider metadata and download only a tiny pinned
  public fixture; ordinary CI does not fetch large datasets.

Completion gate: from the GUI, a clean BrainLearn installation can discover a
curated pinned public EEG dataset, review its size/license/citation, download
and verify it into the active project, cancel and resume safely, reopen the
project offline, and feed the immutable dataset record to BIDS inspection. A
replay on another clean machine retrieves the same snapshot or fails with a
clear provider/version error.
