# Reproducibility and provenance

Reproducibility has levels. Re-running the same code in the same environment is different from agreement across computers or from independent scientific replication. BrainLearn records context needed to interpret a run; it does not promise bit-for-bit identity across every operating system, library version, or hardware backend.

## What a run should record

- Workflow and schema version.
- Input identity and dataset version.
- Parameter values and explicit QC decisions.
- Node and scientific-library versions.
- Environment and execution settings, including seeds where relevant.
- Output artifact identities and citations.

Large arrays and recordings remain files. Run records store references and identity metadata. The filter demo also checks that source BIDS files remain unchanged and writes results outside the source dataset.

## Replaying the EEG filter example

Use {doc}`quickstart` to create the deterministic synthetic BIDS fixture, run the app, and execute the generated Python export. The expected comparison tolerance and its FIF precision rationale are described there and in the [engineering reference](https://github.com/Brain-Learn/brainlearn/blob/main/docs/eeg-filter-demo.md).

The synthetic fixture is suitable for repeatable software comparisons. It is not evidence that a method is appropriate for human EEG or for any particular study.

## Data identity and licenses

For public data, record the source, exact snapshot/version, license, citation, and checksums. For local private data, keep files in the selected project root; do not put research data in issue reports or commits. A checksum identifies bytes but does not grant the right to share them.
