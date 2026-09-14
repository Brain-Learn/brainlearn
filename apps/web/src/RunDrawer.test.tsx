import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { RunDrawer } from "./RunDrawer";
import type { RunRecord } from "./types";

function runFixture(overrides: Partial<RunRecord> = {}): RunRecord {
  return {
    schema_version: "1.0",
    id: "run-demo-1",
    workflow_id: "demo-branched",
    workflow_schema_version: "1.0",
    workflow_identity: "brainlearn-v1:workflow:" + "a".repeat(64),
    state: "running",
    created_at: "2026-09-13T00:00:00+00:00",
    started_at: "2026-09-13T00:00:01+00:00",
    finished_at: null,
    environment: {
      schema_version: "1.0",
      operating_system: "TestOS",
      architecture: "arm64",
      python_version: "3.12",
      packages: {},
      accelerator: "cpu",
    },
    seed: 0,
    node_runs: [],
    events: [],
    failure: null,
    ...overrides,
  };
}

function renderDrawer(
  run: RunRecord | null,
  overrides: Partial<Parameters<typeof RunDrawer>[0]> = {},
) {
  const props = {
    run,
    runPath: "/tmp/project",
    launching: false,
    streamStatus: "idle" as const,
    streamNotice: null,
    message: null,
    reducedMotion: false,
    projectOpen: true,
    history: [],
    historyError: null,
    historyPending: false,
    onCancel: vi.fn(),
    onReview: vi.fn(),
    onRecover: vi.fn(),
    onOpenRun: vi.fn(),
    onOpenArtifact: vi.fn(),
    actionPending: false,
    ...overrides,
  };
  render(<RunDrawer {...props} />);
  return props;
}

afterEach(() => {
  cleanup();
});

test("renders idle state without a run", () => {
  renderDrawer(null);
  expect(screen.getByText("No active run")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Cancel/ }),
  ).not.toBeInTheDocument();
});

test("shows aggregate state, node attempts, and cache reuse", () => {
  const run = runFixture({
    state: "succeeded",
    node_runs: [
      {
        schema_version: "1.0",
        id: "source",
        node_id: "source",
        node_type: "demo.copy",
        node_version: "0.1.0",
        dependencies: [],
        attempt: 1,
        state: "succeeded",
        started_at: "2026-09-13T00:00:01+00:00",
        finished_at: "2026-09-13T00:00:02+00:00",
        inputs: {},
        parameters: { text: "x" },
        environment_identity: "brainlearn-v1:environment:" + "b".repeat(64),
        seed: 0,
        settings: {},
        content_identity: "brainlearn-v1:node:" + "c".repeat(64),
        artifacts: [],
        failure: null,
        review_pause: null,
      },
      {
        schema_version: "1.0",
        id: "bridge",
        node_id: "bridge",
        node_type: "demo.relay",
        node_version: "0.1.0",
        dependencies: ["source"],
        attempt: 0,
        state: "cache_reused",
        started_at: null,
        finished_at: null,
        inputs: { in: "brainlearn-v1:node:" + "c".repeat(64) },
        parameters: {},
        environment_identity: "brainlearn-v1:environment:" + "b".repeat(64),
        seed: 0,
        settings: {},
        content_identity: "brainlearn-v1:node:" + "d".repeat(64),
        artifacts: [],
        failure: null,
        review_pause: null,
      },
    ],
  });
  renderDrawer(run);
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
  expect(screen.getByText("attempt 1")).toBeInTheDocument();
  expect(screen.getByText("cache reuse")).toBeInTheDocument();
});

test("shows actionable failure and ordered events", () => {
  const run = runFixture({
    state: "failed",
    failure: {
      schema_version: "1.0",
      code: "node_failed",
      message: "demo.fail raised demonstration failure",
      node_run_id: "boom",
      at: "2026-09-13T00:00:03+00:00",
    },
    events: [
      {
        schema_version: "1.0",
        seq: 0,
        at: "2026-09-13T00:00:00+00:00",
        kind: "run_queued",
        node_run_id: null,
        attempt: null,
        message: "Run created and queued.",
      },
      {
        schema_version: "1.0",
        seq: 1,
        at: "2026-09-13T00:00:03+00:00",
        kind: "run_failed",
        node_run_id: null,
        attempt: null,
        message: "Run failed.",
      },
    ],
  });
  renderDrawer(run);
  expect(screen.getByText(/node_failed: demo.fail raised/)).toBeInTheDocument();
  expect(screen.getAllByText("run_queued").length).toBeGreaterThan(0);
  expect(screen.getAllByText("run_failed").length).toBeGreaterThan(0);
});

