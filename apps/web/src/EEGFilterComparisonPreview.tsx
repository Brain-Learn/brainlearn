import type {
  EEGFilterComparisonPreview as Preview,
  EEGFilterPreviewSide,
  EEGFilterSpectrumTrace,
  EEGFilterTrace,
} from "./types";

function matchingTrace(
  side: EEGFilterPreviewSide,
  name: string,
): EEGFilterTrace | undefined {
  return side.traces.find((trace) => trace.channel_name === name);
}

function matchingSpectrum(
  side: EEGFilterPreviewSide,
  name: string,
): EEGFilterSpectrumTrace | undefined {
  return side.spectrum_traces.find((trace) => trace.channel_name === name);
}

function pairedTraceSvg(
  before: EEGFilterTrace,
  after: EEGFilterTrace,
  label: string,
) {
  const bins = [...before.bins, ...after.bins];
  const minimum = Math.min(...bins.map((bin) => bin.minimum_uv));
  const maximum = Math.max(...bins.map((bin) => bin.maximum_uv));
  const span = maximum - minimum || 1;
  const y = (value: number) => 88 - ((value - minimum) / span) * 72;
  const draw = (trace: EEGFilterTrace, color: string) =>
    trace.bins.map((bin, index) => {
      const x = 20 + (index / Math.max(1, trace.bins.length - 1)) * 420;
      return (
        <line
          key={`${label}-${color}-${index}`}
          stroke={color}
          strokeWidth="1.5"
          x1={x}
          x2={x}
          y1={y(bin.minimum_uv)}
          y2={y(bin.maximum_uv)}
        />
      );
    });
  return (
    <svg
      aria-label={`${label} signal envelope, in microvolts`}
      className="filter-chart"
      role="img"
      viewBox="0 0 460 110"
    >
      <line className="filter-chart-axis" x1="20" x2="440" y1="94" y2="94" />
      <line className="filter-chart-axis" x1="20" x2="20" y1="12" y2="94" />
      {draw(before, "#7bb9ff")}
      {draw(after, "#43d7bb")}
    </svg>
  );
}

function pairedSpectrumSvg(
  preview: Preview,
  channelName: string,
  before: EEGFilterSpectrumTrace,
  after: EEGFilterSpectrumTrace,
) {
  const frequencies = preview.before.frequencies_hz;
  const dbValues = [...before.power_uv2_per_hz, ...after.power_uv2_per_hz].map(
    (value) => 10 * Math.log10(Math.max(value, 1e-20)),
  );
  const minimum = Math.min(...dbValues);
  const maximum = Math.max(...dbValues);
  const span = maximum - minimum || 1;
  const maxFrequency = frequencies[frequencies.length - 1] || 1;
  const points = (trace: EEGFilterSpectrumTrace) =>
    trace.power_uv2_per_hz
      .map((power, index) => {
        const x = 20 + (frequencies[index] / maxFrequency) * 420;
        const db = 10 * Math.log10(Math.max(power, 1e-20));
        const y = 88 - ((db - minimum) / span) * 72;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  return (
    <svg
      aria-label={`${channelName} before and after power spectrum, in decibels relative to one microvolt squared per hertz`}
      className="filter-chart"
      role="img"
      viewBox="0 0 460 110"
    >
      <line className="filter-chart-axis" x1="20" x2="440" y1="94" y2="94" />
      <line className="filter-chart-axis" x1="20" x2="20" y1="12" y2="94" />
      <polyline className="filter-spectrum-before" points={points(before)} />
      <polyline className="filter-spectrum-after" points={points(after)} />
    </svg>
  );
}

export function EEGFilterComparisonPreview({
  preview,
  onClose,
}: {
  preview: Preview;
  onClose: () => void;
}) {
  return (
    <section
      className="filter-comparison"
      aria-labelledby="filter-comparison-title"
    >
      <div className="filter-comparison-heading">
        <div>
          <h3 id="filter-comparison-title">Filter comparison</h3>
          <p>
            {preview.recording_path} ·{" "}
            {preview.sampling_frequency_hz.toLocaleString()} Hz · up to{" "}
            {preview.channels.length} EEG channels
          </p>
        </div>
        <button aria-label="Close filter comparison" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="filter-comparison-note">{preview.note}</p>
      <div className="filter-comparison-legend" aria-label="Chart legend">
        <span>
          <i className="filter-legend-before" /> Before
        </span>
        <span>
          <i className="filter-legend-after" /> After
        </span>
        <span>Trace envelope: µV</span>
        <span>Spectrum: dB (µV²/Hz)</span>
      </div>
      {preview.channels.map((channel) => {
        const beforeTrace = matchingTrace(preview.before, channel);
        const afterTrace = matchingTrace(preview.after, channel);
        const beforeSpectrum = matchingSpectrum(preview.before, channel);
        const afterSpectrum = matchingSpectrum(preview.after, channel);
        if (!beforeTrace || !afterTrace || !beforeSpectrum || !afterSpectrum)
          return null;
        return (
          <article className="filter-channel-comparison" key={channel}>
            <h4>{channel}</h4>
            <div className="filter-comparison-columns">
              <figure>
                <figcaption>
                  Signal envelope · 0–
                  {preview.before.preview_duration_seconds.toFixed(1)} s
                </figcaption>
                {pairedTraceSvg(
                  beforeTrace,
                  afterTrace,
                  `${channel} before and after`,
                )}
              </figure>
              <figure>
                <figcaption>
                  Power spectrum · 0–
                  {preview.before.frequencies_hz[
                    preview.before.frequencies_hz.length - 1
                  ].toFixed(0)}{" "}
                  Hz · {preview.before.spectrum_method}
                </figcaption>
                {pairedSpectrumSvg(
                  preview,
                  channel,
                  beforeSpectrum,
                  afterSpectrum,
                )}
              </figure>
            </div>
          </article>
        );
      })}
      <p className="filter-preview-identity">
        Source identity: <code>{preview.source_content_identity}</code>
      </p>
    </section>
  );
}
