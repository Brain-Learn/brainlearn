import { useEffect, useRef, useState } from "react";

import {
  cancelCohort,
  downloadCohortResult,
  openCohort,
  recoverCohorts,
  startCohort,
} from "./cohorts";
import { discoverBidsEeg } from "./datasets";
import { openRun } from "./runs";
import type {
  BidsEegDiscovery,
  CohortRunRecord,
  RunResponse,
  Workflow,
} from "./types";

interface CohortLauncherProps {
  projectPath: string;
  token: string;
  workflow: Workflow;
  onChildRun: (response: RunResponse) => void;
}

const ACTIVE_COHORT_STATES = new Set(["running", "waiting_for_review"]);

function rootParameter(workflow: Workflow): string {
  const value = workflow.nodes
    .find((node) => node.type === "input.bids_eeg")
    ?.parameters.find((parameter) => parameter.id === "root")?.value;
  return typeof value === "string" ? value : "";
}

function withDatasetRoot(workflow: Workflow, root: string): Workflow {
  return {
    ...workflow,
    nodes: workflow.nodes.map((node) =>
      node.type !== "input.bids_eeg"
        ? node
        : {
            ...node,
            parameters: node.parameters.map((parameter) =>
              parameter.id === "root"
                ? { ...parameter, value: root }
                : parameter,
            ),
          },
    ),
  };
}

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function CohortLauncher({
  projectPath,
  token,
  workflow,
  onChildRun,
}: CohortLauncherProps) {
  const [datasetRoot, setDatasetRoot] = useState(() => rootParameter(workflow));
  const [discovery, setDiscovery] = useState<BidsEegDiscovery | null>(null);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [cohort, setCohort] = useState<CohortRunRecord | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [resultPending, setResultPending] = useState(false);
  const lastChildRunId = useRef<string | null>(null);
  const generation = useRef(0);
  const onChildRunRef = useRef(onChildRun);
  const cohortId = cohort?.id;
  const cohortState = cohort?.state;
  const cohortIsActive = cohortState
    ? ACTIVE_COHORT_STATES.has(cohortState)
    : false;

  useEffect(() => {
    onChildRunRef.current = onChildRun;
  }, [onChildRun]);

  useEffect(() => {
    if (!projectPath || !token) return;
    let stopped = false;
    void recoverCohorts(projectPath, token)
      .then((records) => {
        if (stopped) return;
        const active = records.find((item) =>
          ACTIVE_COHORT_STATES.has(item.state),
        );
        if (active) {
          generation.current += 1;
          lastChildRunId.current = null;
          setCohort(active);
          setMessage(
            "Recovered the persisted cohort run and its child workflows.",
          );
        }
      })
      .catch((reason: unknown) => {
        if (!stopped) {
          setMessage(
            reason instanceof Error
              ? reason.message
              : "Unable to recover cohort runs.",
          );
        }
      });
    return () => {
      stopped = true;
    };
  }, [projectPath, token]);

  useEffect(() => {
    setDatasetRoot(rootParameter(workflow));
  }, [workflow]);

  useEffect(() => {
    if (!projectPath || !token || !cohortId || !cohortIsActive) return;
    let stopped = false;
    const currentGeneration = generation.current;
    const refresh = async () => {
      try {
        const latest = await openCohort(projectPath, cohortId, token);
        if (stopped || generation.current !== currentGeneration) return;
        setCohort(latest);
        const activeRecording = latest.recordings.find(
          (item) =>
            item.child_run_id &&
            ["running", "waiting_for_review"].includes(item.state),
        );
        if (
          activeRecording?.child_run_id &&
          activeRecording.child_run_id !== lastChildRunId.current
        ) {
          const response = await openRun(
            projectPath,
            activeRecording.child_run_id,
            token,
          );
          if (stopped || generation.current !== currentGeneration) return;
          lastChildRunId.current = response.run_id;
          onChildRunRef.current(response);
        }
      } catch (reason) {
        if (stopped || generation.current !== currentGeneration) return;
        setMessage(
          reason instanceof Error
            ? reason.message
            : "Unable to refresh cohort progress.",
        );
      }
    };
    const timer = window.setInterval(() => void refresh(), 800);
    void refresh();
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [cohortId, cohortIsActive, projectPath, token]);

  const scan = async () => {
    if (!projectPath || !token) {
      setMessage(
        "Open a local project and enter the BrainLearn session token first.",
      );
      return;
    }
    if (!datasetRoot.trim()) {
      setMessage(
        "Enter a project-relative BIDS dataset folder before scanning.",
      );
      return;
    }
    setPending(true);
    setMessage(null);
    try {
      const result = await discoverBidsEeg(projectPath, datasetRoot.trim(), {
        token,
      });
      setDiscovery(result);
      const referenceSubjects = new Set(["001", "002", "003", "004", "005"]);
      const defaultSelection = /ds005284/.test(datasetRoot)
        ? result.recordings
            .filter(
              (item) =>
                item.status === "ready" && referenceSubjects.has(item.subject),
            )
            .map((item) => item.path)
        : [];
      setSelectedPaths(defaultSelection);
      if (result.status !== "ready")
        setMessage("BIDS metadata is not ready for a cohort run.");
      else if (defaultSelection.length === 0)
        setMessage("Select one or more compatible recordings below.");
    } catch (reason) {
      setDiscovery(null);
      setSelectedPaths([]);
      setMessage(
        reason instanceof Error
          ? reason.message
          : "Unable to scan the BIDS dataset.",
      );
    } finally {
      setPending(false);
    }
  };

  const toggleRecording = (path: string) => {
    setSelectedPaths((current) =>
      current.includes(path)
        ? current.filter((item) => item !== path)
        : [...current, path],
    );
  };

  const launch = async () => {
    if (!projectPath || !token || !discovery || selectedPaths.length === 0)
      return;
    setPending(true);
    setMessage(null);
    try {
      const record = await startCohort(
        projectPath,
        withDatasetRoot(workflow, datasetRoot.trim()),
        selectedPaths,
        token,
      );
      generation.current += 1;
      lastChildRunId.current = null;
      setCohort(record);
      setMessage(
        `Cohort ${record.id} started. Each recording will pause for explicit channel and ICA review.`,
      );
    } catch (reason) {
      setMessage(
        reason instanceof Error
          ? reason.message
          : "Unable to start the cohort workflow.",
      );
    } finally {
      setPending(false);
    }
  };

  const stop = async () => {
    if (!cohort || !projectPath || !token) return;
    setPending(true);
    try {
      setCohort(await cancelCohort(projectPath, cohort.id, token));
      setMessage("Cohort cancellation requested.");
    } catch (reason) {
      setMessage(
        reason instanceof Error
          ? reason.message
          : "Unable to cancel the cohort.",
      );
    } finally {
      setPending(false);
    }
  };

  const saveResult = async (kind: "report" | "summary") => {
    if (!cohort || !projectPath || !token) return;
    setResultPending(true);
    try {
      const blob = await downloadCohortResult(
        projectPath,
        cohort.id,
        kind,
        token,
      );
      download(
        blob,
        `${cohort.id}-${kind}.${kind === "report" ? "html" : "json"}`,
      );
    } catch (reason) {
      setMessage(
        reason instanceof Error
          ? reason.message
          : `Unable to download the ${kind}.`,
      );
    } finally {
      setResultPending(false);
    }
  };

  const activeRecording = cohort?.recordings.find((item) =>
    ["running", "waiting_for_review"].includes(item.state),
  );

  return (
    <section className="cohort-panel" aria-labelledby="cohort-heading">
      <div className="panel-heading">
        <span id="cohort-heading">EEG cohort run</span>
        <span>experimental</span>
      </div>
      <p className="muted">
        Select discovered recordings. The workflow runs sequentially and pauses
        on each recording for explicit bad-channel and ICA decisions. Method
        settings remain provisional and require independent review before
        scientific certification.
      </p>
      <label className="parameter">
        <span>Project-relative BIDS EEG folder</span>
        <input
          aria-label="Project-relative BIDS EEG folder for cohort"
          onChange={(event) => setDatasetRoot(event.target.value)}
          placeholder="datasets/openneuro/ds005284"
          value={datasetRoot}
        />
      </label>
      <button
        disabled={pending || !projectPath || !token}
        onClick={() => void scan()}
      >
        {pending ? "Working…" : "Scan cohort recordings"}
      </button>
      {discovery && (
        <div
          className="cohort-recordings"
          role="group"
          aria-label="Select BIDS EEG recordings"
        >
          <p>
            {discovery.dataset_name ?? discovery.dataset_path} ·{" "}
            {discovery.recordings.length} recording(s)
          </p>
          {discovery.recordings.map((recording) => (
            <label className="cohort-recording" key={recording.path}>
              <input
                checked={selectedPaths.includes(recording.path)}
                disabled={recording.status !== "ready" || pending}
                onChange={() => toggleRecording(recording.path)}
                type="checkbox"
              />
              <span>
                <strong>{recording.path}</strong>
                <small>
                  {recording.status} ·{" "}
                  {recording.recording_type ?? "recording type unspecified"} ·{" "}
                  {recording.sampling_frequency_hz ?? "unknown"} Hz ·{" "}
                  {recording.eeg_channel_count ?? "?"} EEG channels
                </small>
              </span>
            </label>
          ))}
          <button
            disabled={
              pending ||
              discovery.status !== "ready" ||
              selectedPaths.length === 0
            }
            onClick={() => void launch()}
          >
            {pending ? "Starting…" : `Run ${selectedPaths.length} recording(s)`}
          </button>
        </div>
      )}
      {cohort && (
        <div className="cohort-status" aria-live="polite">
          <strong>
            {cohort.id} · {cohort.state.replaceAll("_", " ")}
          </strong>
          <p>
            Selected {cohort.selected_count} · processed{" "}
            {cohort.processed_count} · excluded {cohort.excluded_count} · failed{" "}
            {cohort.failed_count} · cancelled {cohort.cancelled_count} ·
            participants {cohort.processed_participant_count}
          </p>
          {activeRecording && (
            <p>
              Current recording: {activeRecording.recording_path} ·{" "}
              {activeRecording.state.replaceAll("_", " ")}. Use the run panel to
              review the paused recording.
            </p>
          )}
          {cohort.failure_reason && <p role="alert">{cohort.failure_reason}</p>}
          {cohort.state === "succeeded" && (
            <div className="project-buttons">
              <button
                disabled={resultPending}
                onClick={() => void saveResult("report")}
              >
                Download HTML report
              </button>
              <button
                disabled={resultPending}
                onClick={() => void saveResult("summary")}
              >
                Download summary JSON
              </button>
            </div>
          )}
          {ACTIVE_COHORT_STATES.has(cohort.state) && (
            <button disabled={pending} onClick={() => void stop()}>
              Cancel cohort
            </button>
          )}
          <ul>
            {cohort.recordings.map((item) => (
              <li key={item.recording_path}>
                {item.recording_path} · {item.state.replaceAll("_", " ")}
                {item.failure_reason ? ` — ${item.failure_reason}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {message && (
        <div className="project-message" role="status">
          {message}
        </div>
      )}
    </section>
  );
}
