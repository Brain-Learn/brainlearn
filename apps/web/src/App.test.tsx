import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { NodeManifest, Workflow } from "./types";

const bidsManifest: NodeManifest = {
  manifest_schema_version: "1.0",
  id: "input.bids_eeg",
  node_version: "0.1.0",
  label: "BIDS EEG",
  description: "Choose a dataset.",
  category: "Input",
  status: "example",
  ports: [
    {
      id: "dataset",
      label: "Dataset",
      direction: "output",
      data_type: "bids_dataset",
      required: true,
    },
  ],
  parameters: [
    {
      id: "root",
      label: "Dataset root",
      value_type: "string",
      default: "synthetic/example-bids",
      required: true,
    },
  ],
  review_behavior: "none",
  citations: [],
  license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
  capability_requirements: [],
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/validate") && init?.body) {
        const workflow = JSON.parse(String(init.body)) as Workflow;
        const missing = workflow.nodes.some((node) =>
          node.parameters.some(
            (parameter) => parameter.required && parameter.value === null,
          ),
        );
        return {
          ok: true,
          json: async () => ({
            workflow,
            validation: {
              valid: !missing,
              issues: missing
                ? [
                    {
                      code: "missing_required_parameter",
                      node_id: workflow.nodes[0]?.id,
                      message: "Dataset root is required.",
                    },
                  ]
                : [],
            },
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
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
  vi.useRealTimers();
});

test("adds and removes a registry node from an empty canvas", async () => {
  render(<App />);
  expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument();
  const addButton = await screen.findByRole("button", { name: /BIDS EEG/ });

  fireEvent.click(addButton);

  await waitFor(() =>
    expect(screen.getByLabelText("Dataset root")).toBeInTheDocument(),
  );
  expect(
    screen.queryByText("Build an example EEG graph"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove node" }));
  expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument();
});

test("edits a manifest parameter and supports undo and redo", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const input = await screen.findByLabelText("Dataset root");

  fireEvent.change(input, { target: { value: "datasets/changed" } });
  expect(input).toHaveValue("datasets/changed");
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(screen.getByLabelText("Dataset root")).toHaveValue(
    "synthetic/example-bids",
  );
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect(screen.getByLabelText("Dataset root")).toHaveValue("datasets/changed");
});

test("shows backend validation issues on the selected node", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));

  fireEvent.change(await screen.findByLabelText("Dataset root"), {
    target: { value: "" },
  });

  expect(await screen.findAllByText("Dataset root is required.")).toHaveLength(
    2,
  );
});

test("does not overwrite edits made while a save is pending", async () => {
  let capturedBody = "";
  let resolveSave!: (response: unknown) => void;
  const saveGate = new Promise<unknown>((resolve) => {
    resolveSave = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/projects/create") && init?.body) {
        const body = JSON.parse(String(init.body)) as { path: string };
        const workflow = (
          JSON.parse(String(init.body)) as { workflow: Workflow }
        ).workflow;
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: body.path },
            workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/projects/save") && init?.body) {
        capturedBody = String(init.body);
        return saveGate;
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/race-project" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Create$/ }));
  await waitFor(() =>
    expect(screen.getByLabelText("Project folder")).toHaveValue(
      "/tmp/race-project",
    ),
  );

  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Dataset root");
  expect(
    screen.getByRole("button", { name: /^Save$/ }).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));

  // Wait until the save request has started, then edit while it is pending.
  await waitFor(() => expect(capturedBody).not.toBe(""));
  // Edit the graph while the save request is still pending.
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));

  const snapshot = (JSON.parse(capturedBody) as { workflow: Workflow })
    .workflow;
  expect(snapshot.nodes).toHaveLength(1);
  resolveSave({
    ok: true,
    json: async () => ({
      path: "/tmp/race-project",
      manifest: { name: "Race" },
      workflow: snapshot,
      validation: { valid: true, issues: [] },
    }),
  });

  await waitFor(() =>
    expect(
      screen.getAllByText(
        "Ignored a stale project response; canvas and project unchanged.",
      ),
    ).not.toHaveLength(0),
  );
  const draftRaw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
  expect(draftRaw).not.toBeNull();
  const draft = JSON.parse(draftRaw as string) as {
    workflow: Workflow;
  };
  expect(draft.workflow.nodes).toHaveLength(2);
});

