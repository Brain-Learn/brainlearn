# BrainLearn EEG cohort export

Source cohort: `{cohort_id}`. This portable project contains code and
provenance, not EEG recordings or BrainLearn artifacts. It is research-only and
experimental. Independent EEG methods approval is pending. It makes no
clinical, inferential, or population-generalization claim.

## Files

- `workflow.py`: direct MNE-Python mode. It verifies source checksums, applies
  recorded bad-channel choices only after explicit acknowledgement, refits
  ICA, and requires a fresh human decision before applying component
  exclusions.
- `replay_brainlearn.py`: exact runner mode. It launches the pinned BrainLearn
  CLI against a local project and loopback service. The CLI requests fresh QC
  decisions. A browser is not required.
- `analysis.ipynb`: setup, input inspection, QC decisions, processing, and
  results in separate sections.
- `workflow.json`, `config.json`, and `provenance.json`: saved graph,
  parameters, source identities, per-recording outcomes, QC decisions, output
  identities, environment, and replay limits.
- `environment.lock`: hash-pinned dependencies for direct MNE mode.
- `citations.json` and `expected_outputs.md`: methods citations and expected
  output descriptions.

## Direct MNE mode

Place a local BIDS dataset in `./dataset` or pass its location with
`--dataset-root`. The files must match the relative paths, sizes, and SHA-256
hashes recorded in the export. The workflow never resamples, interpolates, or
approves QC automatically.

```bash
uv venv --python 3.12
uv pip sync --python .venv/bin/python environment.lock
.venv/bin/python workflow.py \
  --dataset-root /path/to/local/bids \
  --reuse-recorded-bad-channel-qc
```

On Windows, use `.venv\Scripts\python.exe` and a local dataset path. The flag
is an explicit acknowledgement of the source-bound bad-channel decisions.
ICA is refit, so the saved component exclusions are documentary only. Inspect
the new components and make a fresh decision at the prompt. The notebook is the
recommended interactive route.

## Exact BrainLearn-runner mode

Install the BrainLearn server version listed in `config.json` and start its
local service:

```bash
python replay_brainlearn.py \
  --project /path/to/your/brainlearn-project \
  --dataset-root datasets/your-bids-dataset
```

The project and data remain local. The CLI reads the session token from the
existing environment or prompts for it; no token is stored in this export. QC
decisions are requested again so replay never silently approves a decision.
Before the CLI contacts the loopback service, the runner verifies every
selected source file against its saved size, SHA-256 checksum, and BIDS input
identity. It rejects absolute paths, path traversal, and symlinks.

## Agreement and provenance

The saved graph, node parameters and versions, participant/session/run
selection, source hashes, base seed, prior decisions, and citations are
recorded exactly. Direct MNE replay refits ICA and requires a new human review.
Floating-point output agreement is tolerance-based; it is not promised to be
bit-for-bit identical across operating systems, BLAS builds, or MNE versions.
The Step 8 engineering comparison used `rtol=1e-7` and `atol=1e-12` for
participant-level ERP/PSD summaries, and `1e-12 V` for epoch samples. These
tolerances do not establish scientific suitability. Output artifact hashes
are recorded in provenance; large source and output files are not bundled.

Before interpretation, review event meaning, filter and epoch settings, both
QC stages, exclusions, failures, and the human-review placeholders in the
generated report.
