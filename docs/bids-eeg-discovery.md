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
Issue messages identify the affected path and the field, companion file, or
format to correct. Unsupported-format messages list the accepted EEG formats;
incomplete-metadata messages identify the required sidecar or dataset
description fields and tell the researcher to scan again after correction.
Discovery actions for identity creation, signal inspection, and previews remain
unavailable unless both the dataset and selected recording are ready. The
identity, inspection, preview, and workflow input paths repeat the readiness
check, so a stale UI state or a workflow submitted directly to the API cannot
pass an unsupported or incomplete recording to MNE or downstream execution.

When matching `channels.tsv` and `events.tsv` files exist, the scan summarizes
channel names/count and event count/types. Channel tables are checked for
`name`, `type`, and `units`; event timing columns and values are checked, with
`n/a` accepted for unknown onset or duration as specified by [BIDS events](https://bids-specification.readthedocs.io/en/stable/modality-agnostic-files/events.html).
For event parity without returning a potentially large row array, discovery
also returns `events_timing_sha256`: SHA-256 of sorted event descriptions,
onsets, and durations, with numeric times rounded to 1 ns and `n/a` encoded as
unknown. This identifies event timing metadata; it does not read signal data.
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

## Researcher annotations and decisions

After creating an input identity, a researcher can explicitly save an
annotation and choose `accepted`, `rejected`, or `needs review`. A submission
requires a researcher name and records the dataset and recording-relative
paths, source content identity, note, optional time interval and channel names,
and creation/update timestamps. The UI does not preselect a decision or infer
one from signal content. Submissions are append-only; a new action creates a
new provenance record.

Records are stored atomically in the open project at
`annotations/eeg-researcher-decisions.json`, in a versioned schema separate
from raw BIDS files. The authenticated service checks project authorization
and recomputes the recording's content identity before listing or saving. If
the recording or its identity-defining sidecars change, the old decision is
not displayed for the new identity and a stale save is rejected. These are
researcher annotations and explicit inspection decisions; they do not certify
signal quality, modify the source, or constitute a clinical judgment.

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

## Pinned direct-MNE metadata reference

`scripts/reference_bids_eeg_mne.py` independently selects the pinned
`ds002181:1.0.0` recording with `mne_bids.find_matching_paths` and reads it
through `mne_bids.read_raw_bids`. It emits the direct reader's recording path,
channel labels/types, sampling frequency, sample count/duration, bad-channel
count, annotations, and high/low-pass metadata, along with Python/MNE/MNE-BIDS,
NumPy, and SciPy versions and the exact fixture file hashes. It does not import
BrainLearn discovery or inspection code. The JSON baseline is
[`ds002181-sub-1473-mne-reference-1.0.0.json`](fixtures/ds002181-sub-1473-mne-reference-1.0.0.json).

The pinned five-file subset's EEGLAB `.set` header references an external
`.fdt` that is not included in the subset. The script copies only the verified
manifest files into a temporary directory and creates an empty companion there
so MNE can read header and BIDS event metadata. It uses `preload=False` and
never reads or compares signal samples. The source fixture is hash-checked
before and after. This is an independent metadata and event-structure check,
not a signal-value or analysis validation.

The parity test uses exact equality for the selected relative recording path,
channel labels, channel-type counts, sampling frequency, sample count, duration,
bad-channel count, annotation count/descriptions, and MNE high/low-pass
metadata. It compares the discovery event count and event-type set with the
annotations MNE-BIDS loads from the pinned `events.tsv`. It also compares
`events_timing_sha256`, which covers every annotation onset and duration plus
the event label, at 1 ns precision. The EEG-channel count is compared with
MNE's `eeg` channel count. These checks intentionally do not compare
amplitudes, filtering, or other signal-derived values.

Run the actual pinned-fixture parity check with:

```bash
uv sync --locked --all-packages --group eeg
uv run python scripts/fetch_bids_eeg_fixture.py /tmp/brainlearn-ds002181-fixture
BRAINLEARN_BIDS_EEG_FIXTURE=/tmp/brainlearn-ds002181-fixture \
  uv run --locked --group eeg pytest -q tests/test_bids_eeg_mne_reference.py
```

The optional test compares the independent JSON reference against BrainLearn's
metadata discovery and MNE inspection values, verifies the pinned versions and
fixture identity, and confirms the source files remain unchanged. The ordinary
unit suite skips this test when no fixture path is configured; the scheduled
dataset-smoke workflow retrieves the small verified subset and runs it.
