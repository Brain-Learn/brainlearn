import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type {
  NodeManifest,
  NodeRunRecord,
  RunEvent,
  RunRecord,
  Workflow,
} from "./types";

const bidsManifest: NodeManifest = {
  manifest_schema_version: "1.0",
  id: "input.bids_eeg",
  node_version: "0.1.0",
  label: "BIDS EEG",
  description: "Choose a dataset.",
  category: "Input",
  status: "example",
  ports: [],
  parameters: [],
  review_behavior: "none",
  citations: [],
  license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
  capability_requirements: [],
};

const IDENTITY = (domain: string, ch: string) =>
  `brainlearn-v1:${domain}:${ch.repeat(64)}`;

function makeEvent(
  seq: number,
  kind: RunEvent["kind"],
  message: string,
  nodeRunId: string | null = null,
): RunEvent {
  return {
    schema_version: "1.0",
    seq,
    at: "2026-09-13T00:00:00+00:00",
    kind,
    node_run_id: nodeRunId,
    attempt: null,
    message,
  };
}

function makeNodeRun(
  id: string,
  overrides: Partial<NodeRunRecord> = {},
): NodeRunRecord {
  return {
    schema_version: "1.0",
    id,
    node_id: id,
    node_type: "demo.copy",
    node_version: "0.1.0",
    dependencies: [],
    attempt: 0,
    state: "queued",
    started_at: null,
    finished_at: null,
    inputs: {},
    parameters: {},
    environment_identity: IDENTITY("environment", "b"),
    seed: 0,
    settings: {},
    content_identity: IDENTITY("node", "c"),
    artifacts: [],
    failure: null,
    review_pause: null,
    ...overrides,
  };
}

function makeRun(
  id: string,
  state: RunRecord["state"],
  events: RunEvent[],
  nodeRuns: NodeRunRecord[] = [],
): RunRecord {
  const terminal = ["succeeded", "failed", "cancelled"].includes(state);
  return {
    schema_version: "1.0",
    id,
    workflow_id: "demo",
    workflow_schema_version: "1.0",
    workflow_identity: IDENTITY("workflow", "a"),
    state,
    created_at: "2026-09-13T00:00:00+00:00",
    started_at: "2026-09-13T00:00:01+00:00",
    finished_at: terminal ? "2026-09-13T00:00:02+00:00" : null,
    environment: {
      schema_version: "1.0",
      operating_system: "TestOS",
      architecture: "arm64",
      python_version: "3.12",
      packages: {},
      accelerator: "cpu",
    },
    seed: 0,
    node_runs: nodeRuns,
    events,
    failure: null,
  };
}

