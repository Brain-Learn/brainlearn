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
import {
  discoverBidsEeg,
  identifyBidsEegRecording,
  inspectBidsEegSignal,
  previewBidsEegSignal,
  listResearcherDecisions,
  createResearcherDecision,
} from "./datasets";
import type {
  BidsEegDiscovery as BidsEegDiscoveryResult,
  BidsEegInputIdentity,
  BidsEegSignalInspection,
  BidsEegSignalPreview,
} from "./types";

vi.mock("./datasets", () => ({
  discoverBidsEeg: vi.fn(),
  identifyBidsEegRecording: vi.fn(),
  inspectBidsEegSignal: vi.fn(),
  previewBidsEegSignal: vi.fn(),
  listResearcherDecisions: vi.fn(),
  createResearcherDecision: vi.fn(),
}));

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
const identifyMock = vi.mocked(identifyBidsEegRecording);
const inspectMock = vi.mocked(inspectBidsEegSignal);
const previewMock = vi.mocked(previewBidsEegSignal);
const decisionsMock = vi.mocked(listResearcherDecisions);
const createDecisionMock = vi.mocked(createResearcherDecision);

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
  expect(screen.getByText(/bounded chunks/)).toBeInTheDocument();
  expect(discoverMock).toHaveBeenCalledWith(
    "/tmp/brainlearn-project",
    "datasets/OpenNeuro/ds002181/1.0.0",
    expect.objectContaining({ token: "session-token" }),
  );
});

test("creates and displays an input identity only after the explicit hash action", async () => {
  discoverMock.mockResolvedValue(result);
  const identity: BidsEegInputIdentity = {
    schema_version: "1.0",
    recording_path: result.recordings[0].path,
    status: "ready",
    content_identity: `brainlearn-v1:artifact:${"0".repeat(64)}`,
    files: [
      {
        path: result.recordings[0].path,
        byte_size: 123,
        sha256: "a".repeat(64),
      },
    ],
    message: null,
    inspection_scope: "bounded_source_hashes",
  };
  identifyMock.mockResolvedValue(identity);
  render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="datasets/OpenNeuro/ds002181/1.0.0"
      token="session-token"
    />,
  );
  fireEvent.click(screen.getByTestId("bids-eeg-discover"));
  await screen.findByText(/BIDS EEG metadata is ready/);
  expect(identifyMock).not.toHaveBeenCalled();

  fireEvent.click(
    screen.getByRole("button", { name: "Create input identity" }),
  );
  expect(
    await screen.findByText(/brainlearn-v1:artifact:00000000/),
  ).toBeInTheDocument();
  expect(screen.getByText(/123 bytes · SHA-256/)).toBeInTheDocument();
  expect(identifyMock).toHaveBeenCalledWith(
    "/tmp/brainlearn-project",
    "datasets/OpenNeuro/ds002181/1.0.0",
    result.recordings[0].path,
    { token: "session-token" },
  );
});

test("persists a researcher decision only after an explicit choice and submission", async () => {
  discoverMock.mockResolvedValue(result);
  identifyMock.mockResolvedValue({
    schema_version: "1.0",
    recording_path: result.recordings[0].path,
    status: "ready",
    content_identity: `brainlearn-v1:artifact:${"0".repeat(64)}`,
    files: [],
    message: null,
    inspection_scope: "bounded_source_hashes",
  });
  decisionsMock.mockResolvedValue([]);
  createDecisionMock.mockResolvedValue({
    schema_version: "1.0",
    id: "decision-1",
    researcher: "Dr. Example",
    dataset_path: "datasets/OpenNeuro/ds002181/1.0.0",
    recording_path: result.recordings[0].path,
    source_content_identity: `brainlearn-v1:artifact:${"0".repeat(64)}`,
    decision: "needs_review",
    note: "Check blink",
    time_start_seconds: 1,
    time_end_seconds: 2,
    channel_names: ["Cz"],
    created_at: "2026-10-04T10:00:00Z",
    updated_at: "2026-10-04T10:00:00Z",
  });
  render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="datasets/OpenNeuro/ds002181/1.0.0"
      token="session-token"
    />,
  );
  fireEvent.click(screen.getByTestId("bids-eeg-discover"));
  await screen.findByText(/BIDS EEG metadata is ready/);
  fireEvent.click(
    screen.getByRole("button", { name: "Create input identity" }),
  );
  await screen.findByRole("heading", {
    name: "Researcher annotation and inspection decision",
  });
  expect(createDecisionMock).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Researcher"), {
    target: { value: "Dr. Example" },
  });
  fireEvent.change(screen.getByLabelText("Decision"), {
    target: { value: "needs_review" },
  });
  fireEvent.change(screen.getByLabelText("Annotation note"), {
    target: { value: "Check blink" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save explicit decision" }),
  );
  await screen.findByText(/needs review · Dr. Example/);
  expect(createDecisionMock).toHaveBeenCalledWith(
    "/tmp/brainlearn-project",
    expect.objectContaining({
      researcher: "Dr. Example",
      decision: "needs_review",
      note: "Check blink",
      channel_names: [],
    }),
    "session-token",
  );
});

