import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ICAComponentDiagnostics } from "./ICAComponentDiagnostics";
import { parseEEGICAComponentDiagnostics } from "./icaDiagnostics";

const data = parseEEGICAComponentDiagnostics({
  schema_version: "1.0",
  sampling_frequency_hz: 250,
  channels: ["Cz", "Pz"],
  component_count: 2,
  component_maps: [
    { component: 0, channel_weights: [0.5, -0.5] },
    { component: 1, channel_weights: [-0.5, 0.5] },
  ],
  channel_locations_m: {},
  topography_available: false,
  time_course_unit: "arbitrary ICA source units",
  time_courses: [
    { component: 0, points: [{ time_seconds: 0, minimum: -1, maximum: 1 }] },
    { component: 1, points: [{ time_seconds: 0, minimum: -2, maximum: 2 }] },
  ],
  spectrum_unit: "arbitrary units squared per Hz",
  spectrum_method: "MNE Welch",
  spectrum_n_fft: 512,
  spectrum_n_per_seg: 512,
  spectrum_n_overlap: 256,
  frequencies_hz: [0, 1],
  spectra: [
    { component: 0, power: [0.1, 0.2] },
    { component: 1, power: [0.2, 0.3] },
  ],
  preview_sample_count: 1000,
  preview_duration_seconds: 4,
  note: "Synthetic fixture diagnostics.",
});

test("shows component maps, source courses, spectra, and honest montage state", () => {
  render(<ICAComponentDiagnostics diagnostics={data} onClose={vi.fn()} />);
  expect(
    screen.getByRole("heading", { name: "ICA component review" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/no sensor locations/)).toBeInTheDocument();
  expect(screen.getByText(/Channel weights/)).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: /time course envelope/ }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("ICA component"), {
    target: { value: "1" },
  });
  expect(screen.getByText("Pz")).toBeInTheDocument();
});
