import type { EEGFilterComparisonPreview, EEGFilterPreviewSide } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isPreviewSide(value: unknown): value is EEGFilterPreviewSide {
  if (!isRecord(value)) return false;
  const frequencies = value.frequencies_hz;
  if (
    !Array.isArray(frequencies) ||
    frequencies.length < 2 ||
    !frequencies.every(isFiniteNumber) ||
    !Array.isArray(value.traces) ||
    !Array.isArray(value.spectrum_traces) ||
    !isFiniteNumber(value.preview_sample_count) ||
    !isFiniteNumber(value.preview_duration_seconds) ||
    typeof value.spectrum_method !== "string" ||
    !isFiniteNumber(value.spectrum_n_fft) ||
    !isFiniteNumber(value.spectrum_n_per_seg) ||
    !isFiniteNumber(value.spectrum_n_overlap)
  ) {
    return false;
  }
  const validTraces = value.traces.every(
    (trace) =>
      isRecord(trace) &&
      typeof trace.channel_name === "string" &&
      Array.isArray(trace.bins) &&
      trace.bins.length > 0 &&
      trace.bins.every(
        (bin) =>
          isRecord(bin) &&
          isFiniteNumber(bin.time_seconds) &&
          isFiniteNumber(bin.minimum_uv) &&
          isFiniteNumber(bin.maximum_uv),
      ),
  );
  const validSpectra = value.spectrum_traces.every(
    (trace) =>
      isRecord(trace) &&
      typeof trace.channel_name === "string" &&
      Array.isArray(trace.power_uv2_per_hz) &&
      trace.power_uv2_per_hz.length === frequencies.length &&
      trace.power_uv2_per_hz.every(isFiniteNumber),
  );
  return validTraces && validSpectra;
}

export function parseEEGFilterPreview(
  value: unknown,
): EEGFilterComparisonPreview {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    typeof value.source_content_identity !== "string" ||
    typeof value.recording_path !== "string" ||
    !isFiniteNumber(value.sampling_frequency_hz) ||
    !Array.isArray(value.channels) ||
    !value.channels.every((channel) => typeof channel === "string") ||
    value.unit !== "µV" ||
    value.spectrum_unit !== "µV²/Hz" ||
    !isPreviewSide(value.before) ||
    !isPreviewSide(value.after) ||
    typeof value.note !== "string"
  ) {
    throw new Error(
      "This artifact is not a supported EEG filter comparison preview.",
    );
  }
  return value as unknown as EEGFilterComparisonPreview;
}
