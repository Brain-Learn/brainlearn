import { CircleAlert, RotateCcw, Square } from "lucide-react";
import { useState } from "react";

import type {
  ArtifactRecord,
  NodeRunRecord,
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
  ) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="review-controls">
      <input
        aria-label={`Review note for ${node.node_id}`}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Review note (optional)"
        type="text"
        value={note}
      />
      <button
        disabled={disabled}
        onClick={() => onReview(node.id, "approved", note)}
      >
        Approve
      </button>
      <button
        className="review-reject"
        disabled={disabled}
        onClick={() => onReview(node.id, "rejected", note)}
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
  onCancel,
  onReview,
  onRecover,
  onOpenRun,
  onOpenArtifact,
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
  onCancel: () => void;
  onReview: (
    nodeRunId: string,
    decision: "approved" | "rejected",
    note: string,
  ) => void;
  onRecover: () => void;
  onOpenRun: (runId: string, path: string) => void;
  onOpenArtifact: (artifact: ArtifactRecord) => void;
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
                      Open
                    </button>
                  </div>
                )),
              )}
            </div>
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
