import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { CohortLauncher } from "./CohortLauncher";
import type { BidsEegDiscovery, CohortRunRecord, Workflow } from "./types";

const mocked = vi.hoisted(() => ({
  cancelCohort: vi.fn(),
  discoverBidsEeg: vi.fn(),
  downloadCohortResult: vi.fn(),
  openCohort: vi.fn(),
  openRun: vi.fn(),
  recoverCohorts: vi.fn(),
  startCohort: vi.fn(),
}));

vi.mock("./cohorts", () => ({
  cancelCohort: mocked.cancelCohort,
  downloadCohortResult: mocked.downloadCohortResult,
  openCohort: mocked.openCohort,
  recoverCohorts: mocked.recoverCohorts,
  startCohort: mocked.startCohort,
}));

vi.mock("./datasets", () => ({ discoverBidsEeg: mocked.discoverBidsEeg }));
vi.mock("./runs", () => ({ openRun: mocked.openRun }));

const workflow: Workflow = {
  schema_version: "1.0",
  id: "workflow-test",
  metadata: {
    name: "Cohort test",
    description: "UI lifecycle fixture",
    created_with: "test",
    modality: "EEG",
    status: "experimental",
  },
  nodes: [
    {
      id: "input",
      type: "input.bids_eeg",
      label: "BIDS EEG",
      category: "Input",
      position: { x: 0, y: 0 },
      ports: [],
      parameters: [
        {
          id: "root",
          label: "Dataset root",
          value: "datasets/openneuro/ds005284",
          required: true,
        },
      ],
      pauses_for_review: false,
    },
  ],
  edges: [],
};

function cohortRecord(
  id: string,
  state: "running" | "succeeded",
): CohortRunRecord {
  const active = state === "running";
  return {
    schema_version: "1.2",
    id,
    state,
    dataset_path: "datasets/openneuro/ds005284",
    dataset_id: "ds005284",
    dataset_version: "1.0.0",
    workflow_id: workflow.id,
    workflow_identity: `brainlearn-v1:workflow:${"a".repeat(64)}`,
    workflow,
    created_at: "2026-11-14T10:00:00Z",
    started_at: "2026-11-14T10:00:00Z",
    finished_at: active ? null : "2026-11-14T10:01:00Z",
    seed: 0,
    parameters: {},
    recordings: [
      {
        recording_path: "sub-001/eeg/sub-001_task-test_eeg.bdf",
        participant: "sub-001",
        session: null,
        run: null,
        state: active ? "selected" : "processed",
        input_identity: null,
        input_files: [],
        child_run_id: null,
        parameters: {},
        qc_review_ids: [],
        outputs: [],
        failure_reason: null,
      },
    ],
    selected_count: 1,
    processed_count: active ? 0 : 1,
    excluded_count: 0,
    failed_count: 0,
    cancelled_count: 0,
    processed_participant_count: active ? 0 : 1,
    report_path: active ? null : "cohorts/report.html",
    report_sha256: active ? null : "b".repeat(64),
    summary_path: active ? null : "cohorts/summary.json",
    summary_sha256: active ? null : "c".repeat(64),
    citations: [],
    failure_reason: null,
  };
}

beforeEach(() => {
  mocked.cancelCohort.mockReset();
  mocked.discoverBidsEeg.mockReset();
  mocked.downloadCohortResult.mockReset();
  mocked.openCohort.mockReset();
  mocked.openRun.mockReset();
  mocked.recoverCohorts.mockReset().mockResolvedValue([]);
  mocked.startCohort.mockReset();
});

afterEach(() => cleanup());

