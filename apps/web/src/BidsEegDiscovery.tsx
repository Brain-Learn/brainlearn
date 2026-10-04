import { useEffect, useRef, useState } from "react";

import { discoverBidsEeg } from "./datasets";
import type { BidsEegDiscovery as BidsEegDiscoveryResult } from "./types";

interface BidsEegDiscoveryProps {
  projectPath: string;
  relativeDir: string;
  token: string;
}

const STATUS_LABELS: Record<BidsEegDiscoveryResult["status"], string> = {
  ready: "BIDS EEG metadata is ready.",
  incomplete_metadata:
    "BIDS EEG recordings were found, but required metadata is incomplete.",
  unsupported:
    "This dataset contains an unsupported BIDS version, type, or EEG format.",
  not_bids: "This directory does not contain a BIDS dataset description.",
  no_recordings:
    "BIDS metadata was found, but no raw EEG recordings were discovered.",
};

/** Metadata-only BIDS EEG discovery for an already selected project dataset. */
export function BidsEegDiscovery({
  projectPath,
  relativeDir,
  token,
}: BidsEegDiscoveryProps) {
  const [result, setResult] = useState<BidsEegDiscoveryResult | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const operation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    operation.current += 1;
    controller.current?.abort();
    controller.current = null;
    setResult(null);
    setPending(false);
    setMessage(null);
    return () => {
      operation.current += 1;
      controller.current?.abort();
      controller.current = null;
    };
  }, [projectPath, relativeDir, token]);

  const handleDiscover = async () => {
    if (pending || !projectPath || !relativeDir || !token) return;
    const sequence = ++operation.current;
    const abortController = new AbortController();
    controller.current = abortController;
    setPending(true);
    setMessage(null);
    setResult(null);
    try {
      const discovered = await discoverBidsEeg(projectPath, relativeDir, {
        token,
        signal: abortController.signal,
      });
      if (sequence !== operation.current) return;
      setResult(discovered);
    } catch (error) {
      if (sequence !== operation.current || abortController.signal.aborted)
        return;
      setMessage(
        error instanceof Error
          ? error.message
          : "Unable to inspect BIDS EEG metadata.",
      );
    } finally {
      if (sequence === operation.current) {
        controller.current = null;
        setPending(false);
      }
    }
  };

  return (
    <section aria-label="BIDS EEG discovery" className="project-panel">
      <h5>BIDS EEG discovery</h5>
      <p className="project-message">
        Checks recording filenames and required BIDS metadata only. Signal bytes
        are not opened, and source files are not changed.
      </p>
      <div className="project-buttons">
        <button
          data-testid="bids-eeg-discover"
          disabled={pending || !projectPath || !relativeDir || !token}
          onClick={() => void handleDiscover()}
        >
          {pending ? "Checking BIDS metadata…" : "Check BIDS EEG metadata"}
        </button>
      </div>
      {message && (
        <div className="project-message" role="alert">
          {message}
        </div>
      )}
      {result && (
        <div data-testid="bids-eeg-result">
          <p className="project-message" role="status">
            {STATUS_LABELS[result.status]}
            {result.dataset_name ? ` ${result.dataset_name}` : ""}
            {result.bids_version ? ` · BIDS ${result.bids_version}` : ""}
            {` · ${result.recordings.length} EEG ${result.recordings.length === 1 ? "recording" : "recordings"}`}
          </p>
          {result.recordings.length > 0 && (
            <ul className="library-list">
              {result.recordings.map((recording) => (
                <li key={recording.path}>
                  <div>
                    <strong>{recording.path}</strong>
                    <div>
                      {recording.task} ·{" "}
                      {recording.format ?? "unsupported format"} ·{" "}
                      {recording.sampling_frequency_hz === null
                        ? "sampling frequency missing"
                        : `${recording.sampling_frequency_hz} Hz`}
                      {recording.eeg_channel_count === null
                        ? " · channel count unavailable"
                        : ` · ${recording.eeg_channel_count} EEG channels`}
                      {recording.event_count === null
                        ? " · events unavailable"
                        : ` · ${recording.event_count} events`}
                    </div>
                    {recording.issues.map((issue) => (
                      <div
                        className="project-message"
                        key={`${issue.code}:${issue.path}`}
                      >
                        {issue.message}
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {result.issues.map((issue) => (
            <div
              className="project-message"
              key={`${issue.code}:${issue.path}`}
            >
              {issue.message}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