function emptyWorkflow(): Workflow {
  return {
    schema_version: "1.0",
    id: "demo",
    metadata: {
      name: "Demo",
      description: "",
      created_with: "BrainLearn",
      modality: "EEG",
      status: "example",
    },
    nodes: [],
    edges: [],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function streamBody(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
}

function eventFrame(event: RunEvent): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  sessionStorage.setItem("brainlearn.sessionToken", "test-token");
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubBaseFetch(overrides: {
  onStart?: (body: Record<string, unknown>) => Promise<unknown>;
  onOpen?: (body: Record<string, unknown>) => Promise<unknown>;
  onEvents?: (url: string, init?: RequestInit) => Promise<unknown>;
  onList?: () => Promise<unknown>;
}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/examples")) {
        return { ok: true, json: async () => ({ examples: [] }) };
      }
      if (url.endsWith("/api/workflows/validate") && init?.body) {
        const workflow = JSON.parse(String(init.body)) as Workflow;
        return {
          ok: true,
          json: async () => ({
            workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/projects/open")) {
        const body = JSON.parse(String(init?.body)) as { path: string };
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: "Run Project" },
            workflow: emptyWorkflow(),
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/projects/create")) {
        const body = JSON.parse(String(init?.body)) as { path: string };
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: "Run Project" },
            workflow: emptyWorkflow(),
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/runs/start") && init?.body) {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        if (overrides.onStart) return overrides.onStart(body);
        throw new Error(`Unexpected start call: ${url}`);
      }
      if (url.endsWith("/api/runs/open") && init?.body) {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        if (overrides.onOpen) return overrides.onOpen(body);
        throw new Error(`Unexpected open call: ${url}`);
      }
      if (url.includes("/api/runs/events")) {
        if (overrides.onEvents) return overrides.onEvents(url, init);
        throw new Error(`Unexpected events call: ${url}`);
      }
      if (url.includes("/api/runs/list")) {
        if (overrides.onList) return overrides.onList();
        throw new Error(`Unexpected list call: ${url}`);
      }
      if (
        url.endsWith("/api/runs/cancel") ||
        url.endsWith("/api/runs/review")
      ) {
        throw new Error(`Unexpected action call: ${url}`);
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  return calls;
}

async function openTestProject() {
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/run-project" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(
      screen.getByText("Active project: /tmp/run-project"),
    ).toBeInTheDocument(),
  );
}

test("a second run subscribes from its own cursor and renders early events", async () => {
  const firstEvents = Array.from({ length: 14 }, (_, seq) =>
    makeEvent(seq, "node_succeeded", `first event ${seq}`),
  );
  const run1 = makeRun("run-first", "succeeded", firstEvents, []);
  const run2 = makeRun(
    "run-second",
    "queued",
    [
      makeEvent(0, "run_queued", "Run created and queued."),
      makeEvent(1, "cache_reused", "Reused content second-run."),
    ],
    [],
  );
  const eventUrls: string[] = [];
  let startCount = 0;
  stubBaseFetch({
    onStart: async (body) => {
      startCount += 1;
      const run = startCount === 1 ? run1 : run2;
      return {
        ok: true,
        json: async () => ({ path: body.path, run_id: run.id, run }),
      };
    },
    onOpen: async () => ({
      ok: true,
      json: async () => ({
        path: "/tmp/run-project",
        run_id: "run-second",
        run: run2,
      }),
    }),
    onEvents: async (url) => {
      eventUrls.push(url);
      return { ok: true, body: streamBody([]) };
    },
  });

  await openTestProject();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() => expect(screen.getAllByText("run-first")).toHaveLength(2));

  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() =>
    expect(screen.getAllByText("run-second")).toHaveLength(2),
  );
  expect(screen.getByText("Reused content second-run.")).toBeInTheDocument();

  await waitFor(() => {
    const secondStream = eventUrls.find((url) => url.includes("run-second"));
    expect(secondStream).toBeDefined();
    expect(secondStream).toContain("after=1");
    expect(secondStream).not.toContain("after=13");
  });
});