test("keeps node ids unique after opening a project", async () => {
  const openedWorkflow: Workflow = {
    schema_version: "1.0",
    id: "opened",
    metadata: {
      name: "Opened",
      description: "",
      created_with: "BrainLearn",
      modality: "EEG",
      status: "example",
    },
    nodes: [
      {
        id: "input-bids-eeg-1",
        type: "input.bids_eeg",
        label: "BIDS EEG",
        category: "Input",
        position: { x: 0, y: 0 },
        ports: bidsManifest.ports,
        parameters: [
          {
            id: "root",
            label: "Dataset root",
            value: "synthetic/example-bids",
            required: true,
          },
        ],
        pauses_for_review: false,
      },
    ],
    edges: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/projects/open")) {
        return {
          ok: true,
          json: async () => ({
            path: "/tmp/opened",
            manifest: { name: "Opened" },
            workflow: openedWorkflow,
            validation: { valid: true, issues: [] },
          }),
        };
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/opened" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() => {
    const draftRaw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
    const draft = JSON.parse(draftRaw as string) as { workflow: Workflow };
    expect(draft.workflow.nodes).toHaveLength(1);
  });

  fireEvent.click(screen.getByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    const draftRaw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
    const draft = JSON.parse(draftRaw as string) as { workflow: Workflow };
    expect(draft.workflow.nodes).toHaveLength(2);
  });
  const draftRaw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
  const draft = JSON.parse(draftRaw as string) as { workflow: Workflow };
  const ids = draft.workflow.nodes.map((node) => node.id);
  expect(new Set(ids).size).toBe(ids.length);
  expect(ids).toContain("input-bids-eeg-1");
});

test("keeps node ids unique after draft recovery", async () => {
  const recovered: Workflow = {
    schema_version: "1.0",
    id: "recovered",
    metadata: {
      name: "Recovered",
      description: "",
      created_with: "BrainLearn",
      modality: "EEG",
      status: "example",
    },
    nodes: [
      {
        id: "input-bids-eeg-1",
        type: "input.bids_eeg",
        label: "BIDS EEG",
        category: "Input",
        position: { x: 0, y: 0 },
        ports: bidsManifest.ports,
        parameters: [
          {
            id: "root",
            label: "Dataset root",
            value: "synthetic/example-bids",
            required: true,
          },
        ],
        pauses_for_review: false,
      },
    ],
    edges: [],
  };
  localStorage.setItem(
    "brainlearn.unsaved-workflow.v1",
    JSON.stringify({
      savedAt: "2026-09-12T00:00:00.000Z",
      workflow: recovered,
      projectPath: null,
    }),
  );
  render(<App />);
  expect(
    await screen.findByText(/Recovered unsaved edits/),
  ).toBeInTheDocument();

  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    const draftRaw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
    const draft = JSON.parse(draftRaw as string) as { workflow: Workflow };
    expect(draft.workflow.nodes).toHaveLength(2);
  });
  const draftRaw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
  const draft = JSON.parse(draftRaw as string) as { workflow: Workflow };
  const ids = draft.workflow.nodes.map((node) => node.id);
  expect(new Set(ids).size).toBe(ids.length);
});

test("ignores stale validation responses", async () => {
  const pending: Array<{
    workflow: Workflow;
    resolve: (response: unknown) => void;
  }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/validate") && init?.body) {
        const workflow = JSON.parse(String(init.body)) as Workflow;
        const value = workflow.nodes[0]?.parameters[0]?.value;
        const message = value === "second" ? "second-result" : "first-result";
        return new Promise<unknown>((resolve) => {
          pending.push({
            workflow,
            resolve: () =>
              resolve({
                ok: true,
                json: async () => ({
                  workflow,
                  validation: {
                    valid: false,
                    issues: [
                      {
                        code: "missing_required_parameter",
                        node_id: workflow.nodes[0]?.id,
                        message,
                      },
                    ],
                  },
                }),
              }),
          });
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const input = await screen.findByLabelText("Dataset root");
  fireEvent.change(input, { target: { value: "second" } });

  expect(pending).toHaveLength(2);
  // Resolve the newest validation first, then the stale one.
  pending[1].resolve(undefined);
  await waitFor(() =>
    expect(screen.getAllByText("second-result").length).toBeGreaterThan(0),
  );
  pending[0].resolve(undefined);
  await waitFor(() =>
    expect(screen.queryByText("first-result")).not.toBeInTheDocument(),
  );
  expect(screen.getAllByText("second-result").length).toBeGreaterThan(0);
});

function bidsNode(id: string): Workflow["nodes"][number] {
  return {
    id,
    type: "input.bids_eeg",
    label: "BIDS EEG",
    category: "Input",
    position: { x: 0, y: 0 },
    ports: bidsManifest.ports,
    parameters: [
      {
        id: "root",
        label: "Dataset root",
        value: "synthetic/example-bids",
        required: true,
      },
    ],
    pauses_for_review: false,
  };
}

function testWorkflow(id: string, nodeIds: string[]): Workflow {
  return {
    schema_version: "1.0",
    id,
    metadata: {
      name: id,
      description: "",
      created_with: "BrainLearn",
      modality: "EEG",
      status: "example",
    },
    nodes: nodeIds.map((nodeId) => bidsNode(nodeId)),
    edges: [],
  };
}

test("typing a folder does not move the active project until open succeeds", async () => {
  const savedPaths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/projects/open") && init?.body) {
        const body = JSON.parse(String(init.body)) as { path: string };
        if (body.path === "/tmp/project-b") {
          return {
            ok: false,
            status: 404,
            json: async () => ({ detail: "No project at /tmp/project-b." }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            path: "/tmp/project-a",
            manifest: { name: "A" },
            workflow: testWorkflow("a", ["node-a"]),
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/projects/save") && init?.body) {
        const body = JSON.parse(String(init.body)) as { path: string };
        savedPaths.push(body.path);
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: body.path },
            workflow: (JSON.parse(String(init.body)) as { workflow: Workflow })
              .workflow,
            validation: { valid: true, issues: [] },
          }),
        };
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });

  // Open A: both graph and active identity switch to A.
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(
      screen.getByText("Active project: /tmp/project-a"),
    ).toBeInTheDocument(),
  );

  // Type B without opening: the draft must still pair workflow A with A.
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-b" },
  });
  await waitFor(() => {
    const draft = JSON.parse(
      localStorage.getItem("brainlearn.unsaved-workflow.v1") as string,
    ) as { workflow: Workflow; projectPath: string | null };
    expect(draft.workflow.id).toBe("a");
    expect(draft.projectPath).toBe("/tmp/project-a");
  });

  // Open B fails: active workflow, path, and draft association stay on A.
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(
      screen.getByText("No project at /tmp/project-b."),
    ).toBeInTheDocument(),
  );
  expect(
    screen.getByText("Active project: /tmp/project-a"),
  ).toBeInTheDocument();
  const draft = JSON.parse(
    localStorage.getItem("brainlearn.unsaved-workflow.v1") as string,
  ) as { workflow: Workflow; projectPath: string | null };
  expect(draft.workflow.id).toBe("a");
  expect(draft.projectPath).toBe("/tmp/project-a");

  // A subsequent save still targets A, never the typed-but-unopened B.
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(savedPaths).toEqual(["/tmp/project-a"]));
});

