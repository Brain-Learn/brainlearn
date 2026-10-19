import { CircleAlert, RotateCcw, Square } from "lucide-react";
import { useState } from "react";

import { EEGFilterComparisonPreview } from "./EEGFilterComparisonPreview";
import { ICAComponentDiagnostics } from "./ICAComponentDiagnostics";
import type {
  ArtifactRecord,
  EEGFilterComparisonPreview as EEGFilterComparisonPreviewData,
  EEGICAComponentDiagnostics as EEGICAComponentDiagnosticsData,
  NodeRunRecord,
  QCReviewRecord,
  RunRecord,
  RunResponse,
  RunState,
} from "./types";

const RUN_STATE_LABEL: Record<RunState, string> = {
  queued: "Queued",
  running: "Running",
  waiting_for_review: "Awaiting review",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

const STREAM_STATE_LABEL: Record<string, string> = {
  idle: "",
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function shortSha(sha256: string): string {
  return sha256.slice(0, 12);
}

function ReviewControls({
  node,
  disabled,
  onReview,
}: {
  node: NodeRunRecord;
  disabled: boolean;
  onReview: (
    nodeRunId: string,
    decision: "approved" | "rejected",
    note: string,
    qc?: {
      reviewer: string;
      excludedChannelNames: string[];
      excludedIcaComponents: number[];
    },
  ) => void;
}) {
  const [note, setNote] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [excludedChannels, setExcludedChannels] = useState("");
  const [excludedComponents, setExcludedComponents] = useState("");
  const [componentError, setComponentError] = useState<string | null>(null);
  const requiresBadChannelQc = node.node_type === "eeg.bad_channel_review";
  const requiresIcaQc = node.node_type === "eeg.ica_review";
  const requiresQc = requiresBadChannelQc || requiresIcaQc;
  const save = (decision: "approved" | "rejected") => {
    const names = excludedChannels
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
    if (requiresIcaQc) {
      const entries = excludedComponents
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean);
      const oneBased = entries.map(Number);
      if (oneBased.some((index) => !Number.isInteger(index) || index < 1)) {
        setComponentError(
          "Enter component numbers starting at 1, separated by commas.",
        );
        return;
      }
      setComponentError(null);
      onReview(node.id, decision, note, {
        reviewer: reviewer.trim(),
        excludedChannelNames: [],
        excludedIcaComponents: oneBased.map((index) => index - 1),
      });
      return;
    }
    if (requiresBadChannelQc) {
      onReview(node.id, decision, note, {
        reviewer: reviewer.trim(),
        excludedChannelNames: names,
        excludedIcaComponents: [],
      });
      return;
    }
    onReview(node.id, decision, note);
  };
  return (
    <div className="review-controls">
      {requiresBadChannelQc && (
        <>
          <p
            id={`bad-channel-review-help-${node.node_id}`}
            className="project-message"
          >
            Use EEG channel names from the signal preview. BrainLearn checks
            each name against the current recording and saves this decision with
            the run and input identities. The source signal is unchanged. Enter
            a researcher name to enable either decision.
          </p>
          <label>
            Researcher
            <input
              aria-label={`Researcher for ${node.node_id}`}
              aria-describedby={`bad-channel-review-help-${node.node_id}`}
              maxLength={200}
              onChange={(event) => setReviewer(event.target.value)}
              required
              value={reviewer}
            />
          </label>
          <label>
            Bad EEG channels to exclude (comma-separated)
            <input
              aria-label={`Bad EEG channels for ${node.node_id}`}
              maxLength={4096}
              onChange={(event) => setExcludedChannels(event.target.value)}
              placeholder="Fp1, Cz"
              value={excludedChannels}
            />
          </label>
        </>
      )}
      {requiresIcaQc && (
        <>
          <p className="project-message">
            Review the fitted components above. Enter one-based component
            numbers to exclude, such as 1, 3. The decision is saved with the
            exact raw and fitted artifacts before MNE applies it.
          </p>
          <label>
            Researcher
            <input
              aria-label={`Researcher for ${node.node_id}`}
              maxLength={200}
              onChange={(event) => setReviewer(event.target.value)}
              required
              value={reviewer}
            />
          </label>
          <label>
            Components to exclude (one-based numbers, comma-separated)
            <input
              aria-label={`ICA components for ${node.node_id}`}
              inputMode="numeric"
              maxLength={4096}
              onChange={(event) => {
                setExcludedComponents(event.target.value);
                setComponentError(null);
              }}
              placeholder="1, 3"
              value={excludedComponents}
            />
          </label>
          {componentError && <p role="alert">{componentError}</p>}
        </>
      )}
      <input
        aria-label={`Review note for ${node.node_id}`}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Review note (optional)"
        type="text"
        value={note}
      />
      <button
        disabled={disabled || (requiresQc && !reviewer.trim())}
        onClick={() => save("approved")}
      >
        Approve
      </button>
      <button
        className="review-reject"
        disabled={disabled || (requiresQc && !reviewer.trim())}
        onClick={() => save("rejected")}
      >
        Reject
      </button>
    </div>
  );
}

export function RunDrawer({
  run,
  runPath,
  launching,
  streamStatus,
  streamNotice,
  message,
  reducedMotion,
  projectOpen,
  history,
  historyError,
  historyPending,
  qcReviews,
  onCancel,
  onReview,
  onRecover,
  onOpenRun,
  onOpenArtifact,
  filterPreview,
  onCloseFilterPreview,
  icaDiagnostics,
  onCloseIcaDiagnostics,
  actionPending,
}: {
  run: RunRecord | null;
  runPath: string;
  launching: boolean;
  streamStatus: "idle" | "connecting" | "live" | "reconnecting";
  streamNotice: string | null;
  message: string | null;
  reducedMotion: boolean;
  projectOpen: boolean;
  history: RunResponse[];
  historyError: string | null;
  historyPending: boolean;
  qcReviews: QCReviewRecord[];
  onCancel: () => void;
  onReview: (
    nodeRunId: string,
    decision: "approved" | "rejected",
    note: string,
    qc?: {
      reviewer: string;
      excludedChannelNames: string[];
      excludedIcaComponents: number[];
    },
  ) => void;
  onRecover: () => void;
  onOpenRun: (runId: string, path: string) => void;
  onOpenArtifact: (artifact: ArtifactRecord) => void;
  filterPreview: EEGFilterComparisonPreviewData | null;
  onCloseFilterPreview: () => void;
  icaDiagnostics: EEGICAComponentDiagnosticsData | null;
  onCloseIcaDiagnostics: () => void;
  actionPending: boolean;
}) {
  const terminal = run
    ? ["succeeded", "failed", "cancelled"].includes(run.state)
    : false;
  const waitingNodes = run
    ? run.node_runs.filter((node) => node.state === "waiting_for_review")
    : [];
  const failure = run?.failure ?? null;

  return (
    <section
      className={`run-drawer${run ? " has-run" : ""}`}
      data-reduced-motion={reducedMotion ? "true" : "false"}
      data-stream-status={streamStatus}
    >
      <div className="run-summary" data-state={run?.state ?? "idle"}>
        <span className={`status-dot ${run ? `state-${run.state}` : ""}`} />
        <strong>{run ? RUN_STATE_LABEL[run.state] : "No active run"}</strong>
        {run && <code>{run.id}</code>}
        {run && <span className="run-path">{runPath}</span>}
        <span className="run-spacer" />
        {run && !terminal && (
          <button disabled={actionPending} onClick={onCancel}>
            <Square size={14} /> Cancel
          </button>
        )}
        {projectOpen && (
          <button
            disabled={actionPending}
            onClick={onRecover}
            title="Recover interrupted runs"
          >
            <RotateCcw size={14} /> Recover
          </button>
        )}
        {!run && !launching && (
          <span className="run-idle-note">
            Run the workflow to see progress here.
          </span>
        )}
        {launching && <span className="run-idle-note">Launching…</span>}
      </div>

      {run && (
        <div className="run-stream" role="status">
          {STREAM_STATE_LABEL[streamStatus] && (
            <span className="run-stream-state">
              {STREAM_STATE_LABEL[streamStatus]}
            </span>
          )}
          {streamNotice && (
            <span className="run-stream-notice">{streamNotice}</span>
          )}
        </div>
      )}

      {message && (
        <div className="run-message" role="status">
          {message}
        </div>
      )}

      {projectOpen && (
        <div className="run-history">
          <span className="run-history-heading">
            Runs{historyPending ? " (loading…)" : ""}
          </span>
          {history.length === 0 && !historyPending && !historyError && (
            <span className="run-idle-note">No runs recorded yet.</span>
          )}
          {historyError && (
            <span className="run-history-error" role="status">
              {historyError}
            </span>
          )}
          {history.map((entry) => (
            <div
              className="run-history-row"
              key={`${entry.path}::${entry.run_id}`}
            >
              <span className="run-node-name">{entry.run_id}</span>
              <span className="run-node-state">{entry.run.state}</span>
              <button
                aria-label={`Open run ${entry.run_id}`}
                disabled={actionPending || entry.run_id === run?.id}
                onClick={() => onOpenRun(entry.run_id, entry.path)}
              >
                Open
              </button>
            </div>
          ))}
        </div>
      )}

      {run && (
        <>
          {failure && (
            <div className="run-failure" role="alert">
              <CircleAlert size={14} /> {failure.code}: {failure.message}
            </div>
          )}

          {waitingNodes.length > 0 && (
            <div className="run-reviews">
              {waitingNodes.map((node) => (
                <div className="run-review-row" key={node.id}>
                  <span className="run-node-name">{node.node_id}</span>
                  <ReviewControls
                    disabled={actionPending}
                    node={node}
                    onReview={onReview}
                  />
                </div>
              ))}
            </div>
          )}

          {qcReviews.length > 0 && (
            <div
              className="run-qc-reviews"
              aria-label="Saved EEG review decisions"
            >
              <h4>Saved bad-channel decisions</h4>
              {qcReviews.map((review) => (
                <p key={review.id}>
                  {review.decision} · {review.reviewer} · {review.node_id}
                  {review.excluded_channel_names.length > 0
                    ? ` · Excluded: ${review.excluded_channel_names.join(", ")}`
                    : " · No channels selected"}
                  {review.note ? ` · ${review.note}` : ""}
                </p>
              ))}
            </div>
          )}

          <div className="run-nodes">
            {run.node_runs.map((node) => (
              <div
                className="run-node-row"
                data-state={node.state}
                key={node.id}
              >
                <span className="run-node-name">{node.node_id}</span>
                <span className="run-node-type">{node.node_type}</span>
                <span className="run-node-state">{node.state}</span>
                <span className="run-node-attempt">
                  {node.state === "cache_reused"
                    ? "cache reuse"
                    : node.attempt > 0
                      ? `attempt ${node.attempt}`
                      : "not run"}
                </span>
                {node.failure && (
                  <span
                    className="run-node-failure"
                    title={node.failure.message}
                  >
                    {node.failure.message}
                  </span>
                )}
                {node.artifacts.length > 0 && (
                  <span className="run-node-artifacts">
                    {node.artifacts.length} artifact
                    {node.artifacts.length === 1 ? "" : "s"}
                  </span>
                )}
              </div>
            ))}
          </div>

          {run.node_runs.some((node) => node.artifacts.length > 0) && (
            <div className="run-artifacts">
              {run.node_runs.flatMap((node) =>
                node.artifacts.map((artifact) => (
                  <div className="run-artifact-row" key={artifact.artifact_id}>
                    <span className="run-node-name">{artifact.port_id}</span>
                    <code className="run-artifact-path">{artifact.path}</code>
                    <span>{formatBytes(artifact.byte_size)}</span>
                    <span>sha256:{shortSha(artifact.sha256)}</span>
                    <span>{artifact.media_type}</span>
                    <button
                      disabled={actionPending}
                      onClick={() => onOpenArtifact(artifact)}
                    >
                      {artifact.media_type ===
                      "application/vnd.brainlearn.eeg-filter-preview+json"
                        ? "View comparison"
                        : artifact.media_type ===
                            "application/vnd.brainlearn.eeg-ica-diagnostics+json"
                          ? "View components"
                          : "Open"}
                    </button>
                  </div>
                )),
              )}
            </div>
          )}

          {filterPreview && (
            <EEGFilterComparisonPreview
              onClose={onCloseFilterPreview}
              preview={filterPreview}
            />
          )}
          {icaDiagnostics && (
            <ICAComponentDiagnostics
              diagnostics={icaDiagnostics}
              onClose={onCloseIcaDiagnostics}
            />
          )}

          <div className="run-events">
            {run.events.map((event) => (
              <div
                className="run-event-row"
                data-kind={event.kind}
                key={event.seq}
              >
                <span className="run-event-seq">{event.seq}</span>
                <span className="run-event-kind">{event.kind}</span>
                {event.node_run_id && (
                  <span className="run-node-name">{event.node_run_id}</span>
                )}
                <span className="run-event-message">{event.message}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
