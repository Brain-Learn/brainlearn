import "@testing-library/jest-dom/vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { WorkflowCanvas } from "./App";
import { commitDragPositions, type CanvasPosition } from "./graph";
import type { NodeManifest, ValidationResult, Workflow } from "./types";

const mocked = vi.hoisted(() => ({
  lastProps: null as {
    nodes: Array<{
      id: string;
      position: CanvasPosition;
      data: { selected?: boolean };
    }>;
    onNodeDragStart: () => void;
    onNodeDragStop: (
      event: unknown,
      node: unknown,
      nodes: Array<{ id: string; position: CanvasPosition }>,
    ) => void;
  } | null,
  converter: (point: { x: number; y: number }): CanvasPosition => ({
    ...point,
  }),
}));

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...actual,
    ReactFlow: (props: {
      nodes: Array<{ id: string; position: CanvasPosition }>;
      onNodeDragStart: () => void;
      onNodeDragStop: (
        event: unknown,
        node: unknown,
        nodes: Array<{ id: string; position: CanvasPosition }>,
      ) => void;
    }) => {
      mocked.lastProps = props as typeof mocked.lastProps;
      return <div data-testid="rf-stub" />;
    },
    Background: () => null,
    Controls: () => null,
    useReactFlow: () => ({
      screenToFlowPosition: (point: { x: number; y: number }) =>
        mocked.converter(point),
    }),
  };
});

const manifest: NodeManifest = {
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
  parameters: [],
  review_behavior: "none",
  citations: [],
  license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
  capability_requirements: [],
};

function makeWorkflow(
  id: string,
  nodes: Array<{ id: string; x: number; y: number }>,
): Workflow {
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
    nodes: nodes.map((node) => ({
      id: node.id,
      type: "input.bids_eeg",
      label: "BIDS EEG",
      category: "Input",
      position: { x: node.x, y: node.y },
      ports: manifest.ports,
      parameters: [],
      pauses_for_review: false,
    })),
    edges: [],
  };
}

const validation: ValidationResult = { valid: true, issues: [] };

