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
