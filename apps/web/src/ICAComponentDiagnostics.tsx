import { useState } from "react";

import type { EEGICAComponentDiagnostics } from "./types";

function envelopeChart(
  points: EEGICAComponentDiagnostics["time_courses"][number]["points"],
) {
  const values = points.flatMap((point) => [point.minimum, point.maximum]);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  return (
    <svg
      aria-label="Component source time course envelope"
      className="ica-chart"
      role="img"
      viewBox="0 0 460 110"
    >
      <line className="ica-chart-axis" x1="20" x2="440" y1="94" y2="94" />
      <line className="ica-chart-axis" x1="20" x2="20" y1="12" y2="94" />
      {points.map((point, index) => {
        const x = 20 + (index / Math.max(1, points.length - 1)) * 420;
        const y = (value: number) => 88 - ((value - minimum) / span) * 72;
        return (
          <line
            key={`${point.time_seconds}-${index}`}
            className="ica-envelope"
            x1={x}
            x2={x}
            y1={y(point.minimum)}
            y2={y(point.maximum)}
          />
        );
      })}
    </svg>
  );
}

function spectrumChart(
  frequencies: number[],
  power: number[],
  component: number,
) {
  const values = power.map((value) => 10 * Math.log10(Math.max(value, 1e-30)));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  const maxFrequency = frequencies[frequencies.length - 1] || 1;
  const points = values
    .map((value, index) => {
      const x = 20 + (frequencies[index] / maxFrequency) * 420;
      const y = 88 - ((value - minimum) / span) * 72;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      aria-label={`Component ${component + 1} power spectrum in decibels`}
      className="ica-chart"
      role="img"
      viewBox="0 0 460 110"
    >
      <line className="ica-chart-axis" x1="20" x2="440" y1="94" y2="94" />
      <line className="ica-chart-axis" x1="20" x2="20" y1="12" y2="94" />
      <polyline className="ica-spectrum" points={points} />
    </svg>
  );
}

export function ICAComponentDiagnostics({
  diagnostics,
  onClose,
}: {
  diagnostics: EEGICAComponentDiagnostics;
  onClose: () => void;
}) {
  const [component, setComponent] = useState(0);
  const map = diagnostics.component_maps[component];
  const course = diagnostics.time_courses[component];
  const spectrum = diagnostics.spectra[component];
  const maximumWeight = Math.max(
    ...map.channel_weights.map((weight) => Math.abs(weight)),
    1e-30,
  );

  return (
    <section
      className="ica-diagnostics"
      aria-labelledby="ica-diagnostics-title"
    >
      <div className="ica-diagnostics-heading">
        <div>
          <h3 id="ica-diagnostics-title">ICA component review</h3>
          <p>
            {diagnostics.channels.length} EEG channels ·{" "}
            {diagnostics.sampling_frequency_hz.toLocaleString()} Hz ·{" "}
            {diagnostics.preview_duration_seconds.toFixed(1)} s shown
          </p>
        </div>
        <button aria-label="Close ICA component diagnostics" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="ica-diagnostics-note">
        {diagnostics.fit_converged === true &&
        diagnostics.fit_iterations !== undefined
          ? `FastICA converged in ${diagnostics.fit_iterations} of 1000 iterations. `
          : null}
        {diagnostics.excluded_bad_channels?.length
          ? `MNE excluded EEG channels marked bad: ${diagnostics.excluded_bad_channels.join(", ")}. `
          : null}
        Review components individually. BrainLearn applies no exclusions until a
        researcher saves a decision.
        {diagnostics.topography_available
          ? " Channel locations are available; channel weights are shown below."
          : " This recording has no sensor locations, so the channel-weight map is not a scalp topography."}
      </p>
      <label className="ica-component-select">
        Component
        <select
          aria-label="ICA component"
          onChange={(event) => setComponent(Number(event.target.value))}
          value={component}
        >
          {Array.from(
            { length: diagnostics.component_count },
            (_item, index) => (
              <option key={index} value={index}>
                {index + 1} of {diagnostics.component_count}
              </option>
            ),
          )}
        </select>
      </label>
      <div className="ica-diagnostics-columns">
        <figure>
          <figcaption>Channel weights · component {component + 1}</figcaption>
          <div className="ica-weight-list">
            {diagnostics.channels.map((channel, index) => {
              const weight = map.channel_weights[index];
              return (
                <div className="ica-weight-row" key={channel}>
                  <span>{channel}</span>
                  <svg
                    aria-hidden="true"
                    className="ica-weight-track"
                    viewBox="0 0 100 10"
                  >
                    <line x1="50" x2="50" y1="0" y2="10" />
                    <rect
                      className={weight < 0 ? "negative" : "positive"}
                      height="6"
                      width={(Math.abs(weight) / maximumWeight) * 50}
                      x={
                        weight < 0
                          ? 50 - (Math.abs(weight) / maximumWeight) * 50
                          : 50
                      }
                      y="2"
                    />
                  </svg>
                  <code>{weight.toPrecision(3)}</code>
                </div>
              );
            })}
          </div>
          <p className="ica-chart-caption">
            Signed channel weights; a larger magnitude indicates a stronger
            contribution.
          </p>
        </figure>
        <figure>
          <figcaption>
            Source time course · first{" "}
            {diagnostics.preview_duration_seconds.toFixed(1)} s
          </figcaption>
          {envelopeChart(course.points)}
          <p className="ica-chart-caption">{diagnostics.time_course_unit}</p>
        </figure>
        <figure>
          <figcaption>
            Power spectrum · 0–
            {diagnostics.frequencies_hz[
              diagnostics.frequencies_hz.length - 1
            ]?.toFixed(0) ?? "0"}{" "}
            Hz
          </figcaption>
          {spectrumChart(diagnostics.frequencies_hz, spectrum.power, component)}
          <p className="ica-chart-caption">
            {diagnostics.spectrum_method} · {diagnostics.spectrum_unit}
          </p>
        </figure>
      </div>
      <p className="ica-diagnostics-note">{diagnostics.note}</p>
    </section>
  );
}
