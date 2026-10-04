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
summary is read. This action does not filter, resample, create previews, save
signal data, or persist an inspection record. It is available only for EDF,
BDF, complete BrainVision, and EEGLAB recordings discovered with complete
required BIDS metadata. MNE read failures, missing companion signal files, and
missing optional EEG dependencies are returned as explicit errors. The result
reports properties from the file reader and is not a scientific quality or
validity assessment.
