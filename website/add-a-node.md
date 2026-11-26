# Add an in-tree workflow node

This worked example shows how to add a small node that summarizes channel-type
counts already recorded by the `eeg.inspect` node. It reads a persisted JSON
report and writes another JSON report. It does not read EEG samples, transform a
signal, or make a scientific inference.

This page is for contributors changing BrainLearn itself. The Python modules
under `packages/server` are implementation details, and their classes and
helpers are **not a supported third-party plugin API**. A node runs only after
its type and adapter have been added to BrainLearn's curated worker allowlist;
workflow JSON cannot provide Python code, module paths, or shell commands.
The repository's [contribution rules](contributing.md) and
[pull-request workflow](https://github.com/Brain-Learn/brainlearn/blob/main/docs/github-workflow.md)
still apply.

## The example contract

The existing `eeg.inspect` node emits an `application/json` report with a
`source_content_identity` and an `inspection` object. The latter includes
`channel_count` and a mapping such as `{"eeg": 60, "eog": 2}`. The new
`report.channel_types` node will:

- accept one `report` input and produce one `summary` output, both typed as
  `report`;
- validate the input artifact and the counts before writing anything;
- preserve the source content identity in its output;
- use a deterministic JSON representation and no extra software dependency;
- fail with a readable node error for invalid input.

The counts describe what `eeg.inspect` already measured. The new node adds no
measurement or interpretation. Its node status should therefore remain
`experimental`, consistent with the current EEG workflow.

## 1. Set up the development environment

From a clean checkout, install the locked project and optional EEG dependencies:

```bash
uv sync --locked --all-packages --group docs --group eeg
```

Read [local development](https://github.com/Brain-Learn/brainlearn/blob/main/docs/development.md),
the [node dependency metadata contract](https://github.com/Brain-Learn/brainlearn/blob/main/docs/node-dependency-metadata.md),
and the adapter closest to the behavior you plan to add. For this example,
inspect `packages/server/src/brainlearn_server/demo_nodes.py`,
`mne_analysis.py`, and `registry.py`, plus `tests/test_eeg_analysis.py`,
`tests/test_registry.py`, and `tests/test_worker.py`.

## 2. Define the visible typed manifest

`NodeManifest` is the registry description exposed to workflow validation and
the UI. Add an entry to `_EEG_MANIFESTS` in
`packages/server/src/brainlearn_server/registry.py`:

```python
NodeManifest(
    id="report.channel_types",
    node_version="0.1.0",
    label="Channel Type Summary",
    description=(
        "Summarize channel-type counts already recorded by signal inspection; "
        "does not read or transform EEG samples."
    ),
    category="Reports",
    status="experimental",
    ports=[
        _port("report", "Inspection report", PortDirection.INPUT, ScientificType.REPORT),
        _port("summary", "Channel type summary", PortDirection.OUTPUT, ScientificType.REPORT),
    ],
    license=EXAMPLE_LICENSE,
)
```

This example uses no parameters, method citation, or new third-party package.
Do not add an MNE dependency to a node that only parses JSON. The upstream
`eeg.inspect` manifest already records the software used to create its report.
Attach method and software citations to the manifest of the node that actually
performs that operation; the report layer reads citation metadata from those
manifests.

When a method paper applies, the registry field has this shape (replace every
placeholder with a reference verified for the implemented operation):

```python
citations=[
    CitationMetadata(
        title="Verified paper title for this operation",
        doi="10.xxxx/verified-doi",
        url="https://doi.org/10.xxxx/verified-doi",
    )
]
```

When the adapter itself calls MNE, its dependency metadata can use the pinned
MNE record already defined in `registry.py`:

```python
software_dependencies=_software_dependencies(_MNE_DEPENDENCY)
```

That record supplies the MNE distribution name, version, license, software
citations, and current installation status. Do not add it to the metadata-only
example node, and do not use a software citation as a substitute for a method
reference.

For a node that performs a scientific method, record its method reference in
`NodeManifest.citations`. Record each package the adapter actually uses in
`software_dependencies`, including its pinned version, SPDX license, and
software citations. Those fields answer different questions: cite the method
that was performed and the software that performed it. Do not copy a citation
from another node unless it applies to this operation. See the current
[BrainLearn citation guidance](citation.md) and
[dependency metadata notes](https://github.com/Brain-Learn/brainlearn/blob/main/docs/node-dependency-metadata.md).

## 3. Implement the adapter

The execution manifest and adapter are defined in
`packages/server/src/brainlearn_server/demo_nodes.py`; MNE-oriented adapter
implementations live in `mne_analysis.py`. The current code uses an execution
manifest for allowed inputs and outputs, the handler, version, and cache
policy. The adapter receives a `NodeContext` and returns staged outputs. The
worker, rather than the adapter, validates and promotes those outputs into the
run record.

The following fragment follows the current in-tree adapter pattern. The
underscored copy and work-directory helpers are private repository helpers;
they are shown only to explain this codebase and are not public extension
points.

```python
def channel_type_summary_adapter(ctx: NodeContext) -> list[StagedOutput]:
    _require_cancel(ctx)
    artifact = ctx.input_artifacts.get("report")
    if artifact is None or artifact.media_type != "application/json":
        raise ControlledFailure(
            "Channel Type Summary requires the JSON report from signal inspection."
        )

    work_dir = _ica_work_dir(ctx, "channel-type-summary-work")
    try:
        source = work_dir / "inspection-report.json"
        _verified_copy(ctx, "report", source)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControlledFailure("The inspection report is not readable JSON.") from exc
        if not isinstance(payload, dict):
            raise ControlledFailure("The inspection report has an unsupported structure.")

        inspection = payload.get("inspection")
        source_identity = payload.get("source_content_identity")
        counts = inspection.get("channel_types") if isinstance(inspection, dict) else None
        if not isinstance(source_identity, str) or not source_identity:
            raise ControlledFailure("The inspection report has no source content identity.")
        if not isinstance(counts, dict) or not counts:
            raise ControlledFailure("The inspection report has no channel-type counts.")
        if any(
            not isinstance(name, str)
            or not name
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            for name, count in counts.items()
        ):
            raise ControlledFailure("The inspection report contains invalid channel-type counts.")

        channel_count = inspection.get("channel_count")
        if (
            isinstance(channel_count, bool)
            or not isinstance(channel_count, int)
            or channel_count != sum(counts.values())
        ):
            raise ControlledFailure(
                "The channel-type counts do not agree with the inspection channel count."
            )

        result = {
            "schema_version": "1.0",
            "source_content_identity": source_identity,
            "channel_count": channel_count,
            "channel_types": dict(sorted(counts.items())),
        }
        output = ctx.staging_dir / "channel-type-summary.json"
        _write_json(output, result)
        _require_cancel(ctx)
        return [StagedOutput("summary", output.name, "application/json")]
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
```

In this module, import the existing `json` and `shutil` modules and the
in-tree `NodeContext`, `StagedOutput`, `ControlledFailure`, `_require_cancel`,
`_ica_work_dir`, `_verified_copy`, and `_write_json` symbols as needed. Keep
adapter imports lazy at the execution boundary, as the current EEG wrappers do,
so optional scientific packages are not imported just to list node manifests.

The checks are deliberate. A port type says what kind of artifact is expected;
it does not prove that the artifact bytes are valid or have the expected
schema. Validate both. Use `ControlledFailure` for expected bad input so the
worker can record a useful `node_failed` reason. Check cancellation around
work, and write only under the attempt's staging directory. Let the worker
promote outputs after success; do not publish a partial artifact yourself.

## 4. Add the worker contract and registration

Add a wrapper beside the existing EEG wrappers in `demo_nodes.py`:

```python
def eeg_channel_type_summary_adapter(ctx: NodeContext) -> list[StagedOutput]:
    from brainlearn_server.mne_analysis import channel_type_summary_adapter

    return channel_type_summary_adapter(ctx)
```

Then add an entry to `EEG_NODES` in the same file:

```python
"report.channel_types": DemoNodeManifest(
    version="0.1.0",
    inputs={"report": True},
    outputs={"summary": True},
    handler=eeg_channel_type_summary_adapter,
),
```

The identifier, version, port IDs, and requiredness must agree with the UI
manifest. The `report` input must be connected; the adapter must emit the
required `summary` port. Keep this node cacheable only while its output is a
deterministic function of the input artifact and its recorded settings. If it
later reads external state, use randomness, or depends on unrecorded settings,
fix the identity/provenance contract or disable caching.

`demo_nodes.py` contains the execution allowlist. Adding a manifest alone does
not make code executable, and adding a function alone does not make it
available in workflows. The worker rejects undeclared ports and missing
required inputs or outputs before completing a run. An unknown node type fails
as `unsupported_node_type`; it never imports a module named in workflow JSON.

## 5. Validate the graph and write focused tests

The registry manifest supplies `report` input and `report` output types, so
the connection from `eeg.inspect.report` is type-compatible. Add registry
coverage that the node manifest round-trips, has a unique ID, and can be
instantiated. Add a graph-validation case for the valid connection and a
negative case for a deliberately incompatible port type. The existing
validator reports graph-level problems such as unknown ports, missing required
parameters, wrong port directions, incompatible types, and cycles before
execution.

Test the adapter and the worker separately:

| Layer | Evidence to add |
| --- | --- |
| Adapter | Valid input writes exactly one JSON summary, preserves the source identity, and leaves the input artifact unchanged. |
| Adapter errors | Wrong media type, malformed JSON, missing identity, missing counts, inconsistent totals, and malformed count values produce actionable `ControlledFailure`s. |
| Cancellation | A cancelled context stops work before a successful output is returned. |
| Registry | The visible node and allowlisted worker manifest agree on ID, version, ports, and requiredness. |
| Worker | A connected synthetic workflow succeeds and records the output; an invalid adapter input fails the node and skips dependent nodes without publishing a successful partial output. |

The example only reformats recorded metadata, so it does not need a numerical
MNE reference test. Any new signal operation does: write a direct-library
reference independently of the adapter, declare method-specific tolerances
before comparing results, and verify source bytes are unchanged. A UI
screenshot is not scientific verification.

Run the focused tests while iterating, followed by the complete repository
gate before opening a pull request:

```bash
uv run --locked --all-packages --group eeg pytest -q \
  tests/test_registry.py tests/test_worker.py tests/test_eeg_analysis.py
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/core/src packages/server/src
uv run pytest -q
npm --prefix apps/web run lint
npm --prefix apps/web run format:check
npm --prefix apps/web run test
npm --prefix apps/web run build
npm --prefix apps/web audit --omit=dev
uv run --locked --group docs --group eeg sphinx-build -E -a -n -W \
  --keep-going -b html website /tmp/brainlearn-docs
uv run --locked --group docs --group eeg sphinx-build -E -a -n -W \
  --keep-going -b linkcheck website /tmp/brainlearn-linkcheck
npm ci --prefix website
npx --prefix website playwright install chromium
npm --prefix website run check -- /tmp/brainlearn-docs
uv lock --check
git diff --check
```

## 6. Handle errors, artifacts, and provenance

- Raise `ControlledFailure` with a concise corrective message for invalid
  parameters, unreadable input, unsupported metadata, or a scientifically
  incompatible recording. Do not silently resample, interpolate, reinterpret,
  or approve QC.
- Raise the review-control exception only when the node must pause for a
  persisted researcher decision. A QC node must not approve itself.
- Return `StagedOutput` records whose port IDs are declared and whose paths
  stay inside the worker-provided staging directory. The worker checks the
  declared output contract and publishes successful outputs atomically.
- Set a new node version when computation semantics change. The run record
  stores the node version, parameters, input identities, environment, seed,
  and output identities; the version also participates in node content
  identity and cache validation. Citation lists are assembled by the report
  layer from the registry manifest and dependency metadata, so keep those
  entries accurate for the operation.
- Keep large arrays and signals in artifacts, not workflow JSON or run-record
  fields. Reports should reference artifact identities and describe their
  actual contents.

For the example summary, the output record should identify the same source as
the upstream inspection report and contain only the reported count and
channel-type mapping. It is descriptive metadata, not a quality decision or a
claim about signal quality.

## 7. Finish the contribution

Follow the repository's sequential implementation assignment. Include the
focused tests and complete gate in the pull-request description, state any
scientific assumptions, and leave monitor-owned checklist items unchecked.
Ask for independent scientific review when a node changes or introduces a
scientific operation; engineering agreement alone does not approve a method.

The worked node is an in-tree example, not an extension promise. External
plugin discovery, packaging, isolation, compatibility guarantees, and a stable
third-party API require a separate design and review.
