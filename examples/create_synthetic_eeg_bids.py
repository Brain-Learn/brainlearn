#!/usr/bin/env python3
"""Create a tiny, deterministic BrainVision BIDS EEG dataset for engineering demos."""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
from pathlib import Path


def create_dataset(destination: Path) -> None:
    """Write a 10-second, two-channel synthetic recording without external dependencies."""

    root = destination.resolve()
    eeg_dir = root / "sub-01" / "eeg"
    eeg_dir.mkdir(parents=True, exist_ok=True)
    name = "sub-01_task-Demo_eeg"
    sampling_frequency = 250
    duration_seconds = 10
    samples = sampling_frequency * duration_seconds
    resolution_uv = 0.1
    rng = random.Random(20261012)
    channel_values: list[list[int]] = [[], []]
    for sample in range(samples):
        time_seconds = sample / sampling_frequency
        values = (
            20.0 * math.sin(2 * math.pi * 10 * time_seconds)
            + 5.0 * math.sin(2 * math.pi * 50 * time_seconds)
            + rng.gauss(0.0, 0.2),
            15.0 * math.sin(2 * math.pi * 12 * time_seconds + 0.2)
            + 7.0 * math.sin(2 * math.pi * 50 * time_seconds)
            + rng.gauss(0.0, 0.2),
        )
        for channel, value in enumerate(values):
            channel_values[channel].append(round(value / resolution_uv))

    # BrainVision's MULTIPLEXED INT_16 format stores one signed sample per channel per time point.
    signal = bytearray()
    for sample in range(samples):
        for channel in range(2):
            signal.extend(struct.pack("<h", channel_values[channel][sample]))
    (eeg_dir / f"{name}.eeg").write_bytes(signal)
    (eeg_dir / f"{name}.vhdr").write_text(
        "\n".join(
            [
                "Brain Vision Data Exchange Header File Version 1.0",
                "",
                "[Common Infos]",
                "Codepage=UTF-8",
                f"DataFile={name}.eeg",
                f"MarkerFile={name}.vmrk",
                "DataFormat=BINARY",
                "DataOrientation=MULTIPLEXED",
                "NumberOfChannels=2",
                f"SamplingInterval={1_000_000 / sampling_frequency:.1f}",
                "",
                "[Binary Infos]",
                "BinaryFormat=INT_16",
                "",
                "[Channel Infos]",
                f"Ch1=Cz,,{resolution_uv},µV",
                f"Ch2=Pz,,{resolution_uv},µV",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (eeg_dir / f"{name}.vmrk").write_text(
        "\n".join(
            [
                "Brain Vision Data Exchange Marker File, Version 1.0",
                "",
                "[Common Infos]",
                "Codepage=UTF-8",
                f"DataFile={name}.eeg",
                "",
                "[Marker Infos]",
                "Mk1=New Segment,,1,1,0,20261012120000000000",
                f"Mk2=Stimulus,S 1,{5 * sampling_frequency + 1},1,0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "dataset_description.json").write_text(
        json.dumps(
            {
                "Name": "BrainLearn Synthetic EEG Demo",
                "BIDSVersion": "1.8.0",
                "DatasetType": "raw",
                "License": "CC0-1.0",
                "GeneratedBy": [{"Name": "BrainLearn synthetic EEG fixture", "Version": "1.0.0"}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "participants.tsv").write_text(
        "participant_id\nsub-01\n",
        encoding="utf-8",
    )
    (eeg_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "TaskName": "Demo",
                "SamplingFrequency": sampling_frequency,
                "PowerLineFrequency": 50,
                "EEGReference": "linked mastoids (synthetic reference)",
                "SoftwareFilters": "n/a",
                "EEGChannelCount": 2,
                "RecordingType": "continuous",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (eeg_dir / "sub-01_task-Demo_channels.tsv").write_text(
        "name\ttype\tunits\treference\n"
        "Cz\tEEG\tµV\tlinked mastoids (synthetic reference)\n"
        "Pz\tEEG\tµV\tlinked mastoids (synthetic reference)\n",
        encoding="utf-8",
    )
    (eeg_dir / "sub-01_task-Demo_events.tsv").write_text(
        "onset\tduration\ttrial_type\n5.0\t0\tstimulus\n",
        encoding="utf-8",
    )
    (eeg_dir / "sub-01_task-Demo_events.json").write_text(
        json.dumps({"trial_type": {"stimulus": {"Description": "Synthetic event"}}}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "destination", type=Path, help="Directory to create as the BIDS dataset root"
    )
    args = parser.parse_args()
    create_dataset(args.destination)
    print(f"Synthetic BIDS EEG dataset created at {args.destination.resolve()}")


if __name__ == "__main__":
    main()