test("opening a second project atomically switches graph and identity", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/projects/open") && init?.body) {
        const body = JSON.parse(String(init.body)) as { path: string };
        const tag = body.path === "/tmp/project-b" ? "b" : "a";
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: tag.toUpperCase() },
            workflow: testWorkflow(tag, [`node-${tag}`]),
            validation: { valid: true, issues: [] },
          }),
        };
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(
      screen.getByText("Active project: /tmp/project-a"),
    ).toBeInTheDocument(),
  );

  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-b" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() =>
    expect(
      screen.getByText("Active project: /tmp/project-b"),
    ).toBeInTheDocument(),
  );
  const draft = JSON.parse(
    localStorage.getItem("brainlearn.unsaved-workflow.v1") as string,
  ) as { workflow: Workflow; projectPath: string | null };
  expect(draft.workflow.id).toBe("b");
  expect(draft.projectPath).toBe("/tmp/project-b");
  expect(draft.workflow.nodes.map((node) => node.id)).toEqual(["node-b"]);
});

test("leaves graph and project unchanged when the graph changes during open", async () => {
  let resolveOpen!: (response: unknown) => void;
  const openGate = new Promise<unknown>((resolve) => {
    resolveOpen = resolve;
  });
  let openRequested = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/projects/open")) {
        openRequested = true;
        return openGate;
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Dataset root");
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/original" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() => expect(openRequested).toBe(true));

  // Edit the current graph while the open request is pending.
  fireEvent.click(screen.getByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    const draft = JSON.parse(
      localStorage.getItem("brainlearn.unsaved-workflow.v1") as string,
    ) as { workflow: Workflow };
    expect(draft.workflow.nodes).toHaveLength(2);
  });

  resolveOpen({
    ok: true,
    json: async () => ({
      path: "/tmp/other-project",
      manifest: { name: "Other" },
      workflow: testWorkflow("other", ["input-bids-eeg-9"]),
      validation: { valid: true, issues: [] },
    }),
  });

  await waitFor(() =>
    expect(
      screen.getAllByText(
        "Ignored a stale project response; canvas and project unchanged.",
      ).length,
    ).toBeGreaterThan(0),
  );
  // The retained graph keeps its identity: still two local nodes, no active
  // project, and the typed folder is not promoted to the draft association.
  const draft = JSON.parse(
    localStorage.getItem("brainlearn.unsaved-workflow.v1") as string,
  ) as { workflow: Workflow; projectPath: string | null };
  expect(draft.workflow.nodes).toHaveLength(2);
  expect(draft.projectPath).toBeNull();
  expect(
    screen.getByText("Active project: none — open or create one"),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Project folder")).toHaveValue("/tmp/original");
});