test("reload exposes history and reopens a pending run", async () => {
  const waiting = makeRun(
    "run-waiting",
    "waiting_for_review",
    [
      makeEvent(0, "run_queued", "queued"),
      makeEvent(1, "review_requested", "review"),
    ],
    [
      makeNodeRun("gate", {
        attempt: 1,
        state: "waiting_for_review",
        started_at: "2026-09-13T00:00:01+00:00",
        review_pause: {
          schema_version: "1.0",
          id: "rev-1",
          node_run_id: "gate",
          input_identity: IDENTITY("node", "c"),
          requested_at: "2026-09-13T00:00:01+00:00",
          decided_at: null,
          decision: null,
          note: "",
        },
      }),
    ],
  );
  const done = makeRun(
    "run-done",
    "succeeded",
    [makeEvent(0, "run_queued", "queued")],
    [],
  );
  localStorage.setItem(
    "brainlearn.unsaved-workflow.v1",
    JSON.stringify({
      savedAt: "2026-09-13T00:00:00.000Z",
      workflow: emptyWorkflow(),
      projectPath: "/tmp/run-project",
      projectName: "Run Project",
    }),
  );
  stubBaseFetch({
    onList: async () => ({
      ok: true,
      json: async () => [
        { path: "/tmp/run-project", run_id: "run-waiting", run: waiting },
        { path: "/tmp/run-project", run_id: "run-done", run: done },
      ],
    }),
    onOpen: async (body) => {
      const run = body.run_id === "run-waiting" ? waiting : done;
      return {
        ok: true,
        json: async () => ({
          path: "/tmp/run-project",
          run_id: body.run_id,
          run,
        }),
      };
    },
    onEvents: async () => ({ ok: true, body: streamBody([]) }),
  });

  render(<App />);
  await waitFor(() =>
    expect(screen.getByText("run-waiting")).toBeInTheDocument(),
  );
  expect(screen.getByText("run-done")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Recover/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Open run run-waiting" }));
  await waitFor(() =>
    expect(screen.getByText("Awaiting review")).toBeInTheDocument(),
  );
  expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
});

test("reload reopens terminal history without run controls", async () => {
  const done = makeRun(
    "run-done",
    "succeeded",
    [makeEvent(0, "run_queued", "queued")],
    [],
  );
  localStorage.setItem(
    "brainlearn.unsaved-workflow.v1",
    JSON.stringify({
      savedAt: "2026-09-13T00:00:00.000Z",
      workflow: emptyWorkflow(),
      projectPath: "/tmp/run-project",
      projectName: "Run Project",
    }),
  );
  stubBaseFetch({
    onList: async () => ({
      ok: true,
      json: async () => [
        { path: "/tmp/run-project", run_id: "run-done", run: done },
      ],
    }),
    onOpen: async () => ({
      ok: true,
      json: async () => ({
        path: "/tmp/run-project",
        run_id: "run-done",
        run: done,
      }),
    }),
    onEvents: async () => ({ ok: true, body: streamBody([]) }),
  });

  render(<App />);
  await waitFor(() => expect(screen.getByText("run-done")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Open run run-done" }));
  await waitFor(() =>
    expect(screen.getByText("Succeeded")).toBeInTheDocument(),
  );
  expect(
    screen.queryByRole("button", { name: /Cancel/ }),
  ).not.toBeInTheDocument();
});

test("a stale snapshot cannot overwrite a review response", async () => {
  const waiting = makeRun(
    "run-review",
    "waiting_for_review",
    [makeEvent(0, "run_queued", "queued")],
    [
      makeNodeRun("gate", {
        attempt: 1,
        state: "waiting_for_review",
        started_at: "2026-09-13T00:00:01+00:00",
        review_pause: {
          schema_version: "1.0",
          id: "rev-1",
          node_run_id: "gate",
          input_identity: IDENTITY("node", "c"),
          requested_at: "2026-09-13T00:00:01+00:00",
          decided_at: null,
          decision: null,
          note: "",
        },
      }),
    ],
  );
  const decided = makeRun(
    "run-review",
    "running",
    [
      makeEvent(0, "run_queued", "queued"),
      makeEvent(1, "review_decided", "Review approved for gate."),
    ],
    [],
  );
  const openDeferreds: Array<ReturnType<typeof deferred<unknown>>> = [];
  const reviewDeferred = deferred<unknown>();
  let reviewCalls = 0;
  stubBaseFetch({
    onStart: async (body) => ({
      ok: true,
      json: async () => ({
        path: body.path,
        run_id: "run-review",
        run: waiting,
      }),
    }),
    onOpen: async () => {
      const gate = deferred<unknown>();
      openDeferreds.push(gate);
      return gate.promise;
    },
    onEvents: async () => ({
      ok: true,
      body: streamBody([
        eventFrame(makeEvent(1, "review_requested", "Gate awaits review.")),
      ]),
    }),
  });
  // Intercept review calls by re-stubbing cancel/review through the same mock.
  const fetchMock = vi.mocked(fetch);
  const originalImpl = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(
    async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runs/review")) {
        reviewCalls += 1;
        return reviewDeferred.promise as unknown as Response;
      }
      return originalImpl!(input, init);
    },
  );

  await openTestProject();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() =>
    expect(screen.getAllByText("run-review")).toHaveLength(2),
  );
  await waitFor(() => expect(openDeferreds.length).toBeGreaterThan(0));

  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(reviewCalls).toBe(1));
  reviewDeferred.resolve({
    ok: true,
    json: async () => ({
      path: "/tmp/run-project",
      run_id: "run-review",
      run: decided,
    }),
  });
  await waitFor(() =>
    expect(screen.getAllByText("Review approved for gate.")).toHaveLength(2),
  );
  for (const gate of openDeferreds.splice(0)) {
    gate.resolve({
      ok: true,
      json: async () => ({
        path: "/tmp/run-project",
        run_id: "run-review",
        run: waiting,
      }),
    });
  }
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(screen.getAllByText("Review approved for gate.")).toHaveLength(2);
  expect(screen.queryByText("Gate awaits review.")).not.toBeInTheDocument();
});

