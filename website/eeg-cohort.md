# Experimental EEG cohort walkthrough

This walkthrough runs BrainLearn's experimental BIDS EEG cohort workflow on
five public recordings. It is an engineering demonstration. The methods have
not received independent scientific approval, and the report makes descriptive
summaries only. It does not make clinical or inferential claims.

## Reference data

Use [OpenNeuro ds005284, version 1.0.0](https://openneuro.org/datasets/ds005284/versions/1.0.0),
the CC0 dataset **26 By Biosemi**. The reference sample is `sub-001` through
`sub-005`, each with one continuous 64-channel, 1024 Hz recording. The pinned
events use the exact label `condition 54`, described by the dataset's event
metadata as laser stimulation. The dataset's 50 Hz power-line metadata is
recorded; the example does not apply a notch filter.

The files below are the signal-file identities in the pinned OpenNeuro 1.0.0
snapshot. Keep them beside the BIDS metadata in the local project and verify
the digest after downloading.

| Participant | File size | SHA-256 |
| --- | ---: | --- |
| `sub-001` | 65,511,936 bytes | `a3dcbdc69c5cd2b47c19cd2fcbe27ddbca0baa3deca6881b37e8e02fed970cda` |
| `sub-002` | 65,711,616 bytes | `a8f700a4b3c50420d9c357e698653deb73419f6a39ec9ddc52fafdf2c03f4b25` |
| `sub-003` | 65,911,296 bytes | `4c2fb27bc6e73d3e0aca19bdba1f85b905bd8ec48335f10bdf6d07e83d5fd47f` |
| `sub-004` | 65,112,576 bytes | `e0e5984ece35f742585106c63642dbdf96c833ddaa9a574193855cafb3d7b50c` |
| `sub-005` | 69,106,176 bytes | `0d4ab10e6a1fef3723f9749ef3c42a17964cb56ff9717c371bda22b1fc53e3d8` |

### Prepare the local BIDS folder

The OpenNeuro GitHub mirror contains the versioned BIDS metadata. The EEG
signals are stored separately, so fetch only the five BDF files for this
walkthrough. Replace `/path/to/brainlearn-project` with the project folder you
created in BrainLearn.

```sh
git clone --depth 1 --branch 1.0.0 https://github.com/OpenNeuroDatasets/ds005284.git /tmp/ds005284-v1.0.0
mkdir -p /path/to/brainlearn-project/datasets/openneuro/ds005284
cp /tmp/ds005284-v1.0.0/dataset_description.json /tmp/ds005284-v1.0.0/participants.tsv /tmp/ds005284-v1.0.0/participants.json /tmp/ds005284-v1.0.0/task-26ByBiosemi_events.json /path/to/brainlearn-project/datasets/openneuro/ds005284/
```

For each participant, copy that subject's BIDS sidecars and download the BDF
file to the matching destination. The following example shows `sub-001`; repeat
it with IDs `002` through `005` and replace the ID in both URLs and paths.

```sh
mkdir -p /path/to/brainlearn-project/datasets/openneuro/ds005284/sub-001/eeg
cp /tmp/ds005284-v1.0.0/sub-001/eeg/sub-001_task-26ByBiosemi_{channels.tsv,eeg.json,electrodes.tsv,events.tsv} /path/to/brainlearn-project/datasets/openneuro/ds005284/sub-001/eeg/
curl --fail --location --output /path/to/brainlearn-project/datasets/openneuro/ds005284/sub-001/eeg/sub-001_task-26ByBiosemi_eeg.bdf https://s3.amazonaws.com/openneuro.org/ds005284/sub-001/eeg/sub-001_task-26ByBiosemi_eeg.bdf
```

Check every signal file before proceeding. On macOS or Linux:

```sh
shasum -a 256 /path/to/brainlearn-project/datasets/openneuro/ds005284/sub-00*/eeg/*_eeg.bdf
```

On Linux, `sha256sum` can be used instead. The five results must match the
table. BIDS source files must be regular local files inside the project; do not
leave the BDF files as git-annex symlinks. Never commit this downloaded dataset
to the BrainLearn repository.

## Run through the UI

1. Install the repository's locked Python dependencies and start the local
   service and web app as described in {doc}`quickstart`.
2. Create or open the project containing `datasets/openneuro/ds005284`.
3. Load the **EEG first look** workflow. Open the **EEG cohort run** panel,
   enter `datasets/openneuro/ds005284`, and choose **Scan cohort recordings**.
4. Confirm the five reference recordings are listed as continuous with matching
   channel and sampling metadata. Select `sub-001` through `sub-005` and start
   the run.
5. For each recording, review its channel list and signal preview. Record your
   own bad-channel decision and a short reason. Then review the ICA maps,
   time courses, and spectra and record which components, if any, to exclude.
   The run cannot infer these decisions. A decision for one recording is not
   reused for another.
6. When processing finishes, open or download the HTML report and JSON summary.
   Check the selected, processed, excluded, and failed counts and each
   participant's input identity, parameters, QC decisions, outputs, and reasons.

Do not approve a recording only to get through the tutorial. If the signal or
ICA results are unclear, pause and ask an EEG researcher to review them.

## Run through the CLI

Start the local service in one terminal and keep it running. In another, set
the session token printed by the service and run the same pinned recordings.
The CLI pauses in the terminal for the same explicit QC decisions.

```sh
BRAINLEARN_SESSION_TOKEN='<local-session-token>' uv run brainlearn-cohort \
  --project /path/to/brainlearn-project \
  --workflow examples/eeg-first-look.workflow.json \
  --dataset-root datasets/openneuro/ds005284 \
  --dataset-id ds005284 \
  --dataset-version 1.0.0 \
  --seed 0 \
  --recording sub-001/eeg/sub-001_task-26ByBiosemi_eeg.bdf \
  --recording sub-002/eeg/sub-002_task-26ByBiosemi_eeg.bdf \
  --recording sub-003/eeg/sub-003_task-26ByBiosemi_eeg.bdf \
  --recording sub-004/eeg/sub-004_task-26ByBiosemi_eeg.bdf \
  --recording sub-005/eeg/sub-005_task-26ByBiosemi_eeg.bdf
```

The service accepts cohort work only on the local loopback API. The generated
report and summary are stored in the project's `cohorts/` directory.

## Explicit provisional settings

The example uses the following settings. They are explicit so the run record
can describe them; they are not endorsed defaults.

| Stage | Setting |
| --- | --- |
| Filter | MNE FIR band-pass, 1–40 Hz; no notch filter |
| ICA fit | FastICA, all selected EEG channels, 1,000 iteration limit, no annotation rejection, decimation 1; provisional and explicit in the node parameters |
| Event | Exact BIDS annotation `condition 54` |
| Epoch | −0.2 s to +0.8 s relative to event |
| Baseline | Apply the −0.2 s to 0 s baseline |
| Epoch rejection | Peak-to-peak threshold is 0 µV, which disables amplitude rejection; reject epochs overlapping `BAD` annotations |
| ERP | Per-recording average and sample SD across retained epochs |
| PSD | Welch, Hamming window, 256-point FFT and segment, 128-sample overlap, 1–40 Hz; compute from the same retained epochs as ERP |
| Cohort summary | Participant-level means and standard deviations; no hypothesis tests |

The implementation does not silently interpolate channels, resample, or approve
QC. A recording with incompatible channel or sampling metadata is excluded with
a reason. A failed recording does not stop the remaining cohort; a cohort with
no successful recordings fails.

## Reference parity check

After the five signal files are in the local BIDS folder, set
`BRAINLEARN_DS005284_ROOT` to that dataset root and run:

```sh
BRAINLEARN_DS005284_ROOT=/path/to/brainlearn-project/datasets/openneuro/ds005284 \
  uv run --frozen pytest -q tests/test_pinned_ds005284_reference.py
```

This check compares event selection, epochs, ERP summaries, and PSD summaries
for each pinned participant with separately written direct MNE-Python calls.
The test asserts the pinned signal checksums before and after analysis. Its
predeclared tolerances are `1e-12 V` for epoch samples and `rtol=1e-7`,
`atol=1e-12` for ERP/PSD summaries. This establishes numerical agreement for
these explicit operations in the tested environment; it does not approve the
scientific choices or the per-recording QC decisions.

## Output limits

The HTML report is generated from persisted run records. It contains the actual
parameters, exclusions and failures, denominators, retained event/epoch counts,
methods text with human-review placeholders, executed-method citations, and
checksummed output references. Review and edit the methods text before using it
in a manuscript. The workflow is experimental and has not passed the
independent-methods, product, or cross-platform certification gates.
