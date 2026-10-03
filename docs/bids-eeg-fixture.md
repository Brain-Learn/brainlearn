# Pinned BIDS EEG fixture

BrainLearn's compact retrieval fixture is one raw EEG recording and its BIDS
sidecars from OpenNeuro snapshot [`ds002181:1.0.0`](https://openneuro.org/datasets/ds002181/versions/1.0.0),
“CRYPTO and PROVIDE EEG Baseline Data.” The five files total 697,051 bytes.
The complete snapshot is 158,221,569 bytes (1,134 files), so retrieval is
limited to the listed members. No dataset bytes are committed to Git.

The immutable snapshot tree SHA-1 is
`2ec6d5319a5ccd9b4fb45bebb07a30fe743b1810`; its OpenNeuro snapshot tag is
`1.0.0`, created 2019-09-27. File URLs and SHA-256 digests are recorded in
[`docs/fixtures/ds002181-sub-1473-1.0.0.json`](fixtures/ds002181-sub-1473-1.0.0.json).
The metadata object URLs contain their content object IDs, and the annexed EEG
file URL pins its S3 `versionId`. The retrieval helper checks the final HTTPS
host before following redirects, rejects symlinked output paths, checks the
exact byte count and SHA-256, and installs each file atomically without
replacing existing files. A previously retrieved file is accepted only if it
already matches its recorded size and digest.

## License and citation

The exact snapshot's `dataset_description.json` declares `License: CC0`; the
OpenNeuro GraphQL snapshot metadata reports the same value. OpenNeuro's upload
policy requires public datasets to use CC0 at publication or after a 36-month
grace period from the first snapshot, and this snapshot dates to 2019. The
policy and snapshot metadata are linked in the manifest. This license review
is specific to `ds002181:1.0.0`; it does not infer licensing from public
availability alone.

The dataset metadata contains `DatasetDOI: mockDOI`, which is not a usable
identifier. Do not cite it as a DOI. Cite the snapshot by title, accession,
version, and landing page, and cite the associated article:

> Xie, W., Jensen, S. K. G., Wade, M., Kumar, S., Westerlund, A., Kakon, S. H.,
> Haque, R., Petri, W. A., & Nelson, C. A. (2019). Growth faltering is associated
> with altered brain functional connectivity and cognitive outcomes in urban
> Bangladeshi children exposed to early adversity. *BMC Medicine, 17*, 199.
> <https://doi.org/10.1186/s12916-019-1431-5>

## Retrieve and verify

Run from the repository root with the project-managed Python environment. The
destination should be outside the repository so data cannot be committed
accidentally:

```bash
uv run python scripts/fetch_bids_eeg_fixture.py /tmp/brainlearn-ds002181-fixture
```

The helper reads the checked-in manifest, downloads only its five listed files,
and exits with an error if the source, size, or digest differs. It also rejects
redirects to hosts outside the allowlist and preserves files that are already
present at the destination. The helper uses only the Python standard library
and requires no OpenNeuro account or credential.

| BIDS path | Bytes | SHA-256 |
| --- | ---: | --- |
| `dataset_description.json` | 379 | `02120902abf903e6eb3ffd001b2bba8720023a1e21946d38b3ab4ddbd491daad` |
| `sub-1473/eeg/sub-1473_task-Baseline_channels.tsv` | 1,934 | `6cc38eb82a7d632a94b077d26a7e31904f44e6152d386ce2d40b6f1422d26cab` |
| `sub-1473/eeg/sub-1473_task-Baseline_eeg.json` | 613 | `2abb9356b1f752b4f947a4bf90f3d18643527d7e0d81c4b2ccb633ef57bc5df2` |
| `sub-1473/eeg/sub-1473_task-Baseline_eeg.set` | 693,168 | `123900cfe3b81528f63f24292dc1d1207d05ad17d9b44324e16fed7d0fe31fe2` |
| `sub-1473/eeg/sub-1473_task-Baseline_events.tsv` | 957 | `86cbb325e4b070ece0a9ceed09dcc049eb59167d2327f2a07eeb38a4ee561634` |

## Scope and limitations

The selected recording is unprocessed source data. Its sidecar reports 124 EEG
channels, one miscellaneous channel, and a 500 Hz sampling frequency. These
are source metadata values, not an independent signal-inspection result. This
step does not validate BIDS conformance, interpret the signal, or add MNE.

The study includes infant and young-child recordings. This one-recording
fixture is for technical research-software development only; it does not
support cohort-level analysis or any diagnosis, treatment, participant
scoring, or clinical claim. The full-cohort `participants.tsv` and all other
recordings are intentionally omitted.
