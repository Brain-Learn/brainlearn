# Workflow model

The workflow is an explicit, versioned graph rather than a collection of loosely connected files. A port declares what scientific object it accepts or produces. A validator checks graph structure, parameter schemas, scientific type compatibility, and required runtime capabilities before execution.

## Scientific types

The initial type vocabulary includes `bids_dataset`, `raw_eeg`, `reviewed_eeg`, `epochs`, `evoked`, `spectrum`, and `report`. A type change must pass through a node that declares that conversion. File paths or generic arrays do not erase the meaning of a signal.

## Execution

The graph is a directed acyclic graph. A scheduler runs a node only after its inputs are ready, records success or failure, and persists artifact references rather than large arrays in workflow records. Cache identities include input content and processing context so stale results can be invalidated when relevant inputs or settings change.

The application layer validates workflow definitions and manages local projects. Scientific operations run through adapters to the scientific library that owns the algorithm. For EEG, that means MNE-Python and MNE-BIDS. BrainLearn records the selected method and provenance; it does not reimplement their algorithms.

## Quality-control pauses

A QC node can stop execution and save a pending decision. The researcher reviews the evidence and explicitly approves or rejects the proposed action. A pause never approves itself. If the input or processing context changes, a previous decision may become stale and require review again. See {doc}`quality-control`.

## Contracts

The maintained Python API for workflow definitions is documented in {doc}`api`. Versioned data and service boundaries are linked from {doc}`contracts`. Frontend state and provisional adapter internals are not promised as stable public APIs.