test("applies only the newest overlapping project response", async () => {
  const resolvers = new Map<string, (response: unknown) => void>();
  const savedPaths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
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
      if (url.endsWith("/api/projects/open") && init?.body) {
        const body = JSON.parse(String(init.body)) as { path: string };
        return new Promise<unknown>((resolve) => {
          resolvers.set(`open:${body.path}`, () =>
            resolve({
              ok: true,
              json: async () => ({
                path: body.path,
                manifest: { name: body.path },
                workflow: testWorkflow(body.path, [
                  `node-for-${body.path.replaceAll("/", "-")}`,
                ]),
                validation: { valid: true, issues: [] },
              }),
            }),
          );
        });
      }
      if (url.endsWith("/api/projects/save") && init?.body) {
        const body = JSON.parse(String(init.body)) as { path: string };
        savedPaths.push(body.path);
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: body.path },
            workflow: (JSON.parse(String(init.body)) as { workflow: Workflow })
              .workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });

  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-b" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await waitFor(() => expect(resolvers.has("open:/tmp/project-b")).toBe(true));

  // Resolve in reverse order: the older open must be ignored.
  resolvers.get("open:/tmp/project-b")?.(undefined);
  await waitFor(() =>
    expect(screen.getByLabelText("Project folder")).toHaveValue(
      "/tmp/project-b",
    ),
  );
  resolvers.get("open:/tmp/project-a")?.(undefined);
  await waitFor(() =>
    expect(
      screen.getAllByText(
        "Ignored a stale project response; canvas and project unchanged.",
      ).length,
    ).toBeGreaterThan(0),
  );
  expect(screen.getByLabelText("Project folder")).toHaveValue("/tmp/project-b");

  // A subsequent save must target the project shown with the active graph.
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(savedPaths).toEqual(["/tmp/project-b"]));
});

test("prefers edited-graph validation over delayed recovery validation", async () => {
  const pending: Array<{
    workflow: Workflow;
    resolve: (response: unknown) => void;
  }> = [];
  const recovered = testWorkflow("recovered", ["input-bids-eeg-1"]);
  localStorage.setItem(
    "brainlearn.unsaved-workflow.v1",
    JSON.stringify({
      savedAt: "2026-09-12T00:00:00.000Z",
      workflow: recovered,
      projectPath: null,
    }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/validate") && init?.body) {
        const workflow = JSON.parse(String(init.body)) as Workflow;
        const message =
          workflow.nodes.length > 1 ? "edited-result" : "recovery-result";
        return new Promise<unknown>((resolve) => {
          pending.push({
            workflow,
            resolve: () =>
              resolve({
                ok: true,
                json: async () => ({
                  workflow,
                  validation: {
                    valid: false,
                    issues: [
                      {
                        code: "missing_required_parameter",
                        node_id: workflow.nodes[0]?.id,
                        message,
                      },
                    ],
                  },
                }),
              }),
          });
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  expect(
    await screen.findByText(/Recovered unsaved edits/),
  ).toBeInTheDocument();
  await waitFor(() => expect(pending).toHaveLength(1));

  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => expect(pending).toHaveLength(2));

  pending[1].resolve(undefined);
  await waitFor(() =>
    expect(screen.getAllByText("edited-result").length).toBeGreaterThan(0),
  );
  pending[0].resolve(undefined);
  await waitFor(() =>
    expect(screen.queryByText("recovery-result")).not.toBeInTheDocument(),
  );
  expect(screen.getAllByText("edited-result").length).toBeGreaterThan(0);
});

test("preserves the active project name when saving a recovered draft", async () => {
  const recovered = testWorkflow("recovered", ["node-a"]);
  localStorage.setItem(
    "brainlearn.unsaved-workflow.v1",
    JSON.stringify({
      savedAt: "2026-09-12T00:00:00.000Z",
      workflow: recovered,
      projectPath: "/tmp/project-a",
      projectName: "Project A",
    }),
  );
  let savedBody: { path: string; name?: string } | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
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
      if (url.endsWith("/api/projects/save") && init?.body) {
        const body = JSON.parse(String(init.body)) as {
          path: string;
          name?: string;
          workflow: Workflow;
        };
        savedBody = body;
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: body.name },
            workflow: body.workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );

  render(<App />);
  await screen.findByText("Active project: /tmp/project-a");
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));

  await waitFor(() => expect(savedBody).toBeDefined());
  expect(savedBody).toMatchObject({
    path: "/tmp/project-a",
    name: "Project A",
  });
});

