import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { BidsEegDiscovery } from "./BidsEegDiscovery";
import { discoverBidsEeg } from "./datasets";
import type { BidsEegDiscovery as BidsEegDiscoveryResult } from "./types";

vi.mock("./datasets", () => ({ discoverBidsEeg: vi.fn() }));

const result: BidsEegDiscoveryResult = {
  schema_version: "1.0",
  dataset_path: "datasets/OpenNeuro/ds002181/1.0.0",
  status: "ready",
  dataset_name: "Example EEG",
  bids_version: "1.2.0",
  inspection_scope: "metadata_only",
  recordings: [
    {
      path: "sub-01/eeg/sub-01_task-Rest_eeg.edf",
      subject: "01",
      session: null,
      task: "Rest",
      format: "edf",
      status: "ready",
      sampling_frequency_hz: 500,
      eeg_reference: "average",
      eeg_channel_count: 2,
      channel_names: ["Cz", "Pz"],
      event_count: 2,
      event_types: ["deviant", "standard"],
      issues: [],
    },
  ],
  issues: [],
};

const discoverMock = vi.mocked(discoverBidsEeg);

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

test("discovers and summarizes BIDS EEG metadata after an explicit action", async () => {
  discoverMock.mockResolvedValue(result);
  render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="datasets/OpenNeuro/ds002181/1.0.0"
      token="session-token"
    />,
  );

  fireEvent.click(
    screen.getByRole("button", { name: "Check BIDS EEG metadata" }),
  );

  expect(
    await screen.findByText(/BIDS EEG metadata is ready/),
  ).toBeInTheDocument();
  expect(
    screen.getByText("sub-01/eeg/sub-01_task-Rest_eeg.edf"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/500 Hz · 2 EEG channels · 2 events/),
  ).toBeInTheDocument();
  expect(screen.getByText(/not opened/)).toBeInTheDocument();
  expect(discoverMock).toHaveBeenCalledWith(
    "/tmp/brainlearn-project",
    "datasets/OpenNeuro/ds002181/1.0.0",
    expect.objectContaining({ token: "session-token" }),
  );
});

test("reports incomplete and unsupported metadata states with clear issues", async () => {
  discoverMock.mockResolvedValue({
    ...result,
    status: "incomplete_metadata",
    recordings: [
      {
        ...result.recordings[0],
        status: "incomplete_metadata",
        sampling_frequency_hz: null,
        issues: [
          {
            code: "invalid_sampling_frequency",
            message:
              "EEG metadata must provide a positive numeric SamplingFrequency.",
            path: result.recordings[0].path,
          },
        ],
      },
    ],
  });
  const { rerender } = render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="raw-data/study"
      token="session-token"
    />,
  );
  fireEvent.click(screen.getByTestId("bids-eeg-discover"));
  expect(
    await screen.findByText(/required metadata is incomplete/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/positive numeric SamplingFrequency/),
  ).toBeInTheDocument();

  discoverMock.mockResolvedValue({ ...result, status: "unsupported" });
  rerender(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="raw-data/other"
      token="session-token"
    />,
  );
  fireEvent.click(await screen.findByTestId("bids-eeg-discover"));
  expect(
    await screen.findByText(/unsupported BIDS version, type, or EEG format/),
  ).toBeInTheDocument();
});

test("keeps scan failures visible and leaves the action retryable", async () => {
  discoverMock.mockRejectedValueOnce(
    new Error("Dataset directory was not found."),
  );
  render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="raw-data/missing"
      token="session-token"
    />,
  );
  fireEvent.click(screen.getByTestId("bids-eeg-discover"));
  expect(
    await screen.findByText("Dataset directory was not found."),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByTestId("bids-eeg-discover")).toBeEnabled(),
  );
});