test("renders artifact metadata without filesystem actions", () => {
  const run = runFixture({
    node_runs: [
      {
        schema_version: "1.0",
        id: "source",
        node_id: "source",
        node_type: "demo.copy",
        node_version: "0.1.0",
        dependencies: [],
        attempt: 1,
        state: "succeeded",
        started_at: "2026-09-13T00:00:01+00:00",
        finished_at: "2026-09-13T00:00:02+00:00",
        inputs: {},
        parameters: {},
        environment_identity: "brainlearn-v1:environment:" + "b".repeat(64),
        seed: 0,
        settings: {},
        content_identity: "brainlearn-v1:node:" + "c".repeat(64),
        artifacts: [
          {
            schema_version: "1.0",
            artifact_id: "brainlearn-v1:artifact:" + "e".repeat(64),
            path: "runs/run-demo-1/artifacts/source/output.txt",
            media_type: "text/plain",
            byte_size: 11,
            sha256: "f".repeat(64),
            produced_by_node: "source",
            port_id: "output",
          },
        ],
        failure: null,
        review_pause: null,
      },
    ],
  });
  const props = renderDrawer(run);
  expect(
    screen.getByText("runs/run-demo-1/artifacts/source/output.txt"),
  ).toBeInTheDocument();
  expect(screen.getByText("11 B")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(props.onOpenArtifact).toHaveBeenCalledTimes(1);
  expect(vi.mocked(props.onOpenArtifact).mock.calls[0][0].artifact_id).toBe(
    "brainlearn-v1:artifact:" + "e".repeat(64),
  );
});

test("shows approve/reject controls for a waiting review node", () => {
  const run = runFixture({
    state: "waiting_for_review",
    node_runs: [
      {
        schema_version: "1.0",
        id: "gate",
        node_id: "gate",
        node_type: "demo.review",
        node_version: "0.1.0",
        dependencies: [],
        attempt: 1,
        state: "waiting_for_review",
        started_at: "2026-09-13T00:00:01+00:00",
        finished_at: null,
        inputs: {},
        parameters: {},
        environment_identity: "brainlearn-v1:environment:" + "b".repeat(64),
        seed: 0,
        settings: {},
        content_identity: "brainlearn-v1:node:" + "c".repeat(64),
        artifacts: [],
        failure: null,
        review_pause: {
          schema_version: "1.0",
          id: "rev-1",
          node_run_id: "gate",
          input_identity: "brainlearn-v1:node:" + "c".repeat(64),
          requested_at: "2026-09-13T00:00:01+00:00",
          decided_at: null,
          decision: null,
          note: "",
        },
      },
    ],
  });
  const props = renderDrawer(run);
  const approve = screen.getByRole("button", { name: "Approve" });
  const reject = screen.getByRole("button", { name: "Reject" });
  fireEvent.click(approve);
  expect(props.onReview).toHaveBeenCalledWith("gate", "approved", "");
  fireEvent.click(reject);
  expect(props.onReview).toHaveBeenCalledWith("gate", "rejected", "");
});

test("offers cancel only for non-terminal runs and honors reduced motion", () => {
  const props = renderDrawer(runFixture({ state: "running" }));
  expect(screen.getByRole("button", { name: /Cancel/ })).toBeInTheDocument();

  cleanup();
  renderDrawer(runFixture({ state: "cancelled" }));
  expect(
    screen.queryByRole("button", { name: /Cancel/ }),
  ).not.toBeInTheDocument();
  expect(props.onCancel).not.toHaveBeenCalled();

  cleanup();
  render(
    <RunDrawer
      actionPending={false}
      history={[]}
      historyError={null}
      historyPending={false}
      launching={false}
      message={null}
      onCancel={() => {}}
      onOpenArtifact={() => {}}
      onOpenRun={() => {}}
      onRecover={() => {}}
      onReview={() => {}}
      projectOpen
      reducedMotion
      run={runFixture({ state: "running" })}
      runPath="/tmp/project"
      streamNotice={null}
      streamStatus="live"
    />,
  );
  expect(screen.getByText("Running")).toBeInTheDocument();
  expect(document.querySelector(".run-drawer")).toHaveAttribute(
    "data-reduced-motion",
    "true",
  );
});

function historyEntry(
  runId: string,
  state: "succeeded" | "waiting_for_review",
): { path: string; run_id: string; run: RunRecord } {
  return {
    path: "/tmp/project",
    run_id: runId,
    run: runFixture({ id: runId, state }),
  };
}

test("renders stream health and reconnect notice accessibly", () => {
  const props = renderDrawer(runFixture({ state: "running" }), {
    streamStatus: "reconnecting",
    streamNotice: "Event updates paused (boom). Retrying…",
  });
  expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
  expect(screen.getByText(/Event updates paused/)).toBeInTheDocument();
  expect(props.streamStatus).toBe("reconnecting");

  cleanup();
  renderDrawer(runFixture({ state: "running" }), { streamStatus: "live" });
  expect(screen.getByText("Live")).toBeInTheDocument();
  expect(screen.queryByText(/Event updates paused/)).not.toBeInTheDocument();
});

test("lists run history and opens a selected record", () => {
  const props = renderDrawer(null, {
    history: [historyEntry("run-old-1", "succeeded")],
  });
  expect(screen.getByText("run-old-1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open run run-old-1" }));
  expect(props.onOpenRun).toHaveBeenCalledWith("run-old-1", "/tmp/project");
});

test("exposes recovery with no active run when a project is open", () => {
  const props = renderDrawer(null);
  const recover = screen.getByRole("button", { name: /Recover/ });
  expect(recover).toBeInTheDocument();
  fireEvent.click(recover);
  expect(props.onRecover).toHaveBeenCalledTimes(1);
});

test("hides recovery and history without an open project", () => {
  renderDrawer(null, { projectOpen: false });
  expect(
    screen.queryByRole("button", { name: /Recover/ }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("Runs")).not.toBeInTheDocument();
});
