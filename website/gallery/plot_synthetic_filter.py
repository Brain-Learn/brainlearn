"""Filter the deterministic BrainLearn synthetic BIDS EEG recording.

This example is an engineering demonstration. Its signal is synthetic, and
the chosen band is not a recommendation for research data.
"""

# %%
# A generated BIDS input
# ======================
#
# This tiny recording is created by the repository's deterministic generator.
# It contains no human or participant data. Its checksums and CC0-1.0 license
# are recorded in ``examples/synthetic-eeg-bids-manifest.json``.

from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib.pyplot as plt
from mne_bids import BIDSPath, read_raw_bids

from examples.create_synthetic_eeg_bids import create_dataset

with TemporaryDirectory(prefix="brainlearn-gallery-") as temporary_directory:
    dataset_root = Path(temporary_directory) / "example-bids"
    create_dataset(dataset_root)
    bids_path = BIDSPath(
        subject="01",
        task="Demo",
        datatype="eeg",
        root=dataset_root,
        extension=".vhdr",
    )
    raw = read_raw_bids(bids_path, verbose="ERROR")
    raw.load_data()

    before = raw.copy().pick("eeg")
    after = before.copy().filter(
        l_freq=1.0,
        h_freq=40.0,
        method="fir",
        phase="zero",
        fir_window="hamming",
        fir_design="firwin",
        verbose="ERROR",
    )

    times = before.times
    figure, axes = plt.subplots(2, 1, figsize=(9, 5), sharex=True, layout="constrained")
    for channel_index, channel_name in enumerate(before.ch_names):
        axes[0].plot(times, before.get_data(picks=[channel_index])[0] * 1e6, label=channel_name)
        axes[1].plot(times, after.get_data(picks=[channel_index])[0] * 1e6, label=channel_name)
    axes[0].set_title("Synthetic signal before filtering")
    axes[1].set_title("After a 1–40 Hz MNE FIR band-pass")
    axes[1].set_xlabel("Time (s)")
    for axis in axes:
        axis.set_ylabel("Amplitude (µV)")
        axis.legend(loc="upper right")
        axis.grid(alpha=0.2)
    plt.show()

# %%
# Expected output
# ===============
#
# The 50 Hz component is attenuated while the synthetic 10 Hz and 12 Hz
# components remain visible. This plotted example does not measure suitability
# for any research question or constitute scientific validation.
