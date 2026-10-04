import { useEffect, useRef, useState } from "react";

import {
  discoverBidsEeg,
  identifyBidsEegRecording,
  inspectBidsEegSignal,
  previewBidsEegSignal,
  listResearcherDecisions,
  createResearcherDecision,
} from "./datasets";
import { BidsEegSignalPreviewPanel } from "./BidsEegSignalPreviewPanel";
import type {
  BidsEegDiscovery as BidsEegDiscoveryResult,
  BidsEegInputIdentity,
  BidsEegSignalInspection,
  BidsEegSignalPreview,
  ResearcherDecision,
} from "./types";

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

/** BIDS EEG discovery and explicit source identity actions for a project dataset. */
export function BidsEegDiscovery({
  projectPath,
  relativeDir,
  token,
}: BidsEegDiscoveryProps) {
  const [result, setResult] = useState<BidsEegDiscoveryResult | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [identities, setIdentities] = useState<
    Record<string, BidsEegInputIdentity>
  >({});
  const [identityPending, setIdentityPending] = useState<string | null>(null);
  const [identityMessage, setIdentityMessage] = useState<string | null>(null);
  const [signalInspections, setSignalInspections] = useState<
    Record<string, BidsEegSignalInspection>
  >({});
  const [inspectionPending, setInspectionPending] = useState<string | null>(
    null,
  );
  const [inspectionMessage, setInspectionMessage] = useState<string | null>(
    null,
  );
  const [signalPreviews, setSignalPreviews] = useState<
    Record<string, BidsEegSignalPreview>
  >({});
  const [previewPending, setPreviewPending] = useState<string | null>(null);
  const [previewMessage, setPreviewMessage] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<
    Record<string, ResearcherDecision[]>
  >({});
  const [decisionPending, setDecisionPending] = useState<string | null>(null);
  const [decisionMessage, setDecisionMessage] = useState<string | null>(null);
  const [researcher, setResearcher] = useState("");
  const [decisionChoice, setDecisionChoice] = useState<
    "" | ResearcherDecision["decision"]
  >("");
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionStart, setDecisionStart] = useState("");
  const [decisionEnd, setDecisionEnd] = useState("");
  const [decisionChannels, setDecisionChannels] = useState("");
  const operation = useRef(0);
  const identityOperation = useRef(0);
  const inspectionOperation = useRef(0);
  const previewOperation = useRef(0);
  const decisionOperation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    decisionOperation.current += 1;
    operation.current += 1;
    controller.current?.abort();
    controller.current = null;
    setResult(null);
    setPending(false);
    setMessage(null);
    setIdentities({});
    setIdentityPending(null);
    setIdentityMessage(null);
    setSignalInspections({});
    setInspectionPending(null);
    setInspectionMessage(null);
    setSignalPreviews({});
    setPreviewPending(null);
    setPreviewMessage(null);
    setDecisionPending(null);
    setDecisions({});
    setDecisionMessage(null);
    setResearcher("");
    setDecisionChoice("");
    setDecisionNote("");
    setDecisionStart("");
    setDecisionEnd("");
    setDecisionChannels("");
    identityOperation.current += 1;
    inspectionOperation.current += 1;
    previewOperation.current += 1;
    return () => {
      operation.current += 1;
      inspectionOperation.current += 1;
      previewOperation.current += 1;
      decisionOperation.current += 1;
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
    decisionOperation.current += 1;
    setDecisionPending(null);
    setDecisions({});
    identityOperation.current += 1;
    setIdentityPending(null);
    setIdentities({});
    setIdentityMessage(null);
    inspectionOperation.current += 1;
    setSignalInspections({});
    setInspectionPending(null);
    setInspectionMessage(null);
    previewOperation.current += 1;
    setSignalPreviews({});
    setPreviewPending(null);
    setPreviewMessage(null);
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

  const handleIdentify = async (recordingPath: string) => {
    if (identityPending || !projectPath || !relativeDir || !token) return;
    decisionOperation.current += 1;
    setDecisionPending(null);
    const sequence = ++identityOperation.current;
    setIdentityPending(recordingPath);
    setIdentityMessage(null);
    setIdentities((current) => {
      const next = { ...current };
      delete next[recordingPath];
      return next;
    });
    setDecisions((current) => {
      const next = { ...current };
      delete next[recordingPath];
      return next;
    });
    try {
      const identity = await identifyBidsEegRecording(
        projectPath,
        relativeDir,
        recordingPath,
        { token },
      );
      if (sequence === identityOperation.current) {
        setIdentities((current) => ({ ...current, [recordingPath]: identity }));
        if (identity.content_identity) {
          try {
            const records = await listResearcherDecisions(
              projectPath,
              relativeDir,
              recordingPath,
              identity.content_identity,
              token,
            );
            if (sequence === identityOperation.current)
              setDecisions((current) => ({
                ...current,
                [recordingPath]: records,
              }));
          } catch (error) {
            if (sequence === identityOperation.current)
              setDecisionMessage(
                error instanceof Error
                  ? error.message
                  : "Unable to load saved researcher decisions.",
              );
          }
        }
      }
    } catch (error) {
      if (sequence === identityOperation.current) {
        setIdentityMessage(
          error instanceof Error
            ? error.message
            : "Unable to create the BIDS EEG input identity.",
        );
      }
    } finally {
      if (sequence === identityOperation.current) setIdentityPending(null);
    }
  };

  const handleSaveDecision = async (recordingPath: string) => {
    const identity = identities[recordingPath];
    if (
      !identity?.content_identity ||
      !researcher.trim() ||
      !decisionChoice ||
      decisionPending
    )
      return;
    const sequence = ++decisionOperation.current;
    const capturedContext = {
      projectPath,
      relativeDir,
      token,
      contentIdentity: identity.content_identity,
    };
    const isCurrent = () =>
      sequence === decisionOperation.current &&
      projectPath === capturedContext.projectPath &&
      relativeDir === capturedContext.relativeDir &&
      token === capturedContext.token &&
      identities[recordingPath]?.content_identity ===
        capturedContext.contentIdentity;
    setDecisionPending(recordingPath);
    setDecisionMessage(null);
    try {
      const saved = await createResearcherDecision(
        projectPath,
        {
          researcher: researcher.trim(),
          dataset_path: relativeDir,
          recording_path: recordingPath,
          source_content_identity: identity.content_identity,
          decision: decisionChoice,
          note: decisionNote,
          time_start_seconds: decisionStart ? Number(decisionStart) : null,
          time_end_seconds: decisionEnd ? Number(decisionEnd) : null,
          channel_names: decisionChannels
            .split(",")
            .map((name) => name.trim())
            .filter(Boolean),
        },
        token,
      );
      if (isCurrent()) {
        setDecisions((current) => ({
          ...current,
          [recordingPath]: [...(current[recordingPath] ?? []), saved],
        }));
        setDecisionChoice("");
        setDecisionNote("");
      }
    } catch (error) {
      if (isCurrent())
        setDecisionMessage(
          error instanceof Error
            ? error.message
            : "Unable to save researcher decision.",
        );
    } finally {
      if (isCurrent()) setDecisionPending(null);
    }
  };

  const handleInspectSignal = async (recordingPath: string) => {
    if (inspectionPending || !projectPath || !relativeDir || !token) return;
    const sequence = ++inspectionOperation.current;
    setInspectionPending(recordingPath);
    setInspectionMessage(null);
    setSignalInspections((current) => {
      const next = { ...current };
      delete next[recordingPath];
      return next;
    });
    try {
      const inspection = await inspectBidsEegSignal(
        projectPath,
        relativeDir,
        recordingPath,
        { token },
      );
      if (sequence === inspectionOperation.current)
        setSignalInspections((current) => ({
          ...current,
          [recordingPath]: inspection,
        }));
    } catch (error) {
      if (sequence === inspectionOperation.current) {
        setInspectionMessage(
          error instanceof Error
            ? error.message
            : "Unable to inspect the EEG signal.",
        );
      }
    } finally {
      if (sequence === inspectionOperation.current) setInspectionPending(null);
    }
  };

  const handlePreviewSignal = async (
    recordingPath: string,
    options: {
      timeStartSeconds?: number;
      durationSeconds?: number;
      channelNames?: string[];
    } = {},
  ) => {
    if (previewPending || !projectPath || !relativeDir || !token) return;
    const sequence = ++previewOperation.current;
    setPreviewPending(recordingPath);
    setPreviewMessage(null);
    try {
      const preview = await previewBidsEegSignal(
        projectPath,
        relativeDir,
        recordingPath,
        { token, ...options },
      );
      if (sequence === previewOperation.current)
        setSignalPreviews((current) => ({
          ...current,
          [recordingPath]: preview,
        }));
    } catch (error) {
      if (sequence === previewOperation.current) {
        setPreviewMessage(
          error instanceof Error
            ? error.message
            : "Unable to create the EEG signal preview.",
        );
      }
    } finally {
      if (sequence === previewOperation.current) setPreviewPending(null);
    }
  };

  return (
    <section aria-label="BIDS EEG discovery" className="project-panel">
      <h5>BIDS EEG discovery</h5>
      <p className="project-message">
        Check BIDS metadata, inspect raw signal properties with MNE, or create
        an input identity as separate actions. Signal inspection reads without
        preloading or changing the source recording; identity creation reads
        source files in bounded chunks to calculate hashes.
      </p>
      <div className="project-buttons">
        <button
          data-testid="bids-eeg-discover"
          disabled={pending || !projectPath || !relativeDir || !token}
          aria-busy={pending}
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
                    {recording.status === "ready" && (
                      <div className="project-buttons">
                        <button
                          data-testid={`bids-eeg-identify-${recording.path}`}
                          disabled={identityPending !== null}
                          aria-busy={identityPending === recording.path}
                          onClick={() => void handleIdentify(recording.path)}
                        >
                          {identityPending === recording.path
                            ? "Hashing source files…"
                            : "Create input identity"}
                        </button>
                        <button
                          data-testid={`bids-eeg-preview-${recording.path}`}
                          disabled={
                            previewPending !== null ||
                            !signalInspections[recording.path]
                          }
                          aria-busy={previewPending === recording.path}
                          onClick={() =>
                            void handlePreviewSignal(recording.path)
                          }
                        >
                          {previewPending === recording.path
                            ? "Creating preview…"
                            : signalInspections[recording.path]
                              ? "Preview signal"
                              : "Inspect before preview"}
                        </button>
                        <button
                          data-testid={`bids-eeg-inspect-${recording.path}`}
                          disabled={inspectionPending !== null}
                          aria-busy={inspectionPending === recording.path}
                          onClick={() =>
                            void handleInspectSignal(recording.path)
                          }
                        >
                          {inspectionPending === recording.path
                            ? "Inspecting signal…"
                            : "Inspect signal with MNE"}
                        </button>
                      </div>
                    )}
                    {signalInspections[recording.path] && (
                      <div
                        data-testid={`bids-eeg-inspection-${recording.path}`}
                        className="project-message"
                        role="status"
                      >
                        MNE read-only inspection:{" "}
                        {
                          signalInspections[recording.path]
                            .sampling_frequency_hz
                        }{" "}
                        Hz ·{" "}
                        {signalInspections[
                          recording.path
                        ].sample_count.toLocaleString()}{" "}
                        samples ·{" "}
                        {signalInspections[
                          recording.path
                        ].duration_seconds.toFixed(2)}{" "}
                        s · {signalInspections[recording.path].channel_count}{" "}
                        channels (
                        {Object.entries(
                          signalInspections[recording.path].channel_types,
                        )
                          .map(([type, count]) => `${count} ${type}`)
                          .join(", ")}
                        ) ·{" "}
                        {signalInspections[recording.path].bad_channel_count}{" "}
                        marked bad ·{" "}
                        {signalInspections[recording.path].annotation_count}{" "}
                        annotations
                      </div>
                    )}
                    {signalPreviews[recording.path] && (
                      <BidsEegSignalPreviewPanel
                        preview={signalPreviews[recording.path]}
                        pending={previewPending === recording.path}
                        onUpdate={(options) =>
                          void handlePreviewSignal(recording.path, options)
                        }
                      />
                    )}
                    {identities[recording.path] && (
                      <div data-testid={`bids-eeg-identity-${recording.path}`}>
                        <p className="project-message" role="status">
                          {identities[recording.path].status ===
                          "resource_limit"
                            ? identities[recording.path].message
                            : `Input identity: ${identities[recording.path].content_identity}`}
                        </p>
                        {identities[recording.path].files.map((file) => (
                          <div className="project-message" key={file.path}>
                            {file.path} · {file.byte_size} bytes · SHA-256{" "}
                            {file.sha256}
                          </div>
                        ))}
                      </div>
                    )}
                    {identities[recording.path]?.content_identity && (
                      <div
                        className="researcher-decisions"
                        data-testid={`researcher-decisions-${recording.path}`}
                      >
                        <h4>Researcher annotation and inspection decision</h4>
                        <p className="project-message">
                          Saved separately from source data and bound to this
                          recording identity. Each submission records an
                          explicit decision.
                        </p>
                        <label>
                          Researcher
                          <input
                            value={researcher}
                            onChange={(event) =>
                              setResearcher(event.target.value)
                            }
                            maxLength={200}
                          />
                        </label>
                        <label>
                          Decision
                          <select
                            required
                            value={decisionChoice}
                            onChange={(event) =>
                              setDecisionChoice(
                                event.target.value as typeof decisionChoice,
                              )
                            }
                          >
                            <option value="">Choose a decision</option>
                            <option value="accepted">Accepted</option>
                            <option value="rejected">Rejected</option>
                            <option value="needs_review">Needs review</option>
                          </select>
                        </label>
                        <label>
                          Annotation note
                          <textarea
                            value={decisionNote}
                            onChange={(event) =>
                              setDecisionNote(event.target.value)
                            }
                            maxLength={4000}
                          />
                        </label>
                        <div className="project-buttons">
                          <label>
                            Start time (s)
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={decisionStart}
                              onChange={(event) =>
                                setDecisionStart(event.target.value)
                              }
                            />
                          </label>
                          <label>
                            End time (s)
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={decisionEnd}
                              onChange={(event) =>
                                setDecisionEnd(event.target.value)
                              }
                            />
                          </label>
                          <label>
                            Channels (comma separated)
                            <input
                              value={decisionChannels}
                              onChange={(event) =>
                                setDecisionChannels(event.target.value)
                              }
                              placeholder="Cz, Pz"
                            />
                          </label>
                          <button
                            disabled={
                              !researcher.trim() ||
                              !decisionChoice ||
                              decisionPending !== null
                            }
                            onClick={() =>
                              void handleSaveDecision(recording.path)
                            }
                          >
                            {decisionPending === recording.path
                              ? "Saving…"
                              : "Save explicit decision"}
                          </button>
                        </div>
                        {(decisions[recording.path] ?? []).map((item) => (
                          <div className="project-message" key={item.id}>
                            {item.decision.replace("_", " ")} ·{" "}
                            {item.researcher} ·{" "}
                            {new Date(item.created_at).toLocaleString()}
                            {item.note && <p>{item.note}</p>}
                            {(item.time_start_seconds !== null ||
                              item.time_end_seconds !== null) && (
                              <p>
                                {item.time_start_seconds ?? 0}–
                                {item.time_end_seconds ?? "?"} s
                              </p>
                            )}
                            {item.channel_names.length > 0 && (
                              <p>Channels: {item.channel_names.join(", ")}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
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
      {identityMessage && (
        <div className="project-message" role="alert">
          {identityMessage}
        </div>
      )}
      {inspectionMessage && (
        <div className="project-message" role="alert">
          {inspectionMessage}
        </div>
      )}
      {previewMessage && (
        <div className="project-message" role="alert">
          {previewMessage}
        </div>
      )}
      {decisionMessage && (
        <div className="project-message" role="alert">
          {decisionMessage}
        </div>
      )}
    </section>
  );
}