test("selecting a recent project does not rename the active project", async () => {
  let savedBody: { path: string; name?: string } | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
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
            path: "/tmp/project-a",
            manifest: { name: "Project A" },
            workflow: testWorkflow("a", ["node-a"]),
            validation: { valid: true, issues: [] },
          }),
        };
      }
      if (url.endsWith("/api/projects/recent")) {
        return {
          ok: true,
          json: async () => [
            { path: "/tmp/project-b", name: "Project B", last_opened: "now" },
          ],
        };
      }
      if (url.endsWith("/api/projects/save") && init?.body) {
        const body = JSON.parse(String(init.body)) as {
          path: string;
          name?: string;
          workflow: Workflow;
        };
        savedBody = body;
        return {
          ok: true,
          json: async () => ({
            path: body.path,
            manifest: { name: body.name },
            workflow: body.workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );

  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/project-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));
  await screen.findByText("Active project: /tmp/project-a");

  fireEvent.click(screen.getByRole("button", { name: /^Recent$/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Project B" }));
  expect(screen.getByLabelText("Project name")).toHaveValue("Project B");
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));

  await waitFor(() => expect(savedBody).toBeDefined());
  expect(savedBody).toMatchObject({
    path: "/tmp/project-a",
    name: "Project A",
  });
});

function readDraftWorkflow(): Workflow {
  const raw = localStorage.getItem("brainlearn.unsaved-workflow.v1");
  if (!raw) throw new Error("Expected a persisted draft workflow");
  return (JSON.parse(raw) as { workflow: Workflow }).workflow;
}

function dropPayload(manifestId: string) {
  return {
    getData: (format: string) =>
      format === "application/x-brainlearn-node" || format === "text/plain"
        ? manifestId
        : "",
    setData: () => {},
    dropEffect: "copy",
    effectAllowed: "copy",
    files: [],
    types: ["application/x-brainlearn-node"],
  };
}

test("palette buttons are draggable and keyboard insertion matches click", async () => {
  render(<App />);
  const addButton = await screen.findByRole("button", { name: /BIDS EEG/ });
  expect(addButton).toHaveAttribute("draggable", "true");

  fireEvent.keyDown(addButton, { key: "Enter" });
  await screen.findByLabelText("Dataset root");
  const draft = readDraftWorkflow();
  expect(draft.nodes).toHaveLength(1);
  const keyboardNode = draft.nodes[0];
  expect(keyboardNode.type).toBe("input.bids_eeg");
  expect(keyboardNode.label).toBe("BIDS EEG");
});

function dispatchDrop(
  zone: HTMLElement,
  clientX: number,
  clientY: number,
  manifestId: string | null,
) {
  const event = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clientX", { value: clientX });
  Object.defineProperty(event, "clientY", { value: clientY });
  if (manifestId !== null) {
    Object.defineProperty(event, "dataTransfer", {
      value: dropPayload(manifestId),
    });
  } else {
    Object.defineProperty(event, "dataTransfer", { value: null });
  }
  zone.dispatchEvent(event);
}

test("dropping a palette node inserts and selects the same fields as click", async () => {
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  const zone = screen.getByTestId("canvas-drop-zone");

  fireEvent.dragOver(zone, { dataTransfer: dropPayload("input.bids_eeg") });
  expect(zone.className).toMatch(/drag-over/);
  dispatchDrop(zone as HTMLElement, 300, 250, "input.bids_eeg");

  await screen.findByLabelText("Dataset root");
  const draft = readDraftWorkflow();
  expect(draft.nodes).toHaveLength(1);
  expect(draft.nodes[0].type).toBe("input.bids_eeg");
  expect(draft.nodes[0].label).toBe("BIDS EEG");
  expect(typeof draft.nodes[0].position.x).toBe("number");
  expect(typeof draft.nodes[0].position.y).toBe("number");
});

test("invalid, unknown, and missing drop payloads change nothing", async () => {
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  const zone = screen.getByTestId("canvas-drop-zone") as HTMLElement;

  dispatchDrop(zone, 300, 250, "../evil");
  dispatchDrop(zone, 300, 250, "unknown.node");
  dispatchDrop(zone, 300, 250, null);
  await waitFor(() =>
    expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument(),
  );
  expect(readDraftWorkflow().nodes).toHaveLength(0);
});

test("custom title persists, renders on canvas, and undoes in one step", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const titleInput = (await screen.findByLabelText(
    "Custom title",
  )) as HTMLInputElement;

  fireEvent.change(titleInput, { target: { value: "My harvest step" } });
  fireEvent.blur(titleInput);

  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation).toMatchObject({
      title: "My harvest step",
    });
  });
  expect(await screen.findByText("My harvest step")).toBeInTheDocument();
  const draft = readDraftWorkflow();
  expect(draft.nodes[0].type).toBe("input.bids_eeg");
  expect(draft.nodes[0].label).toBe("BIDS EEG");
  expect(draft.nodes[0].parameters).toHaveLength(1);

  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation).toBeUndefined();
  });
  expect(screen.queryByText("My harvest step")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() =>
    expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument(),
  );
  expect(readDraftWorkflow().nodes).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
});

test("accent and compact choices render on the canvas card", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Custom title");

  fireEvent.change(screen.getByLabelText("Accent color"), {
    target: { value: "violet" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "Compact display" }));

  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation).toMatchObject({
      accent: "violet",
      compact: true,
    });
  });
  expect(
    document.querySelector(".workflow-card.accent-violet.compact"),
  ).not.toBeNull();
});

