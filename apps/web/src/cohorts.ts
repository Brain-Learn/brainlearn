import type { CohortRunRecord, Workflow } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function assertCohortRun(value: unknown): asserts value is CohortRunRecord {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.2" ||
    typeof value.id !== "string" ||
    ![
      "running",
      "waiting_for_review",
      "succeeded",
      "failed",
      "cancelled",
    ].includes(String(value.state)) ||
    typeof value.dataset_path !== "string" ||
    typeof value.workflow_id !== "string" ||
    !Array.isArray(value.recordings) ||
    !value.recordings.every(
      (item) =>
        isRecord(item) &&
        typeof item.recording_path === "string" &&
        typeof item.participant === "string" &&
        [
          "selected",
          "running",
          "waiting_for_review",
          "processed",
          "excluded",
          "failed",
          "cancelled",
        ].includes(String(item.state)) &&
        Array.isArray(item.input_files) &&
        Array.isArray(item.qc_review_ids) &&
        Array.isArray(item.outputs),
    ) ||
    typeof value.selected_count !== "number" ||
    typeof value.processed_count !== "number" ||
    typeof value.excluded_count !== "number" ||
    typeof value.failed_count !== "number" ||
    typeof value.cancelled_count !== "number"
  ) {
    throw new Error("The cohort response does not match contract version 1.0.");
  }
}

function authHeaders(token: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function readError(response: Response, fallback: string): Promise<Error> {
  const payload: unknown = await response.json().catch(() => null);
  return new Error(
    isRecord(payload) && typeof payload.detail === "string"
      ? payload.detail
      : `${fallback} (HTTP ${response.status})`,
  );
}

async function cohortRequest(
  endpoint: string,
  payload: Record<string, unknown>,
  token: string,
): Promise<CohortRunRecord> {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw await readError(response, "Cohort request failed");
  const value: unknown = await response.json();
  assertCohortRun(value);
  return value;
}

export function startCohort(
  path: string,
  workflow: Workflow,
  recordingPaths: string[],
  token: string,
): Promise<CohortRunRecord> {
  const datasetPath = workflow.nodes
    .find((node) => node.type === "input.bids_eeg")
    ?.parameters.find((parameter) => parameter.id === "root")?.value;
  const isReference =
    typeof datasetPath === "string" && /ds005284/.test(datasetPath);
  return cohortRequest(
    "/api/cohorts/start",
    {
      path,
      workflow,
      recording_paths: recordingPaths,
      seed: 0,
      dataset_id: isReference ? "ds005284" : null,
      dataset_version: isReference ? "1.0.0" : null,
    },
    token,
  );
}

export function openCohort(
  path: string,
  cohortId: string,
  token: string,
): Promise<CohortRunRecord> {
  return cohortRequest(
    "/api/cohorts/open",
    { path, cohort_id: cohortId },
    token,
  );
}

export async function recoverCohorts(
  path: string,
  token: string,
): Promise<CohortRunRecord[]> {
  const response = await fetch("/api/cohorts/recover", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path }),
  });
  if (!response.ok)
    throw await readError(response, "Unable to recover cohort runs");
  const value: unknown = await response.json();
  if (!Array.isArray(value))
    throw new Error("Cohort recovery returned an invalid response.");
  value.forEach(assertCohortRun);
  return value;
}

export function cancelCohort(
  path: string,
  cohortId: string,
  token: string,
): Promise<CohortRunRecord> {
  return cohortRequest(
    "/api/cohorts/cancel",
    { path, cohort_id: cohortId },
    token,
  );
}

export async function downloadCohortResult(
  path: string,
  cohortId: string,
  kind: "report" | "summary",
  token: string,
): Promise<Blob> {
  const response = await fetch("/api/cohorts/result", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path, cohort_id: cohortId, kind }),
  });
  if (!response.ok)
    throw await readError(response, `Unable to open cohort ${kind}`);
  return response.blob();
}

export async function downloadCohortProject(
  path: string,
  cohortId: string,
  token: string,
): Promise<Blob> {
  const response = await fetch("/api/cohorts/export", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path, cohort_id: cohortId }),
  });
  if (!response.ok)
    throw await readError(
      response,
      "Unable to export the cohort Python project",
    );
  return response.blob();
}
