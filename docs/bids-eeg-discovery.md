# BIDS EEG discovery

BrainLearn's initial dataset scan is read-only and metadata-only. It finds raw
EEG recordings in `sub-*/eeg/` and `sub-*/ses-*/eeg/` directories and supports
EDF (`.edf`), BioSemi (`.bdf`), BrainVision (`.vhdr` with matching `.vmrk` and
`.eeg` companions), and EEGLAB (`.set`) recordings, following the [BIDS EEG
file specification](https://bids-specification.readthedocs.io/en/stable/modality-specific-files/electroencephalography.html).
BrainVision companions and EEGLAB `.fdt` data files are not reported as
separate recordings.

The scan requires a BIDS 1.x `dataset_description.json` with a non-empty `Name`
and numeric `BIDSVersion`, as defined in the [dataset description
specification](https://bids-specification.readthedocs.io/en/stable/modality-agnostic-files/dataset-description.html).
Each recording needs an applicable, possibly
inherited EEG JSON sidecar with `TaskName`, `SamplingFrequency`, `EEGReference`,
`PowerLineFrequency`, and `SoftwareFilters`. `TaskName` is retained as required
metadata while the `task-` filename entity remains the recording's task label.
Unsupported BIDS major versions, derivative
datasets, unsupported recording formats, malformed metadata, and missing
required fields are reported as explicit unsupported or incomplete states.

When matching `channels.tsv` and `events.tsv` files exist, the scan summarizes
channel names/count and event count/types. Channel tables are checked for
`name`, `type`, and `units`; event timing columns and values are checked, with
`n/a` accepted for unknown onset or duration as specified by [BIDS events](https://bids-specification.readthedocs.io/en/stable/modality-agnostic-files/events.html).
These optional summaries do not make the scan a complete BIDS validator.

Metadata discovery opens only bounded JSON and TSV files. It does not read EEG
signal bytes, compute input identities, or modify source files. Discovery
reports whether the required metadata is present; it does not validate signal
content or certify scientific compatibility.

## Input content identity

For a ready recording, the user can separately request an input content
identity. BrainLearn hashes the recording, required BrainVision companions,
an existing EEGLAB `.fdt` companion, and the effective inherited EEG JSON,
channels TSV, and events TSV sidecars. Files are opened read-only and hashed in
1 MiB chunks; no source bytes are copied or persisted. The identity uses the
`brainlearn-v1:artifact:` contract over a versioned payload containing the
recording-relative paths, byte sizes, and SHA-256 digests in path order. It
does not depend on the local project path.

Identity creation refuses symlinks, non-regular files, hard-linked files,
files that change during identity creation, and BIDS directories that change
during the operation. The total input is limited to 10 GiB. If the limit is exceeded,
no identity is returned. These hashes identify current source bytes; they do
not imply that the signal has been inspected or that an analysis is
scientifically valid.

## Read-only signal inspection

After discovery reports a recording as ready, **Inspect signal with MNE** opens
it through the pinned MNE-BIDS and MNE-Python versions. The service reports
measured sampling frequency, sample count and duration, channel count and MNE
channel types, marked-bad channel count, annotation count and up to 100
annotation descriptions, and the reader's high-pass and low-pass metadata.
MNE opens recordings with `preload=False`; the raw object is closed after the
summary is read. The standalone dataset-panel action keeps its result in the
current UI session. The `eeg.inspect` workflow node writes a small JSON report
and an identity-bound reference to the original recording into the run
artifacts; neither contains signal samples. Its `report` output carries the
measured properties, and its `raw` output is a reference for later nodes. The
node does not filter, resample, create previews, or save signal data. It is
available only for EDF,
BDF, complete BrainVision, and EEGLAB recordings discovered with complete
required BIDS metadata. MNE read failures, missing companion signal files, and
missing optional EEG dependencies are returned as explicit errors. The result
reports properties from the file reader and is not a scientific quality or
validity assessment.

## Bounded signal previews

After signal inspection, **Preview signal** makes a temporary view of one raw
recording. The service calculates the recording's content identity before it
opens MNE-BIDS and verifies the identity again after closing the reader; it
returns no preview if the inputs changed. The preview response carries that
identity, selected time range, sample count, and explicit preview scope. The UI
keeps it in memory for the open view only. It does not save signal samples,
create artifacts, alter source files, filter or resample the recording, or
claim to assess signal quality.

Only EEG channels can be selected (one to eight; the initial view selects up
to four). A window is at most 20 seconds and contributes at most two million
selected channel samples. The returned time trace is a minimum-to-maximum
envelope with at most 1,000 bins per channel. The selected-window annotation
summary returns at most 500 descriptions of up to 256 characters and reports
when the visible list is truncated. Annotation events are displayed alongside
the traces and are not automatically accepted as researcher decisions.

The companion spectrum is calculated by MNE-Python's Welch method with a
Hamming window, 1,024-point FFT, segments of at most 1,024 samples, 50% overlap,
and annotation rejection disabled. It is limited to 0–100 Hz (or the Nyquist
frequency when lower), expressed as µV²/Hz, and displayed in dB re
1 µV²/Hz. This is a descriptive preview with fixed estimator settings, not a
validated analysis result. Different settings or annotation-rejection policies
can yield different spectra. The reader uses `get_data` for only the selected
EEG channels and window, then computes the spectrum through the raw object's
`compute_psd` API; MNE documents these methods in its [Raw API
reference](https://mne.tools/stable/generated/mne.io.Raw.html).

The independent synthetic reference test can be reproduced with:

```bash
uv run --locked --group eeg pytest -q tests/test_bids_eeg_preview_reference.py
```

It constructs a controlled MNE `RawArray` independently from the service
reader and compares the preview's channel envelope and Welch power values to
direct MNE calls. The test also checks event selection and source-file
immutability. This verifies numerical agreement for the declared settings; it
does not certify the scientific suitability of a user's recording or a full
experimental analysis.
