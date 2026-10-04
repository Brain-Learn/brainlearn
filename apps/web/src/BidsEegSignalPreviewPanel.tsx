import { useEffect, useState } from "react";

import type { BidsEegSignalPreview } from "./types";

interface Props {
  preview: BidsEegSignalPreview;
  pending: boolean;
  onUpdate: (options: {
    timeStartSeconds: number;
    durationSeconds: number;
    channelNames: string[];
  }) => void;
}

/** Bounded display only: the preview cannot modify or persist its source. */
export function BidsEegSignalPreviewPanel({
  preview,
  pending,
  onUpdate,
}: Props) {
  const [start, setStart] = useState(String(preview.time_start_seconds));
  const [duration, setDuration] = useState(String(preview.duration_seconds));
  const [selected, setSelected] = useState(
    preview.traces.map((x) => x.channel_name),
  );
  useEffect(() => {
    setStart(String(preview.time_start_seconds));
    setDuration(String(preview.duration_seconds));
    setSelected(preview.traces.map((trace) => trace.channel_name));
  }, [preview]);
  const eegChannels = preview.channels.filter(
    (channel) => channel.channel_type === "eeg",
  );
  const powersInDb = preview.spectrum.traces.map((trace) =>
    trace.power_uv2_per_hz.map(
      (power) => 10 * Math.log10(Math.max(power, 1e-20)),
    ),
  );
  const spectrumValues = powersInDb.flat();
  const spectrumMin = Math.min(...spectrumValues);
  const spectrumMax = Math.max(...spectrumValues);
  const spectrumSpan = spectrumMax - spectrumMin || 1;
  const spectrumWidth = 620;
  const spectrumHeight = 150;
  const spectrumPaths = powersInDb.map((values) =>
    values
      .map((value, index) => {
        const x = (index / Math.max(1, values.length - 1)) * spectrumWidth;
        const y =
          spectrumHeight -
          ((value - spectrumMin) / spectrumSpan) * spectrumHeight;
        return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" "),
  );

  const toggleChannel = (name: string) => {
    setSelected((current) => {
      if (current.includes(name))
        return current.filter((item) => item !== name);
      return current.length >= 8 ? current : [...current, name];
    });
  };

  return (
    <section className="eeg-preview" aria-label="Read-only EEG signal preview">
      <h6>Read-only signal preview</h6>
      <p className="project-message">
        Source identity {preview.source_content_identity.slice(0, 28)}… ·{" "}
        {preview.time_start_seconds.toFixed(2)}–
        {(preview.time_start_seconds + preview.duration_seconds).toFixed(2)} s ·{" "}
        {preview.sample_count.toLocaleString()} source samples. This is a
        bounded display of the selected window, not a signal-quality assessment.
        Source data is not changed or saved.
      </p>
      <div className="eeg-preview-controls">
        <label>
          Start time (s)
          <input
            aria-label="Preview start time in seconds"
            type="number"
            min="0"
            step="0.1"
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>
        <label>
          Duration (s, up to 20)
          <input
            aria-label="Preview duration in seconds"
            type="number"
            min="0.016"
            max="20"
            step="0.1"
            value={duration}
            onChange={(event) => setDuration(event.target.value)}
          />
        </label>
        <fieldset>
          <legend>EEG channels (up to 8)</legend>
          {eegChannels.map((channel) => (
            <label key={channel.name}>
              <input
                type="checkbox"
                checked={selected.includes(channel.name)}
                disabled={
                  !selected.includes(channel.name) && selected.length >= 8
                }
                onChange={() => toggleChannel(channel.name)}
              />
              {channel.name}
              {channel.marked_bad ? " (marked bad)" : ""}
            </label>
          ))}
        </fieldset>
        <button
          type="button"
          disabled={pending || selected.length === 0}
          aria-busy={pending}
          onClick={() =>
            onUpdate({
              timeStartSeconds: Number(start),
              durationSeconds: Number(duration),
              channelNames: selected,
            })
          }
        >
          {pending ? "Updating preview…" : "Update preview"}
        </button>
      </div>
      <h6>Channel traces · µV envelope</h6>
      <div className="eeg-preview-traces">
        {preview.traces.map((trace) => {
          const lows = trace.bins.map((bin) => bin.minimum_uv);
          const highs = trace.bins.map((bin) => bin.maximum_uv);
          const absoluteMax = Math.max(
            1,
            ...lows.map(Math.abs),
            ...highs.map(Math.abs),
          );
          const width = 620;
          const height = 72;
          const bars = trace.bins
            .map((bin, index) => {
              const x = (index / trace.bins.length) * width;
              const y1 =
                height / 2 - (bin.maximum_uv / absoluteMax) * (height / 2 - 2);
              const y2 =
                height / 2 - (bin.minimum_uv / absoluteMax) * (height / 2 - 2);
              return `M${x.toFixed(1)},${y1.toFixed(1)}L${x.toFixed(1)},${y2.toFixed(1)}`;
            })
            .join(" ");
          return (
            <figure key={trace.channel_name}>
              <figcaption>{trace.channel_name} · µV</figcaption>
              <svg
                role="img"
                aria-label={`${trace.channel_name} downsampled signal envelope`}
                viewBox={`0 0 ${width} ${height}`}
              >
                <path
                  className="eeg-preview-zero"
                  d={`M0 ${height / 2}H${width}`}
                />
                <path className="eeg-preview-line" d={bars} />
              </svg>
              <small>Envelope: minimum to maximum within each time bin.</small>
            </figure>
          );
        })}
      </div>
      <h6>Welch power spectrum</h6>
      <p className="project-message">
        Hamming window · {preview.spectrum.n_fft} point FFT · no annotation
        rejection · display in dB re 1 µV²/Hz.
      </p>
      <svg
        role="img"
        aria-label="Welch power spectrum for selected EEG channels"
        className="eeg-preview-spectrum"
        viewBox={`0 0 ${spectrumWidth} ${spectrumHeight}`}
      >
        {spectrumPaths.map((path, index) => (
          <path
            key={preview.spectrum.traces[index].channel_name}
            className={`eeg-preview-line eeg-preview-spectrum-${index % 8}`}
            d={path}
          />
        ))}
      </svg>
      <div className="eeg-preview-spectrum-labels">
        <span>{preview.spectrum.frequencies_hz[0]?.toFixed(1) ?? 0} Hz</span>
        <span>
          {preview.spectrum.frequencies_hz.at(-1)?.toFixed(1) ?? 0} Hz
        </span>
      </div>
      <ul className="eeg-preview-legend" aria-label="Spectrum trace channels">
        {preview.spectrum.traces.map((trace, index) => (
          <li key={trace.channel_name}>
            <span
              className={`eeg-preview-legend-swatch eeg-preview-spectrum-${index % 8}`}
              aria-hidden="true"
            />
            {trace.channel_name}
          </li>
        ))}
      </ul>
      <h6>Annotations in selected window ({preview.event_count})</h6>
      {preview.events_truncated && (
        <p role="status">Showing first 500 annotations.</p>
      )}
      {preview.events.length === 0 ? (
        <p>No annotations overlap this window.</p>
      ) : (
        <ul className="eeg-preview-events">
          {preview.events.map((event, index) => (
            <li key={`${event.onset_seconds}:${index}`}>
              {event.onset_seconds.toFixed(3)} s ·{" "}
              {event.duration_seconds.toFixed(3)} s · {event.description}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