test("notes are stored as plain text and never rendered as HTML", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const notesInput = (await screen.findByLabelText(
    "Display notes",
  )) as HTMLTextAreaElement;
  const payload = '<img src="x" onerror="alert(1)">remember this';

  fireEvent.change(notesInput, { target: { value: payload } });
  fireEvent.blur(notesInput);

  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation?.notes).toBe(payload);
  });
  expect(screen.getByLabelText("Display notes")).toHaveValue(payload);
  expect(document.querySelector(".inspector-content img")).toBeNull();
});

test("reset removes presentation overrides", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const titleInput = (await screen.findByLabelText(
    "Custom title",
  )) as HTMLInputElement;
  expect(
    screen.getByRole("button", { name: /Reset to manifest defaults/ }),
  ).toBeDisabled();

  fireEvent.change(titleInput, { target: { value: "Temporary" } });
  fireEvent.blur(titleInput);
  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation?.title).toBe("Temporary");
  });

  fireEvent.click(
    screen.getByRole("button", { name: /Reset to manifest defaults/ }),
  );
  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation).toBeUndefined();
  });
  expect(screen.queryByText("Temporary")).not.toBeInTheDocument();
});

test("presentation survives project open and draft recovery", async () => {
  const presented: Workflow = {
    schema_version: "1.0",
    id: "presented",
    metadata: {
      name: "Presented",
      description: "",
      created_with: "BrainLearn",
      modality: "EEG",
      status: "example",
    },
    nodes: [
      {
        ...bidsNode("node-a"),
        position: { x: 44, y: 55 },
        presentation: {
          title: "Reopened step",
          accent: "amber",
          compact: false,
          notes: "kept",
        },
      },
    ],
    edges: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/projects/open")) {
        return {
          ok: true,
          json: async () => ({
            path: "/tmp/presented",
            manifest: { name: "Presented" },
            workflow: presented,
            validation: { valid: true, issues: [] },
          }),
        };
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  fireEvent.change(screen.getByLabelText("Session token"), {
    target: { value: "test-token" },
  });
  fireEvent.change(screen.getByLabelText("Project folder"), {
    target: { value: "/tmp/presented" },
  });
  fireEvent.click(screen.getByRole("button", { name: /^Open$/ }));

  expect(await screen.findByText("Reopened step")).toBeInTheDocument();
  await waitFor(() => {
    expect(readDraftWorkflow().nodes[0].presentation).toMatchObject({
      title: "Reopened step",
      accent: "amber",
      notes: "kept",
    });
  });
  expect(document.querySelector(".workflow-card.accent-amber")).not.toBeNull();
});

function workflowCardForTitle(title: string): Element {
  const heading = screen.getByText(title);
  const card = heading.closest(".workflow-card");
  if (!card) throw new Error(`No workflow card renders title ${title}`);
  return card;
}

function stubReducedMotion(matches: boolean) {
  const listeners = new Set<() => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: (_type: string, listener: () => void) => {
        listeners.add(listener);
      },
      removeEventListener: (type: string, listener: () => void) => {
        void type;
        listeners.delete(listener);
      },
    })),
  );
  return listeners;
}

test("custom accent survives selected and invalid states", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Custom title");

  fireEvent.change(screen.getByLabelText("Custom title"), {
    target: { value: "Accent probe" },
  });
  fireEvent.blur(screen.getByLabelText("Custom title"));
  fireEvent.change(screen.getByLabelText("Accent color"), {
    target: { value: "violet" },
  });

  // Selected and valid.
  await waitFor(() =>
    expect(workflowCardForTitle("Accent probe")).toHaveClass("accent-violet"),
  );
  expect(workflowCardForTitle("Accent probe")).toHaveClass("selected");
  expect(workflowCardForTitle("Accent probe")).not.toHaveClass("invalid");

  // Selected and invalid.
  fireEvent.change(screen.getByLabelText("Dataset root"), {
    target: { value: "" },
  });
  await waitFor(() =>
    expect(workflowCardForTitle("Accent probe")).toHaveClass("invalid"),
  );
  expect(workflowCardForTitle("Accent probe")).toHaveClass("accent-violet");
  expect(workflowCardForTitle("Accent probe")).toHaveClass("selected");

  // Ordinary and invalid: adding a second node moves selection away.
  fireEvent.click(screen.getByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    expect(readDraftWorkflow().nodes).toHaveLength(2);
  });
  expect(workflowCardForTitle("Accent probe")).toHaveClass("accent-violet");
  expect(workflowCardForTitle("Accent probe")).toHaveClass("invalid");
  expect(workflowCardForTitle("Accent probe")).not.toHaveClass("selected");

  // Ordinary and valid: reselect, repair, then move selection away again.
  fireEvent.click(workflowCardForTitle("Accent probe"));
  await waitFor(() =>
    expect(workflowCardForTitle("Accent probe")).toHaveClass("selected"),
  );
  fireEvent.change(screen.getByLabelText("Dataset root"), {
    target: { value: "datasets/fixed" },
  });
  fireEvent.click(screen.getByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    expect(readDraftWorkflow().nodes).toHaveLength(3);
  });
  await waitFor(() =>
    expect(workflowCardForTitle("Accent probe")).not.toHaveClass("invalid"),
  );
  const card = workflowCardForTitle("Accent probe");
  expect(card).toHaveClass("accent-violet");
  expect(card).not.toHaveClass("selected");
  expect(card).not.toHaveClass("invalid");
  expect(readDraftWorkflow().nodes[0].presentation).toMatchObject({
    title: "Accent probe",
    accent: "violet",
  });
});