test("a stale snapshot cannot overwrite a cancellation", async () => {
  const running = makeRun(
    "run-cancel",
    "running",
    [makeEvent(0, "run_queued", "queued")],
    [],
  );
  const cancelled = makeRun(
    "run-cancel",
    "cancelled",
    [
      makeEvent(0, "run_queued", "queued"),
      makeEvent(1, "run_cancelled", "Run cancelled."),
    ],
    [],
  );
  const openDeferreds: Array<ReturnType<typeof deferred<unknown>>> = [];
  const cancelDeferred = deferred<unknown>();
  stubBaseFetch({
    onStart: async (body) => ({
      ok: true,
      json: async () => ({
        path: body.path,
        run_id: "run-cancel",
        run: running,
      }),
    }),
    onOpen: async () => {
      const gate = deferred<unknown>();
      openDeferreds.push(gate);
      return gate.promise;
    },
    onEvents: async () => ({
      ok: true,
      body: streamBody([
        eventFrame(makeEvent(1, "node_started", "Node started.")),
      ]),
    }),
  });
  const fetchMock = vi.mocked(fetch);
  const originalImpl = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(
    async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/runs/cancel")) {
        return cancelDeferred.promise as unknown as Response;
      }
      return originalImpl!(input, init);
    },
  );

  await openTestProject();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() =>
    expect(screen.getAllByText("run-cancel")).toHaveLength(2),
  );
  await waitFor(() => expect(openDeferreds.length).toBeGreaterThan(0));

  fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
  cancelDeferred.resolve({
    ok: true,
    json: async () => ({
      path: "/tmp/run-project",
      run_id: "run-cancel",
      run: cancelled,
    }),
  });
  await waitFor(() =>
    expect(screen.getAllByText("Run cancelled.")).toHaveLength(2),
  );
  for (const gate of openDeferreds.splice(0)) {
    gate.resolve({
      ok: true,
      json: async () => ({
        path: "/tmp/run-project",
        run_id: "run-cancel",
        run: running,
      }),
    });
  }
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(screen.getAllByText("Run cancelled.")).toHaveLength(2);
  expect(screen.queryByText("Node started.")).not.toBeInTheDocument();
});

test("a dropped stream reconnects and replays without duplicates", async () => {
  const running = makeRun(
    "run-stream",
    "running",
    [makeEvent(0, "run_queued", "queued")],
    [],
  );
  const progressed = makeRun(
    "run-stream",
    "running",
    [
      makeEvent(0, "run_queued", "queued"),
      makeEvent(1, "node_started", "Node started exactly once."),
    ],
    [],
  );
  let eventCalls = 0;
  const openGate = deferred<unknown>();
  stubBaseFetch({
    onStart: async (body) => ({
      ok: true,
      json: async () => ({
        path: body.path,
        run_id: "run-stream",
        run: running,
      }),
    }),
    onOpen: async () => openGate.promise,
    onEvents: async () => {
      eventCalls += 1;
      if (eventCalls === 1) throw new Error("connection dropped");
      return {
        ok: true,
        body: streamBody([
          eventFrame(
            makeEvent(1, "node_started", "Node started exactly once."),
          ),
        ]),
      };
    },
  });

  await openTestProject();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() =>
    expect(screen.getAllByText("run-stream")).toHaveLength(2),
  );
  await waitFor(() =>
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument(),
  );
  await waitFor(() => expect(screen.getByText("Live")).toBeInTheDocument());
  openGate.resolve({
    ok: true,
    json: async () => ({
      path: "/tmp/run-project",
      run_id: "run-stream",
      run: progressed,
    }),
  });
  await waitFor(() =>
    expect(screen.getByText("Node started exactly once.")).toBeInTheDocument(),
  );
  expect(screen.getAllByText("Node started exactly once.")).toHaveLength(1);
});

