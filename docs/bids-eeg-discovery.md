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
`PowerLineFrequency`, and `SoftwareFilters`. The task name must agree with the
recording's `task-` entity. Unsupported BIDS major versions, derivative
datasets, unsupported recording formats, malformed metadata, and missing
required fields are reported as explicit unsupported or incomplete states.

When matching `channels.tsv` and `events.tsv` files exist, the scan summarizes
channel names/count and event count/types. Channel tables are checked for
`name`, `type`, and `units`; event timing columns and values are checked. These
optional summaries do not make the scan a complete BIDS validator.

Only bounded JSON and TSV metadata files are opened. EEG signal bytes are not
read, no input content identities are computed, and source files are not
modified. Discovery reports whether the required metadata is present; it does
not validate signal content or certify scientific compatibility.