test("inspector content remounts on selection change but not on validation", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Custom title");
  const firstPanel = document.querySelector(".inspector-content");
  expect(firstPanel).not.toBeNull();

  fireEvent.click(screen.getByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    expect(readDraftWorkflow().nodes).toHaveLength(2);
  });
  const secondPanel = document.querySelector(".inspector-content");
  expect(secondPanel).not.toBeNull();
  expect(secondPanel).not.toBe(firstPanel);

  fireEvent.change(screen.getByLabelText("Dataset root"), {
    target: { value: "datasets/edited" },
  });
  await waitFor(() =>
    expect(readDraftWorkflow().nodes[1].parameters[0].value).toBe(
      "datasets/edited",
    ),
  );
  expect(document.querySelector(".inspector-content")).toBe(secondPanel);
});

test("node removal shows a leaving state before disappearing", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Custom title");

  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Remove node" }));
  expect(document.querySelector(".react-flow__node.leaving")).not.toBeNull();
  expect(readDraftWorkflow().nodes).toHaveLength(0);

  act(() => {
    vi.advanceTimersByTime(300);
  });
  expect(document.querySelector(".react-flow__node.leaving")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(readDraftWorkflow().nodes).toHaveLength(1);
  expect(document.querySelector(".react-flow__node.leaving")).toBeNull();
});

test("node removal is immediate under reduced motion", async () => {
  stubReducedMotion(true);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Custom title");

  fireEvent.click(screen.getByRole("button", { name: "Remove node" }));
  expect(document.querySelector(".react-flow__node.leaving")).toBeNull();
  expect(readDraftWorkflow().nodes).toHaveLength(0);
});

test("blurring an unchanged title creates no undo entry", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const titleInput = await screen.findByLabelText("Custom title");

  fireEvent.blur(titleInput);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() =>
    expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument(),
  );
  expect(readDraftWorkflow().nodes).toHaveLength(0);
});

const demoExampleWorkflow: Workflow = {
  schema_version: "1.0",
  id: "demo-branched",
  metadata: {
    name: "Branched demonstration",
    description: "Non-scientific demonstration graph.",
    created_with: "BrainLearn",
    modality: "EEG",
    status: "example",
  },
  nodes: [
    {
      id: "source",
      type: "demo.copy",
      label: "Copy",
      category: "Demonstration",
      position: { x: 90, y: 90 },
      ports: [
        {
          id: "output",
          label: "Output",
          direction: "output",
          data_type: "raw_eeg",
          required: true,
        },
      ],
      parameters: [
        { id: "text", label: "Text", value: "trunk-bytes", required: false },
      ],
      pauses_for_review: false,
    },
    {
      id: "gate",
      type: "demo.review",
      label: "Review",
      category: "Demonstration",
      position: { x: 330, y: 300 },
      ports: [],
      parameters: [],
      pauses_for_review: true,
    },
  ],
  edges: [],
};

const eegExampleWorkflow: Workflow = {
  schema_version: "1.0",
  id: "eeg-first-look",
  metadata: {
    name: "EEG first look",
    description: "",
    created_with: "BrainLearn",
    modality: "EEG",
    status: "example",
  },
  nodes: [bidsNode("bids")],
  edges: [],
};

function stubExampleFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/examples")) {
        return {
          ok: true,
          json: async () => ({
            examples: [
              {
                id: "eeg-first-look",
                name: "EEG first look",
                description: "",
                schema_version: "1.0",
              },
              {
                id: "demo-branched",
                name: "Branched demonstration",
                description: "Non-scientific demonstration graph.",
                schema_version: "1.0",
              },
            ],
          }),
        };
      }
      if (url.endsWith("/api/workflows/examples/demo-branched")) {
        return { ok: true, json: async () => demoExampleWorkflow };
      }
      if (url.endsWith("/api/workflows/examples/eeg-first-look")) {
        return { ok: true, json: async () => eegExampleWorkflow };
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
}