test("an approved artifact opens through a secure download", async () => {
  const artifactId = IDENTITY("artifact", "e");
  const done = makeRun(
    "run-artifact",
    "succeeded",
    [makeEvent(0, "run_queued", "queued")],
    [
      makeNodeRun("source", {
        attempt: 1,
        state: "succeeded",
        started_at: "2026-09-13T00:00:01+00:00",
        finished_at: "2026-09-13T00:00:02+00:00",
        artifacts: [
          {
            schema_version: "1.0",
            artifact_id: artifactId,
            path: "runs/run-artifact/artifacts/source/output.txt",
            media_type: "text/plain",
            byte_size: 11,
            sha256: "f".repeat(64),
            produced_by_node: "source",
            port_id: "output",
          },
        ],
      }),
    ],
  );
  const createObjectURL = vi
    .fn()
    .mockReturnValueOnce("blob:mock-first")
    .mockReturnValue("blob:mock-second");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchors.push({ href: this.href, download: this.download });
    });
  const clickedAnchors: Array<{ href: string; download: string }> = [];
  stubBaseFetch({
    onStart: async (body) => ({
      ok: true,
      json: async () => ({
        path: body.path,
        run_id: "run-artifact",
        run: done,
      }),
    }),
    onOpen: async () => ({
      ok: true,
      json: async () => ({
        path: "/tmp/run-project",
        run_id: "run-artifact",
        run: done,
      }),
    }),
    onEvents: async () => ({ ok: true, body: streamBody([]) }),
  });
  const fetchMock = vi.mocked(fetch);
  const originalImpl = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(
    async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/artifacts/open")) {
        return {
          ok: true,
          headers: new Headers({
            "Content-Type": "text/plain",
            "Content-Disposition": 'attachment; filename="output.txt"',
          }),
          blob: async () =>
            new Blob(["artifact-bytes"], { type: "text/plain" }),
        } as unknown as Response;
      }
      return originalImpl!(input, init);
    },
  );

  await openTestProject();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() =>
    expect(screen.getAllByText("run-artifact")).toHaveLength(2),
  );
  const artifactRow = screen
    .getByText("runs/run-artifact/artifacts/source/output.txt")
    .closest(".run-artifact-row");
  if (!artifactRow) throw new Error("artifact row missing");
  const openButton = within(artifactRow as HTMLElement).getByRole("button", {
    name: "Open",
  });
  fireEvent.click(openButton);
  await waitFor(() => expect(clickedAnchors).toHaveLength(1));
  expect(clickedAnchors[0]).toEqual({
    href: "blob:mock-first",
    download: "output.txt",
  });
  fireEvent.click(openButton);
  await waitFor(() => expect(clickedAnchors).toHaveLength(2));
  expect(clickedAnchors[1]).toEqual({
    href: "blob:mock-second",
    download: "output.txt",
  });
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-first");
  anchorClick.mockRestore();
});

test("a pending quiet stream cannot mutate state after a project switch", async () => {
  const runningA = makeRun(
    "run-a",
    "running",
    [makeEvent(0, "run_queued", "queued")],
    [],
  );
  const runningB = makeRun(
    "run-b",
    "running",
    [
      makeEvent(0, "run_queued", "queued"),
      makeEvent(1, "node_started", "B started."),
      makeEvent(2, "node_succeeded", "B progressed."),
    ],
    [],
  );
  const progressedB = makeRun(
    "run-b",
    "running",
    [
      makeEvent(0, "run_queued", "queued"),
      makeEvent(1, "node_started", "B started."),
      makeEvent(2, "node_succeeded", "B progressed."),
      makeEvent(3, "node_started", "B continues."),
    ],
    [],
  );
  let streamController: ReadableStreamDefaultController<Uint8Array> | null =
    null;
  const streamSignals: AbortSignal[] = [];
  let streamControllerB: ReadableStreamDefaultController<Uint8Array> | null =
    null;
  const openedRunIds: string[] = [];
  const eventUrls: string[] = [];
  let startCount = 0;
  stubBaseFetch({
    onStart: async (body) => {
      startCount += 1;
      const run = startCount === 1 ? runningA : runningB;
      return {
        ok: true,
        json: async () => ({ path: body.path, run_id: run.id, run }),
      };
    },
    onOpen: async (body) => {
      const parsed = body as { run_id?: string };
      if (parsed.run_id) openedRunIds.push(parsed.run_id);
      const run = parsed.run_id === "run-b" ? progressedB : runningA;
      return {
        ok: true,
        json: async () => ({ path: "/tmp/x", run_id: parsed.run_id, run }),
      };
    },
    onEvents: async (url, init) => {
      eventUrls.push(url);
      if (url.includes("run-b")) {
        return {
          ok: true,
          body: new ReadableStream<Uint8Array>({
            start(controller) {
              streamControllerB = controller;
            },
          }),
        };
      }
      if (init?.signal instanceof AbortSignal) streamSignals.push(init.signal);
      return {
        ok: true,
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller;
          },
        }),
      };
    },
    onList: async () => ({ ok: true, json: async () => [] }),
  });

  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/proj-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(screen.getByText("Active project: /tmp/proj-a")).toBeInTheDocument(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() => expect(screen.getAllByText("run-a")).toHaveLength(2));
  expect(streamSignals).toHaveLength(1);
  expect(streamSignals[0].aborted).toBe(false);

  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/proj-b" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(screen.getByText("Active project: /tmp/proj-b")).toBeInTheDocument(),
  );
  expect(streamSignals[0].aborted).toBe(true);
  expect(screen.getByText("No active run")).toBeInTheDocument();
  expect(screen.queryByText("run-a")).not.toBeInTheDocument();
  const opensBeforeLateFrames = openedRunIds.length;

  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() => expect(screen.getAllByText("run-b")).toHaveLength(2));
  expect(eventUrls[eventUrls.length - 1]).toContain("after=2");

  const encoder = new TextEncoder();
  await act(async () => {
    streamController?.enqueue(
      encoder.encode(eventFrame(makeEvent(5, "node_started", "stale A event"))),
    );
    streamController?.close();
    await new Promise((resolve) => setTimeout(resolve, 300));
  });
  expect(screen.queryByText("run-a")).not.toBeInTheDocument();
  expect(screen.queryByText("stale A event")).not.toBeInTheDocument();
  expect(screen.queryByText(/Event updates paused/)).not.toBeInTheDocument();
  expect(openedRunIds.length).toBe(opensBeforeLateFrames);

  await act(async () => {
    streamControllerB?.enqueue(
      encoder.encode(eventFrame(makeEvent(3, "node_started", "B continues."))),
    );
    streamControllerB?.close();
  });
  await waitFor(() =>
    expect(
      eventUrls.filter(
        (url) => url.includes("run-b") && url.includes("after=3"),
      ),
    ).not.toHaveLength(0),
  );
});

