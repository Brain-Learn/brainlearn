import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { EEGFilterComparisonPreview } from "./EEGFilterComparisonPreview";
import { parseEEGFilterPreview } from "./filterPreview";

const preview = {
  schema_version: "1.0",
  source_content_identity: `brainlearn-v1:artifact:${"a".repeat(64)}`,
  recording_path: "sub-01/eeg/sub-01_task-Demo_eeg.vhdr",
  sampling_frequency_hz: 250,
  channels: ["Cz"],
  unit: "µV",
  spectrum_unit: "µV²/Hz",
  before: {
    traces: [
      {
        channel_name: "Cz",
        bins: [
          { time_seconds: 0, minimum_uv: -2, maximum_uv: 3 },
          { time_seconds: 1, minimum_uv: -1, maximum_uv: 2 },
        ],
      },
    ],
    frequencies_hz: [0, 1],
    spectrum_traces: [{ channel_name: "Cz", power_uv2_per_hz: [1, 2] }],
    preview_sample_count: 500,
    preview_duration_seconds: 2,
    spectrum_method: "MNE Welch",
    spectrum_n_fft: 1024,
    spectrum_n_per_seg: 500,
    spectrum_n_overlap: 250,
  },
  after: {
    traces: [
      {
        channel_name: "Cz",
        bins: [
          { time_seconds: 0, minimum_uv: -1, maximum_uv: 2 },
          { time_seconds: 1, minimum_uv: -1, maximum_uv: 1 },
        ],
      },
    ],
    frequencies_hz: [0, 1],
    spectrum_traces: [{ channel_name: "Cz", power_uv2_per_hz: [0.5, 1] }],
    preview_sample_count: 500,
    preview_duration_seconds: 2,
    spectrum_method: "MNE Welch",
    spectrum_n_fft: 1024,
    spectrum_n_per_seg: 500,
    spectrum_n_overlap: 250,
  },
  note: "Bounded preview; not a signal-quality assessment.",
};

afterEach(() => cleanup());

test("parses and displays paired signal and spectrum previews with labels", () => {
  const parsed = parseEEGFilterPreview(preview);
  render(<EEGFilterComparisonPreview preview={parsed} onClose={vi.fn()} />);

  expect(
    screen.getByRole("heading", { name: "Filter comparison" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Cz" })).toBeInTheDocument();
  expect(screen.getByText("Before")).toBeInTheDocument();
  expect(screen.getByText("After")).toBeInTheDocument();
  expect(
    screen.getByText(/not a signal-quality assessment/),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("img")).toHaveLength(2);
});

test("rejects a malformed preview artifact", () => {
  expect(() => parseEEGFilterPreview({ ...preview, after: {} })).toThrow(
    "This artifact is not a supported EEG filter comparison preview.",
  );
});
