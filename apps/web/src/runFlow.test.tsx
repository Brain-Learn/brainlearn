import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { NodeManifest, RunRecord, Workflow } from "./types";

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

function terminalRun(state: RunRecord["state"]): RunRecord {
  return {
    schema_version: "1.0",
    id: "run-demo-1",
    workflow_id: "demo",
    workflow_schema_version: "1.0",
    workflow_identity: "brainlearn-v1:workflow:" + "a".repeat(64),
    state,
    created_at: "2026-09-13T00:00:00+00:00",
    started_at: "2026-09-13T00:00:01+00:00",
    finished_at: "2026-09-13T00:00:02+00:00",
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
    ],
    failure: null,
  };
}

function stubRunFlow(options?: {
  startRun?: () => Promise<unknown>;
  onStartBody?: (body: Record<string, unknown>) => void;
}) {
  const startCalls: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
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
        return {
          ok: true,
          json: async () => ({
            path: "/tmp/run-project",
            manifest: { name: "Run Project" },
            workflow: {
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
            } satisfies Workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/runs/start") && init?.body) {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        startCalls.push(body);
        options?.onStartBody?.(body);
        if (options?.startRun) return options.startRun();
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            run_id: "run-demo-1",
            run: terminalRun("succeeded"),
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  return startCalls;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
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

async function openProjectAndRun() {
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
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

test("Run workflow requires an open project", async () => {
  stubRunFlow();
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  expect(
    await screen.findByText("Open or create a project before running."),
  ).toBeInTheDocument();
});

test("Run workflow launches and renders the result in the drawer", async () => {
  stubRunFlow();
  await openProjectAndRun();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  expect(await screen.findByText("Succeeded")).toBeInTheDocument();
  expect(screen.getAllByText("run-demo-1")).toHaveLength(2);
  expect(screen.getByText("Run run-demo-1 started.")).toBeInTheDocument();
});

test("duplicate launches are prevented", async () => {
  let resolveStart!: (value: unknown) => void;
  const gate = new Promise<unknown>((resolve) => {
    resolveStart = resolve;
  });
  const startCalls = stubRunFlow({ startRun: () => gate });
  await openProjectAndRun();

  const runButton = screen.getByRole("button", { name: "Run workflow" });
  fireEvent.click(runButton);
  fireEvent.click(runButton);
  await waitFor(() => expect(startCalls).toHaveLength(1));
  expect(screen.getByRole("button", { name: "Launching…" })).toBeDisabled();

  resolveStart({
    ok: true,
    json: async () => ({
      path: "/tmp/run-project",
      run_id: "run-demo-1",
      run: terminalRun("succeeded"),
    }),
  });
  await waitFor(() =>
    expect(screen.getByText("Succeeded")).toBeInTheDocument(),
  );
  expect(startCalls).toHaveLength(1);
});

test("a validation failure blocks the launch with an actionable message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
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
            validation: {
              valid: false,
              issues: [{ code: "cycle", message: "cycle" }],
            },
          }),
        };
      }
      if (url.endsWith("/api/projects/open")) {
        return {
          ok: true,
          json: async () => ({
            path: "/tmp/run-project",
            manifest: { name: "Run Project" },
            workflow: {
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
            } satisfies Workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  await openProjectAndRun();
  fireEvent.click(screen.getByRole("button", { name: "Run workflow" }));
  expect(
    await screen.findByText("Fix the validation issues before running."),
  ).toBeInTheDocument();
});
