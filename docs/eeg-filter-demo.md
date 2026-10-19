# EEG filter demo

This is BrainLearn's first executable EEG processing path. It imports one
project-local BIDS EEG recording, reads its acquisition metadata, applies a
version-pinned MNE-Python FIR band-pass to EEG channels, and saves a filtered
FIF artifact. The run also stores a bounded before/after trace and Welch
spectrum comparison and a JSON provenance record.

The workflow is experimental. The preview is for inspecting what the configured
filter did; it is not a signal-quality assessment or evidence that a filter is
appropriate for a research question. The first-look workflow's later ICA,
epoching, ERP, PSD, and report nodes remain illustrative and do not execute.

## Run the deterministic example

1. Create or open a BrainLearn project.
2. Generate the small, synthetic BIDS dataset inside that project:

   ```sh
   python examples/create_synthetic_eeg_bids.py \
     /path/to/project/synthetic/example-bids
   ```

   The fixture is a generated two-channel, 10-second BrainVision recording.
   It contains a 50 Hz component and deterministic low-amplitude noise for
   engineering comparisons; it contains no human or participant data. Its
   CC0-1.0 license, generator version, and file checksums are recorded in
   `examples/synthetic-eeg-bids-manifest.json`.
3. Load the **EEG filter demo** example. Its dataset root defaults to
   `synthetic/example-bids`. Set **Recording path** to
   `sub-01/eeg/sub-01_task-Demo_eeg.vhdr`.
4. Run the workflow. Open **View comparison** on the filter's preview artifact
   to see bounded before/after signal envelopes and spectra.
5. Choose **Export Python** to download `brainlearn_eeg_filter.py`. The script
   records its expected dependencies and accepts dataset, recording, and output
   paths as command-line arguments.

The standalone script requires MNE-Python 1.13.2 and MNE-BIDS 0.20.0. Install
those pinned packages in an isolated Python environment before running it:

```sh
python -m pip install mne==1.13.2 mne-bids==0.20.0
python brainlearn_eeg_filter.py \
  --dataset-root /path/to/project/synthetic/example-bids \
  --recording-path sub-01/eeg/sub-01_task-Demo_eeg.vhdr \
  --output /path/to/project/brainlearn_filtered_raw.fif
```

The output must be outside the source BIDS dataset. The script checks source
file hashes before and after processing and stops if they change. Existing
outputs are preserved unless `--overwrite` is explicitly supplied.

## Filter and preview conventions

The node registry advertises the `brainlearn.eeg.mne-filter.python` renderer
(`1.2.1`, Python) on the filter node. The renderer response envelope is schema
`1.0`; it includes the renderer version, pinned dependencies, citations, and
generated source.

- `low_hz` is the high-pass cutoff; its accepted range is 0.1–100 Hz.
- `high_hz` is the low-pass cutoff; its accepted range is 0.5–200 Hz and it
  must be strictly below the input's Nyquist frequency.
- The low cutoff must be less than the high cutoff.
- Optional `notch_hz` accepts up to 16 explicit, unique, ascending center
  frequencies. Leave it blank to disable notch filtering. Each center must lie
  strictly inside the configured pass band and below Nyquist; the app does not
  infer a mains frequency from recording metadata. `notch_width_hz` accepts
  0.1–10 Hz and is passed to MNE's FIR notch operation.
- MNE uses FIR, zero phase, `firwin`, a Hamming window, automatic filter length
  and transition bandwidths, `reflect_limited` padding, and its `edge` and
  `bad_acq_skip` annotation skip behavior.
- All EEG channels are filtered. Other channel types are preserved unchanged.
- The node limits processing to 256 recording channels and 25 million
  channel-samples to bound memory use. The comparison preview includes at most
  four EEG channels and four seconds, with at most 240 trace bins and 513
  spectrum bins per channel.
- Signal traces are shown in µV. Welch power is reported as µV²/Hz; the UI
  displays it in decibels using `10 log10(power)` with a finite floor.
- The original BIDS recording is read but never rewritten. Its content identity
  is checked both before and after processing. The identity and standalone
  Python export include the dataset description, participant/session tables,
  and all applicable inherited metadata sidecars MNE-BIDS can read, including
  events JSON, electrodes, and coordinate-system metadata.

## Reference comparison

The Step 6 test gate compares the worker adapter output, a separately written
direct MNE call, and the executed generated script on the same deterministic
sample-bearing BIDS fixture. The accepted maximum absolute difference is
`2e-5 µV` (`rtol=1e-6`, `atol=2e-5 µV`) comparing the adapter and generated
FIF readbacks with the independent in-memory MNE result. The tolerance covers
MNE's single-precision FIF storage. This fixture exercises a 50 Hz notch with
a 1–70 Hz pass band. It is an engineering comparison on synthetic data, not
scientific validation of filter choices. A sample-bearing, licensed public
BIDS recording remains required for the later scientific reference gate.

MNE-Python citation: [10.3389/fnins.2013.00267](https://doi.org/10.3389/fnins.2013.00267).
MNE-BIDS citation: [10.21105/joss.01896](https://doi.org/10.21105/joss.01896).
