import type { EEGICAComponentDiagnostics } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumbers(value: unknown, maxLength: number): value is number[] {
  return (
    Array.isArray(value) &&
    value.length <= maxLength &&
    value.every((item) => typeof item === "number" && Number.isFinite(item))
  );
}

export function parseEEGICAComponentDiagnostics(
  value: unknown,
): EEGICAComponentDiagnostics {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    !Number.isFinite(value.sampling_frequency_hz) ||
    !Array.isArray(value.channels) ||
    value.channels.length < 2 ||
    value.channels.length > 256 ||
    !value.channels.every(
      (item) => typeof item === "string" && item.length > 0,
    ) ||
    new Set(value.channels).size !== value.channels.length ||
    !Number.isInteger(value.component_count) ||
    Number(value.component_count) < 1 ||
    Number(value.component_count) > 256 ||
    !Array.isArray(value.component_maps) ||
    value.component_maps.length !== value.component_count ||
    !Array.isArray(value.time_courses) ||
    value.time_courses.length !== value.component_count ||
    !Array.isArray(value.frequencies_hz) ||
    !finiteNumbers(value.frequencies_hz, 257) ||
    !Array.isArray(value.spectra) ||
    value.spectra.length !== value.component_count ||
    !isRecord(value.channel_locations_m) ||
    typeof value.topography_available !== "boolean" ||
    typeof value.time_course_unit !== "string" ||
    typeof value.spectrum_unit !== "string" ||
    typeof value.spectrum_method !== "string" ||
    !Number.isInteger(value.spectrum_n_fft) ||
    !Number.isInteger(value.spectrum_n_per_seg) ||
    !Number.isInteger(value.spectrum_n_overlap) ||
    !Number.isInteger(value.preview_sample_count) ||
    !Number.isFinite(value.preview_duration_seconds) ||
    typeof value.note !== "string"
  ) {
    throw new Error(
      "ICA component diagnostics are invalid or outside supported limits.",
    );
  }
  const componentCount = Number(value.component_count);
  for (let index = 0; index < componentCount; index += 1) {
    const map = value.component_maps[index];
    const course = value.time_courses[index];
    const spectrum = value.spectra[index];
    if (
      !isRecord(map) ||
      map.component !== index ||
      !finiteNumbers(map.channel_weights, 256) ||
      map.channel_weights.length !== value.channels.length ||
      !isRecord(course) ||
      course.component !== index ||
      !Array.isArray(course.points) ||
      course.points.length < 1 ||
      course.points.length > 160 ||
      !course.points.every(
        (point) =>
          isRecord(point) &&
          Number.isFinite(point.time_seconds) &&
          Number.isFinite(point.minimum) &&
          Number.isFinite(point.maximum) &&
          Number(point.minimum) <= Number(point.maximum),
      ) ||
      !isRecord(spectrum) ||
      spectrum.component !== index ||
      !finiteNumbers(spectrum.power, 257) ||
      spectrum.power.length !== value.frequencies_hz.length
    ) {
      throw new Error(
        "ICA component diagnostics contain an invalid component series.",
      );
    }
  }
  for (const [name, coordinates] of Object.entries(value.channel_locations_m)) {
    if (
      !value.channels.includes(name) ||
      !Array.isArray(coordinates) ||
      coordinates.length !== 3 ||
      !finiteNumbers(coordinates, 3)
    ) {
      throw new Error("ICA channel locations are invalid.");
    }
  }
  return value as unknown as EEGICAComponentDiagnostics;
}
