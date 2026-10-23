# User guide

BrainLearn organizes a local research project around a directed workflow graph. Each node performs a bounded operation or records an explicit review. Ports carry scientific types and metadata, such as `bids_dataset`, `raw_eeg`, `epochs`, `evoked`, `spectrum`, and `report`.

## Projects and data

A project is a folder selected by the researcher. Workflow drafts and metadata are stored there; large analysis artifacts are stored as files and referenced by identity. The service binds to `127.0.0.1`, checks browser origin and host, and requires a per-session token for filesystem operations. Read the [data and security notes](https://github.com/Brain-Learn/brainlearn/blob/main/docs/threat-model.md) before changing those boundaries.

Use the synthetic fixture in the {doc}`quickstart` while learning. Public datasets must retain their version, citation, license, and checksum information. Private research data should stay on the researcher's machine and must never be committed to Git.

## Nodes and connections

1. Add nodes from the registry or load a versioned example.
2. Connect compatible output and input ports. The validator checks scientific types and required capabilities.
3. Select a node to inspect its parameters and metadata.
4. Validate before running. A valid graph means the declared structure is compatible; it does not prove the method is scientifically appropriate.

The graph is a directed acyclic graph. Loops for repeated model fitting belong inside a structured node, not as graph cycles. Conversions between scientific data types must be explicit.

## Runs and previews

Run records capture workflow version, input identities, parameter values, implementation versions, environment information, QC decisions, outputs, and citations as those fields become available for a node. Previews are bounded summaries and must not be mistaken for full-resolution scientific review.

The current runnable EEG path is BIDS import → signal inspection → MNE filter. The filter reads one recording, leaves source files untouched, and writes a new artifact and provenance record. The other visible nodes communicate the intended workflow and may not execute yet; consult {doc}`status` before relying on one.

## Inspect and export

Inspect run state and node outputs before continuing. Export Python for the demonstrated filter workflow to preserve parameter choices and pinned dependencies in a runnable record. Python and notebook export for the broader workflow remains on the roadmap.

For method details and the numerical comparison, see the [filter demo engineering notes](https://github.com/Brain-Learn/brainlearn/blob/main/docs/eeg-filter-demo.md). For direct instructions, use {doc}`quickstart`.
