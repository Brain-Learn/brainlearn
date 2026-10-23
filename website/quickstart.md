# Ten-minute EEG walkthrough

This walkthrough runs a small synthetic BIDS EEG recording through BrainLearn's current experimental import, inspection, filter, preview, and Python-export path. It contains no human or participant data. The generated signal is an engineering fixture, not a scientifically representative recording.

## Screenshots

The first image shows a successful run of the three-node filter workflow. The second shows the bounded before/after view for the same synthetic recording. These are interface illustrations, not scientific results.

```{figure} _static/images/workflow-editor.png
:alt: BrainLearn showing the BIDS EEG, Inspect Signal, and Band-pass Filter nodes connected in a successful synthetic-data run.
:width: 100%

The experimental BIDS → inspect → filter path and its saved run artifacts.
```

```{figure} _static/images/filter-comparison.png
:alt: BrainLearn's synthetic EEG comparison showing before and after signal envelopes and power spectra for channel Cz.
:width: 100%

The preview shows a bounded 1–40 Hz filter comparison. The synthetic fixture is an engineering reference, not a suitable research recording.
```

## 1. Install the source checkout

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), Node.js 22 or newer, and npm.

```sh
git clone https://github.com/Brain-Learn/brainlearn.git
cd brainlearn
uv sync --all-packages --group eeg
npm --prefix apps/web ci
```

## 2. Start BrainLearn

In one terminal, start the local API:

```sh
uv run brainlearn-server
```

In a second terminal, start the interface:

```sh
npm --prefix apps/web run dev
```

Open the Vite address shown in the terminal (normally `http://127.0.0.1:5173`). The service prints a session token. Enter that token in the app's local project panel when prompted. The token is kept in the browser session and is used only for local filesystem endpoints.

## 3. Create the synthetic BIDS recording

Stop the API briefly if it occupies the same terminal, or use a third terminal from the repository root:

```sh
python examples/create_synthetic_eeg_bids.py \
  /path/to/brainlearn-project/synthetic/example-bids
```

The generator writes a 10-second BrainVision recording with two EEG channels, a 10 Hz and 12 Hz signal, a deterministic 50 Hz component, and seeded low-amplitude noise. Its manifest records the CC0-1.0 license and checksums.

## 4. Load and run the filter example

In the app:

1. Create a project in an empty local directory.
2. Load the **EEG filter demo** from the examples menu.
3. Set the dataset root to `synthetic/example-bids` if you created it inside the project. Otherwise use the permitted project-relative path where the dataset is stored.
4. Set the recording path to `sub-01/eeg/sub-01_task-Demo_eeg.vhdr`.
5. Keep the example's `1 Hz` low cutoff and `40 Hz` high cutoff, then run the workflow.
6. Open the filter node's comparison preview.

The preview shows bounded signal and Welch-spectrum summaries before and after filtering. It is an inspection aid, not an automatic quality score or recommendation. Filter choice must follow the study's question and be reviewed by the researcher.

## 5. Export the run

Choose **Export Python** to download a standalone script. Install its pinned dependencies in an isolated environment, then run it with explicit dataset, recording, and output paths:

```sh
python -m pip install mne==1.13.2 mne-bids==0.20.0
python brainlearn_eeg_filter.py \
  --dataset-root /path/to/brainlearn-project/synthetic/example-bids \
  --recording-path sub-01/eeg/sub-01_task-Demo_eeg.vhdr \
  --output /path/to/brainlearn-project/brainlearn_filtered_raw.fif
```

Keep the output outside the source BIDS dataset. The script checks source identities and will not replace an existing output unless `--overwrite` is specified.

## What this walkthrough demonstrates

- A project-local BIDS dataset is discovered and opened with MNE-BIDS.
- Signal metadata is inspected without changing the source recording.
- A version-pinned MNE FIR filter is applied with explicit units and bounded settings.
- A before/after preview and provenance are saved with the run.
- Generated code can reproduce the demonstrated processing path.

The engineering gate compares the adapter, generated script, and a direct MNE call on this synthetic fixture with a maximum absolute difference of `2e-5 µV` (`rtol=1e-6`, `atol=2e-5 µV`). This tolerance accounts for FIF single-precision storage. It does not validate scientific filter choices. A public sample-bearing EEG dataset and independent methods review are still required for scientific completion.

Download the [versioned example workflow JSON](https://raw.githubusercontent.com/Brain-Learn/brainlearn/main/examples/eeg-filter-demo.workflow.json) and the [synthetic fixture manifest](https://raw.githubusercontent.com/Brain-Learn/brainlearn/main/examples/synthetic-eeg-bids-manifest.json). The manifest records the generator checksum and checksums of every generated fixture file.

## Keyboard and accessibility notes

The canvas supports keyboard selection and node insertion through the registry controls. Use the visible buttons for creating/opening projects, loading an example, validating, running, and exporting; the canvas is not the only path to these actions. Use Tab and Shift+Tab to move through controls and Enter or Space to activate them. A selected node's details appear in the inspector panel. If the canvas itself is difficult to operate with a keyboard, use the registry and node inspector controls and report the issue in GitHub.

```{admonition} Research-only software
:class: warning

This example uses synthetic data and demonstrates engineering behavior only. It does not establish signal quality, clinical value, or suitability for a research question.
```
