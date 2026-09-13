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
import type { CanvasPosition } from "./graph";
import type { NodeManifest, Workflow } from "./types";

const mocked = vi.hoisted(() => ({
  converter: (point: { x: number; y: number }): CanvasPosition => ({
    ...point,
  }),
}));

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...actual,
    ReactFlow: () => <div data-testid="rf-stub" />,
    Background: () => null,
    Controls: () => null,
    useReactFlow: () => ({
      screenToFlowPosition: (point: { x: number; y: number }) =>
        mocked.converter(point),
    }),
  };
});

function demoPort(
  id: string,
  direction: "input" | "output",
  required: boolean,
): NodeManifest["ports"][number] {
  return { id, label: id, direction, data_type: "raw_eeg", required };
}

function demoManifest(
  id: string,
  label: string,
  ports: NodeManifest["ports"],
  parameters: NodeManifest["parameters"] = [],
  review_behavior: "none" | "required" = "none",
): NodeManifest {
  return {
    manifest_schema_version: "1.0",
    id,
    node_version: "0.1.0",
    label,
    description: `Non-scientific demonstration node: ${label}.`,
    category: "Demonstration",
    status: "example",
    ports,
    parameters,
    review_behavior,
    citations: [],
    license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
    capability_requirements: [],
  };
}

const demoRegistry: NodeManifest[] = [
  demoManifest(
    "demo.copy",
    "Copy",
    [demoPort("in", "input", false), demoPort("output", "output", true)],
    [
      {
        id: "text",
        label: "Text",
        value_type: "string",
        default: "brainlearn-demo",
        required: false,
      },
    ],
  ),
  demoManifest(
    "demo.delay",
    "Delay",
    [demoPort("out", "output", false)],
    [
      {
        id: "seconds",
        label: "Delay (seconds)",
        value_type: "number",
        default: 0.1,
        required: false,
      },
    ],
  ),
  demoManifest(
    "demo.fail",
    "Fail",
    [demoPort("out", "output", false)],
    [
      {
        id: "message",
        label: "Failure message",
        value_type: "string",
        default: "demonstration failure",
        required: false,
      },
    ],
  ),
  demoManifest(
    "demo.review",
    "Review",
    [demoPort("out", "output", false)],
    [],
    "required",
  ),
  demoManifest("demo.relay", "Relay", [
    demoPort("in", "input", true),
    demoPort("output", "output", true),
  ]),
];

function stubDemoFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => demoRegistry };
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
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
}

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

function dispatchDrop(
  zone: HTMLElement,
  clientX: number,
  clientY: number,
  manifestId: string,
) {
  const event = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clientX", { value: clientX });
  Object.defineProperty(event, "clientY", { value: clientY });
  Object.defineProperty(event, "dataTransfer", {
    value: dropPayload(manifestId),
  });
  zone.dispatchEvent(event);
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  mocked.converter = ({ x, y }) => ({ x: (x - 20) / 2, y: (y - 10) / 2 });
  stubDemoFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function expectManifestFields(
  node: Workflow["nodes"][number],
  manifest: NodeManifest,
) {
  expect(node.type).toBe(manifest.id);
  expect(node.label).toBe(manifest.label);
  expect(node.category).toBe("Demonstration");
  expect(node.ports.map((port) => port.id).sort()).toEqual(
    manifest.ports.map((port) => port.id).sort(),
  );
  expect(
    node.parameters.map((parameter) => [parameter.id, parameter.value]),
  ).toEqual(
    manifest.parameters.map((parameter) => [parameter.id, parameter.default]),
  );
  expect(node.pauses_for_review).toBe(manifest.review_behavior === "required");
}

for (const manifest of demoRegistry) {
  test(`demo ${manifest.id} inserts by click`, async () => {
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: new RegExp(manifest.label) }),
    );
    await waitFor(() => {
      expect(readDraftWorkflow().nodes).toHaveLength(1);
    });
    const [node] = readDraftWorkflow().nodes;
    expectManifestFields(node, manifest);
    expect(node.position).toEqual({ x: 90, y: 90 });
    expect(
      await screen.findByRole("heading", { name: manifest.label }),
    ).toBeInTheDocument();
  });

  test(`demo ${manifest.id} inserts by keyboard Enter`, async () => {
    render(<App />);
    const button = await screen.findByRole("button", {
      name: new RegExp(manifest.label),
    });
    fireEvent.keyDown(button, { key: "Enter" });
    await waitFor(() => {
      expect(readDraftWorkflow().nodes).toHaveLength(1);
    });
    const [node] = readDraftWorkflow().nodes;
    expectManifestFields(node, manifest);
    expect(node.position).toEqual({ x: 90, y: 90 });
    expect(
      await screen.findByRole("heading", { name: manifest.label }),
    ).toBeInTheDocument();
  });

  test(`demo ${manifest.id} inserts by keyboard Space`, async () => {
    render(<App />);
    const button = await screen.findByRole("button", {
      name: new RegExp(manifest.label),
    });
    fireEvent.keyDown(button, { key: " " });
    await waitFor(() => {
      expect(readDraftWorkflow().nodes).toHaveLength(1);
    });
    const [node] = readDraftWorkflow().nodes;
    expectManifestFields(node, manifest);
    expect(node.position).toEqual({ x: 90, y: 90 });
    expect(
      await screen.findByRole("heading", { name: manifest.label }),
    ).toBeInTheDocument();
  });

  test(`demo ${manifest.id} inserts by palette drop at the converted position`, async () => {
    render(<App />);
    await screen.findByRole("button", { name: new RegExp(manifest.label) });
    dispatchDrop(
      screen.getByTestId("canvas-drop-zone") as HTMLElement,
      220,
      210,
      manifest.id,
    );
    await waitFor(() => {
      expect(readDraftWorkflow().nodes).toHaveLength(1);
    });
    const [node] = readDraftWorkflow().nodes;
    expectManifestFields(node, manifest);
    expect(node.position).toEqual({ x: 100, y: 100 });
    expect(
      await screen.findByRole("heading", { name: manifest.label }),
    ).toBeInTheDocument();
  });
}

test("palette distinguishes executable demonstrations from EEG examples", async () => {
  render(<App />);
  await screen.findByRole("button", { name: /Copy/ });
  expect(
    screen.getByText(/Demonstration nodes run locally/),
  ).toBeInTheDocument();
  for (const manifest of demoRegistry) {
    expect(
      await screen.findByRole("button", { name: new RegExp(manifest.label) }),
    ).toHaveTextContent("Demonstration");
  }
});