test("blocks a second cohort while the current project has an active run", async () => {
  const active = cohortRecord("cohort-running", "running");
  mocked.discoverBidsEeg.mockResolvedValue({
    schema_version: "1.0",
    dataset_path: "datasets/openneuro/ds005284",
    status: "ready",
    dataset_name: "ds005284",
    bids_version: "1.8.0",
    inspection_scope: "metadata_only",
    issues: [],
    recordings: [
      {
        path: active.recordings[0].recording_path,
        subject: "001",
        session: null,
        task: "test",
        format: "BDF",
        recording_type: "continuous",
        status: "ready",
        sampling_frequency_hz: 1024,
        eeg_reference: null,
        eeg_channel_count: 64,
        channel_names: [],
        event_count: 16,
        event_types: ["condition 54"],
        events_timing_sha256: "d".repeat(64),
        issues: [],
      },
    ],
  });
  mocked.startCohort.mockResolvedValue(active);
  mocked.openCohort.mockResolvedValue(active);

  render(
    <CohortLauncher
      onChildRun={vi.fn()}
      projectPath="/project-one"
      token="session-token"
      workflow={workflow}
    />,
  );

  const scan = await screen.findByRole("button", {
    name: "Scan cohort recordings",
  });
  await waitFor(() => expect(scan).toBeEnabled());
  fireEvent.click(scan);
  const launch = await screen.findByRole("button", {
    name: "Run 1 recording(s)",
  });
  fireEvent.click(launch);

  expect(
    await screen.findByText("cohort-running · running"),
  ).toBeInTheDocument();
  expect(scan).toBeDisabled();
  expect(launch).toBeDisabled();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  expect(mocked.startCohort).toHaveBeenCalledTimes(1);
});

test("clears a previous project's cohort when switching projects", async () => {
  const completed = cohortRecord("cohort-completed", "succeeded");
  mocked.discoverBidsEeg.mockResolvedValue({
    schema_version: "1.0",
    dataset_path: "datasets/openneuro/ds005284",
    status: "ready",
    dataset_name: "ds005284",
    bids_version: "1.8.0",
    inspection_scope: "metadata_only",
    issues: [],
    recordings: [
      {
        path: completed.recordings[0].recording_path,
        subject: "001",
        session: null,
        task: "test",
        format: "BDF",
        recording_type: "continuous",
        status: "ready",
        sampling_frequency_hz: 1024,
        eeg_reference: null,
        eeg_channel_count: 64,
        channel_names: [],
        event_count: 16,
        event_types: ["condition 54"],
        events_timing_sha256: "d".repeat(64),
        issues: [],
      },
    ],
  });
  mocked.startCohort.mockResolvedValue(completed);

  const props = {
    onChildRun: vi.fn(),
    token: "session-token",
    workflow,
  };
  const { rerender } = render(
    <CohortLauncher {...props} projectPath="/project-one" />,
  );
  const scan = await screen.findByRole("button", {
    name: "Scan cohort recordings",
  });
  await waitFor(() => expect(scan).toBeEnabled());
  fireEvent.click(scan);
  fireEvent.click(
    await screen.findByRole("button", { name: "Run 1 recording(s)" }),
  );
  expect(
    await screen.findByText("cohort-completed · succeeded"),
  ).toBeInTheDocument();

  rerender(<CohortLauncher {...props} projectPath="/project-two" />);
  await waitFor(() =>
    expect(
      screen.queryByText("cohort-completed · succeeded"),
    ).not.toBeInTheDocument(),
  );
  expect(mocked.recoverCohorts).toHaveBeenCalledWith(
    "/project-two",
    "session-token",
  );
  expect(
    screen.queryByRole("button", { name: "Download HTML report" }),
  ).not.toBeInTheDocument();
});

test("discards a dataset scan that finishes after switching projects", async () => {
  let resolveDiscovery!: (value: BidsEegDiscovery) => void;
  mocked.discoverBidsEeg.mockReturnValue(
    new Promise<BidsEegDiscovery>((resolve) => {
      resolveDiscovery = resolve;
    }),
  );
  const props = {
    onChildRun: vi.fn(),
    token: "session-token",
    workflow,
  };
  const { rerender } = render(
    <CohortLauncher {...props} projectPath="/project-one" />,
  );
  const scan = await screen.findByRole("button", {
    name: "Scan cohort recordings",
  });
  await waitFor(() => expect(scan).toBeEnabled());
  fireEvent.click(scan);
  expect(scan).toBeDisabled();

  rerender(<CohortLauncher {...props} projectPath="/project-two" />);
  await waitFor(() => expect(scan).toBeEnabled());
  resolveDiscovery({
    schema_version: "1.0",
    dataset_path: "datasets/from-project-one",
    status: "ready",
    dataset_name: "stale-project-one-results",
    bids_version: "1.8.0",
    inspection_scope: "metadata_only",
    issues: [],
    recordings: [],
  });

  await waitFor(() => {
    expect(
      screen.queryByText(/stale-project-one-results/),
    ).not.toBeInTheDocument();
  });
  expect(mocked.recoverCohorts).toHaveBeenCalledWith(
    "/project-two",
    "session-token",
  );
});