test("inspects a ready signal only after an explicit action and displays measurements", async () => {
  discoverMock.mockResolvedValue(result);
  const inspection: BidsEegSignalInspection = {
    schema_version: "1.0",
    recording_path: result.recordings[0].path,
    format: "edf",
    sampling_frequency_hz: 500,
    sample_count: 2_000,
    duration_seconds: 4,
    channel_count: 2,
    channel_types: { eeg: 2 },
    bad_channel_count: 0,
    annotation_count: 2,
    annotation_descriptions: ["standard", "deviant"],
    highpass_hz: 0,
    lowpass_hz: 250,
    inspection_scope: "read_only_signal_metadata",
  };
  inspectMock.mockResolvedValue(inspection);
  render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="datasets/OpenNeuro/ds002181/1.0.0"
      token="session-token"
    />,
  );
  fireEvent.click(screen.getByTestId("bids-eeg-discover"));
  await screen.findByText(/BIDS EEG metadata is ready/);
  expect(inspectMock).not.toHaveBeenCalled();

  fireEvent.click(
    screen.getByRole("button", { name: "Inspect signal with MNE" }),
  );
  expect(
    await screen.findByText(
      /MNE read-only inspection: 500 Hz · 2,000 samples · 4.00 s/,
    ),
  ).toBeInTheDocument();
  expect(inspectMock).toHaveBeenCalledWith(
    "/tmp/brainlearn-project",
    "datasets/OpenNeuro/ds002181/1.0.0",
    result.recordings[0].path,
    { token: "session-token" },
  );
});

test("creates a bounded read-only preview on request and supports window/channel updates", async () => {
  discoverMock.mockResolvedValue(result);
  inspectMock.mockResolvedValue({
    schema_version: "1.0",
    recording_path: result.recordings[0].path,
    format: "edf",
    sampling_frequency_hz: 500,
    sample_count: 2_000,
    duration_seconds: 4,
    channel_count: 2,
    channel_types: { eeg: 2 },
    bad_channel_count: 0,
    annotation_count: 1,
    annotation_descriptions: ["stimulus"],
    highpass_hz: 0,
    lowpass_hz: 250,
    inspection_scope: "read_only_signal_metadata",
  });
  const preview: BidsEegSignalPreview = {
    schema_version: "1.0",
    recording_path: result.recordings[0].path,
    source_content_identity: `brainlearn-v1:artifact:${"a".repeat(64)}`,
    time_start_seconds: 0,
    duration_seconds: 2,
    sampling_frequency_hz: 500,
    sample_count: 1_000,
    channels: [
      { name: "Cz", channel_type: "eeg", marked_bad: false },
      { name: "Pz", channel_type: "eeg", marked_bad: true },
    ],
    traces: [
      {
        channel_name: "Cz",
        unit: "µV",
        bins: [{ time_seconds: 0.1, minimum_uv: -4, maximum_uv: 5 }],
      },
    ],
    events: [
      { onset_seconds: 0.5, duration_seconds: 0.1, description: "stimulus" },
    ],
    event_count: 1,
    events_truncated: false,
    spectrum: {
      method: "welch",
      window: "hamming",
      n_fft: 1024,
      n_per_seg: 1000,
      n_overlap: 500,
      reject_by_annotation: false,
      frequencies_hz: [0, 0.5, 1],
      traces: [{ channel_name: "Cz", power_uv2_per_hz: [0.1, 1, 0.1] }],
      unit: "µV²/Hz",
    },
    preview_scope: "bounded_read_only_signal_preview",
  };
  previewMock.mockResolvedValue(preview);
  render(
    <BidsEegDiscovery
      projectPath="/tmp/brainlearn-project"
      relativeDir="datasets/OpenNeuro/ds002181/1.0.0"
      token="session-token"
    />,
  );
  fireEvent.click(screen.getByTestId("bids-eeg-discover"));
  await screen.findByText(/BIDS EEG metadata is ready/);
  expect(previewMock).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Inspect before preview" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Inspect signal with MNE" }),
  );
  await screen.findByText(/MNE read-only inspection/);
  fireEvent.click(screen.getByRole("button", { name: "Preview signal" }));
  expect(
    await screen.findByRole("img", { name: /Cz downsampled signal envelope/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/not a signal-quality assessment/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Annotations in selected window/),
  ).toBeInTheDocument();
  expect(previewMock).toHaveBeenNthCalledWith(
    1,
    "/tmp/brainlearn-project",
    "datasets/OpenNeuro/ds002181/1.0.0",
    result.recordings[0].path,
    { token: "session-token" },
  );
  fireEvent.click(screen.getByRole("button", { name: "Update preview" }));
  await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(2));
  expect(previewMock).toHaveBeenLastCalledWith(
    "/tmp/brainlearn-project",
    "datasets/OpenNeuro/ds002181/1.0.0",
    result.recordings[0].path,
    {
      token: "session-token",
      timeStartSeconds: 0,
      durationSeconds: 2,
      channelNames: ["Cz"],
    },
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