test("loading the branched example replaces the graph with fresh history", async () => {
  stubExampleFetch();
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await screen.findByLabelText("Dataset root");

  fireEvent.click(
    await screen.findByRole("button", { name: /Branched demonstration/ }),
  );
  await waitFor(() => {
    expect(readDraftWorkflow().nodes).toHaveLength(2);
  });
  const draft = readDraftWorkflow();
  expect(draft.id).toBe("demo-branched");
  expect(draft.nodes.map((node) => node.type)).toEqual([
    "demo.copy",
    "demo.review",
  ]);
  expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
  expect(screen.getByText(/Select a node to inspect/)).toBeInTheDocument();
  expect(
    await screen.findByText("Loaded example Branched demonstration."),
  ).toBeInTheDocument();
});

test("loading the EEG example keeps compatibility", async () => {
  stubExampleFetch();
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: /EEG first look/ }),
  );
  await waitFor(() => {
    expect(readDraftWorkflow().nodes).toHaveLength(1);
  });
  expect(readDraftWorkflow().id).toBe("eeg-first-look");
  expect(
    await screen.findByText("Loaded example EEG first look."),
  ).toBeInTheDocument();
});

function stubDeferredExamples() {
  const resolvers = new Map<string, (response: unknown) => void>();
  const rejecters = new Map<string, (reason: unknown) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/examples")) {
        return {
          ok: true,
          json: async () => ({
            examples: [
              {
                id: "eeg-first-look",
                name: "EEG first look",
                description: "",
                schema_version: "1.0",
              },
              {
                id: "demo-branched",
                name: "Branched demonstration",
                description: "",
                schema_version: "1.0",
              },
            ],
          }),
        };
      }
      if (url.includes("/api/workflows/examples/")) {
        const id = url.split("/").pop() as string;
        const payload =
          id === "demo-branched" ? demoExampleWorkflow : eegExampleWorkflow;
        return new Promise<unknown>((resolve, reject) => {
          resolvers.set(id, () =>
            resolve({ ok: true, json: async () => payload }),
          );
          rejecters.set(id, reject);
        });
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  return { resolvers, rejecters };
}

test("the latest requested example wins when responses arrive out of order", async () => {
  const { resolvers } = stubDeferredExamples();
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: /Branched demonstration/ }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /EEG first look/ }),
  );
  await waitFor(() => {
    expect(resolvers.has("demo-branched")).toBe(true);
    expect(resolvers.has("eeg-first-look")).toBe(true);
  });

  resolvers.get("eeg-first-look")?.(undefined);
  await waitFor(() => {
    expect(readDraftWorkflow().id).toBe("eeg-first-look");
  });
  expect(
    await screen.findByText("Loaded example EEG first look."),
  ).toBeInTheDocument();
  resolvers.get("demo-branched")?.(undefined);
  await waitFor(() => expect(readDraftWorkflow().nodes).toHaveLength(1));
  expect(readDraftWorkflow().id).toBe("eeg-first-look");
  expect(
    screen.getByText("Loaded example EEG first look."),
  ).toBeInTheDocument();
  expect(
    screen.queryByText("Ignored a stale example response; canvas unchanged."),
  ).not.toBeInTheDocument();
});

test("an older rejection arriving after the latest load changes nothing", async () => {
  const { resolvers, rejecters } = stubDeferredExamples();
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: /Branched demonstration/ }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /EEG first look/ }),
  );
  await waitFor(() => {
    expect(resolvers.has("demo-branched")).toBe(true);
    expect(resolvers.has("eeg-first-look")).toBe(true);
  });

  resolvers.get("eeg-first-look")?.(undefined);
  await waitFor(() => {
    expect(readDraftWorkflow().id).toBe("eeg-first-look");
  });
  await act(async () => {
    rejecters.get("demo-branched")?.(new Error("stale network boom"));
  });
  expect(readDraftWorkflow().id).toBe("eeg-first-look");
  expect(readDraftWorkflow().nodes).toHaveLength(1);
  expect(
    screen.getByText("Loaded example EEG first look."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/stale network boom/)).not.toBeInTheDocument();
  expect(
    screen.queryByText("Ignored a stale example response; canvas unchanged."),
  ).not.toBeInTheDocument();
});

test("a graph edit during example load keeps the edited graph", async () => {
  const { resolvers } = stubDeferredExamples();
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: /Branched demonstration/ }),
  );
  await waitFor(() => {
    expect(resolvers.has("demo-branched")).toBe(true);
  });

  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  await waitFor(() => {
    expect(readDraftWorkflow().nodes).toHaveLength(1);
  });
  resolvers.get("demo-branched")?.(undefined);
  await waitFor(() =>
    expect(
      screen.getByText("Ignored a stale example response; canvas unchanged."),
    ).toBeInTheDocument(),
  );
  const draft = readDraftWorkflow();
  expect(draft.nodes).toHaveLength(1);
  expect(draft.nodes[0].type).toBe("input.bids_eeg");
});