function Harness({
  initial,
  replacement,
  onDrop,
}: {
  initial: Workflow;
  replacement: Workflow;
  onDrop: (manifest: NodeManifest, position: CanvasPosition) => void;
}) {
  const [workflow, setWorkflow] = useState(initial);
  const [past, setPast] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;
  const commitDrag = (
    base: Workflow,
    positions: Record<string, CanvasPosition>,
  ) => {
    if (base !== workflowRef.current) return;
    const result = commitDragPositions(workflowRef.current, positions);
    if (!result.changed) return;
    setPast((items) => [...items, workflowRef.current]);
    setWorkflow(result.workflow);
  };
  return (
    <div>
      <button onClick={() => setWorkflow(replacement)}>replace-workflow</button>
      <button onClick={() => setSelectedId("n1")}>select-n1</button>
      <span data-testid="past-count">{past.length}</span>
      <span data-testid="node-ids">
        {workflow.nodes.map((node) => node.id).join(",")}
      </span>
      <WorkflowCanvas
        workflow={workflow}
        selectedId={selectedId}
        validation={validation}
        registry={[manifest]}
        onSelect={setSelectedId}
        onCommitDrag={commitDrag}
        onDropNode={onDrop}
        onConnect={() => {}}
        onEdgesDelete={() => {}}
        onNodesDelete={() => {}}
      />
    </div>
  );
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

function dragStop(nodes: Array<{ id: string; position: CanvasPosition }>) {
  const props = mocked.lastProps;
  if (!props) throw new Error("Expected captured ReactFlow props");
  act(() => {
    props.onNodeDragStop({}, { id: nodes[0]?.id }, nodes);
  });
}

function dragStart() {
  const props = mocked.lastProps;
  if (!props) throw new Error("Expected captured ReactFlow props");
  act(() => {
    props.onNodeDragStart();
  });
}

beforeEach(() => {
  mocked.lastProps = null;
  mocked.converter = (point) => ({ ...point });
});

afterEach(() => {
  cleanup();
});

test("a mid-drag replacement with different node IDs wins without an undo entry", () => {
  const drops: Array<{ manifest: NodeManifest; position: CanvasPosition }> = [];
  render(
    <Harness
      initial={makeWorkflow("base", [{ id: "n1", x: 0, y: 0 }])}
      replacement={makeWorkflow("replacement", [{ id: "n2", x: 50, y: 50 }])}
      onDrop={(item, position) => drops.push({ manifest: item, position })}
    />,
  );
  dragStart();
  fireEvent.click(screen.getByRole("button", { name: "replace-workflow" }));
  dragStop([{ id: "n1", position: { x: 99, y: 99 } }]);

  expect(screen.getByTestId("past-count").textContent).toBe("0");
  expect(screen.getByTestId("node-ids").textContent).toBe("n2");
  expect(mocked.lastProps?.nodes.map((node) => node.id)).toEqual(["n2"]);
  expect(mocked.lastProps?.nodes[0].position).toEqual({ x: 50, y: 50 });
});

test("a mid-drag replacement with overlapping node IDs keeps the replacement position", () => {
  render(
    <Harness
      initial={makeWorkflow("base", [{ id: "n1", x: 0, y: 0 }])}
      replacement={makeWorkflow("replacement", [{ id: "n1", x: 400, y: 400 }])}
      onDrop={() => {}}
    />,
  );
  dragStart();
  fireEvent.click(screen.getByRole("button", { name: "replace-workflow" }));
  dragStop([{ id: "n1", position: { x: 99, y: 99 } }]);

  expect(screen.getByTestId("past-count").textContent).toBe("0");
  expect(mocked.lastProps?.nodes).toHaveLength(1);
  expect(mocked.lastProps?.nodes[0].position).toEqual({ x: 400, y: 400 });
});

test("a no-movement stop after a suppressed update resyncs without committing", () => {
  render(
    <Harness
      initial={makeWorkflow("base", [{ id: "n1", x: 0, y: 0 }])}
      replacement={makeWorkflow("replacement", [{ id: "n1", x: 0, y: 0 }])}
      onDrop={() => {}}
    />,
  );
  dragStart();
  fireEvent.click(screen.getByRole("button", { name: "select-n1" }));
  dragStop([{ id: "n1", position: { x: 0, y: 0 } }]);

  expect(screen.getByTestId("past-count").textContent).toBe("0");
  expect(mocked.lastProps?.nodes[0].data.selected).toBe(true);
});

test("an ordinary drag still commits once when the workflow is unchanged", () => {
  render(
    <Harness
      initial={makeWorkflow("base", [{ id: "n1", x: 0, y: 0 }])}
      replacement={makeWorkflow("replacement", [{ id: "n1", x: 0, y: 0 }])}
      onDrop={() => {}}
    />,
  );
  dragStart();
  dragStop([{ id: "n1", position: { x: 25, y: 75 } }]);

  expect(screen.getByTestId("past-count").textContent).toBe("1");
  expect(mocked.lastProps?.nodes[0].position).toEqual({ x: 25, y: 75 });
});

test("a converter failure inserts nothing", () => {
  const drops: Array<{ manifest: NodeManifest; position: CanvasPosition }> = [];
  mocked.converter = () => {
    throw new Error("viewport unavailable");
  };
  render(
    <Harness
      initial={makeWorkflow("base", [])}
      replacement={makeWorkflow("replacement", [])}
      onDrop={(item, position) => drops.push({ manifest: item, position })}
    />,
  );
  dispatchDrop(
    screen.getByTestId("canvas-drop-zone") as HTMLElement,
    300,
    250,
    "input.bids_eeg",
  );

  expect(drops).toHaveLength(0);
});

test("a transformed-viewport drop uses the converter path", () => {
  const drops: Array<{ manifest: NodeManifest; position: CanvasPosition }> = [];
  mocked.converter = ({ x, y }) => ({ x: (x - 20) / 2, y: (y - 10) / 2 });
  render(
    <Harness
      initial={makeWorkflow("base", [])}
      replacement={makeWorkflow("replacement", [])}
      onDrop={(item, position) => drops.push({ manifest: item, position })}
    />,
  );
  dispatchDrop(
    screen.getByTestId("canvas-drop-zone") as HTMLElement,
    220,
    210,
    "input.bids_eeg",
  );

  expect(drops).toHaveLength(1);
  expect(drops[0].manifest.id).toBe("input.bids_eeg");
  expect(drops[0].position).toEqual({ x: 100, y: 100 });
});