test("a blocked artifact download reports an explicit message", async () => {
  const artifactId = IDENTITY("artifact", "e");
  const done = makeRun(
    "run-blocked",
    "succeeded",
    [makeEvent(0, "run_queued", "queued")],
    [
      makeNodeRun("source", {
        attempt: 1,
        state: "succeeded",
        started_at: "2026-09-13T00:00:01+00:00",
        finished_at: "2026-09-13T00:00:02+00:00",
        artifacts: [
          {
            schema_version: "1.0",
            artifact_id: artifactId,
            path: "runs/run-blocked/artifacts/source/output.txt",
            media_type: "text/plain",
            byte_size: 11,
            sha256: "f".repeat(64),
            produced_by_node: "source",
            port_id: "output",
          },
        ],
      }),
    ],
  );
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:mock-blocked"),
    revokeObjectURL: vi.fn(),
  });
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => {
      throw new Error("popup blocked");
    });
  stubBaseFetch({
    onStart: async (body) => ({
      ok: true,
      json: async () => ({ path: body.path, run_id: "run-blocked", run: done }),
    }),
    onOpen: async () => ({
      ok: true,
      json: async () => ({
        path: "/tmp/run-project",
        run_id: "run-blocked",
        run: done,
      }),
    }),
    onEvents: async () => ({ ok: true, body: streamBody([]) }),
  });
  const fetchMock = vi.mocked(fetch);
  const originalImpl = fetchMock.getMockImplementation();
  fetchMock.mockImplementation(
    async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/artifacts/open")) {
        return {
          ok: true,
          headers: new Headers({
            "Content-Type": "text/plain",
            "Content-Disposition": 'attachment; filename="output.txt"',
          }),
          blob: async () =>
            new Blob(["artifact-bytes"], { type: "text/plain" }),
        } as unknown as Response;
      }
      return originalImpl!(input, init);
    },
  );

  await openTestProject();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  await waitFor(() =>
    expect(screen.getAllByText("run-blocked")).toHaveLength(2),
  );
  const artifactRow = screen
    .getByText("runs/run-blocked/artifacts/source/output.txt")
    .closest(".run-artifact-row");
  if (!artifactRow) throw new Error("artifact row missing");
  fireEvent.click(
    within(artifactRow as HTMLElement).getByRole("button", { name: "Open" }),
  );
  await waitFor(() =>
    expect(
      screen.getByText("The browser blocked the artifact download."),
    ).toBeInTheDocument(),
  );
  anchorClick.mockRestore();
});
