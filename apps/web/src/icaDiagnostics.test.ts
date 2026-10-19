import { expect, test } from "vitest";

import { parseEEGICAComponentDiagnostics } from "./icaDiagnostics";

function diagnostics() {
  return {
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
      {
        component: 0,
        points: [{ time_seconds: 0, minimum: -1, maximum: 1 }],
      },
      {
        component: 1,
        points: [{ time_seconds: 0, minimum: -2, maximum: 2 }],
      },
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
  };
}

test("accepts bounded ICA diagnostics for the component viewer", () => {
  const parsed = parseEEGICAComponentDiagnostics(diagnostics());
  expect(parsed.component_count).toBe(2);
  expect(parsed.channels).toEqual(["Cz", "Pz"]);
});

test("rejects malformed or oversized ICA component series", () => {
  expect(() =>
    parseEEGICAComponentDiagnostics({
      ...diagnostics(),
      component_maps: [
        { component: 0, channel_weights: [0.5, -0.5] },
        { component: 2, channel_weights: [-0.5, 0.5] },
      ],
    }),
  ).toThrow("invalid component series");
});
