import { expect, test } from "vitest";

import {
  allocateUniqueId,
  buildConnectionCandidate,
  emptyWorkflow,
  instantiateNode,
  maxIdSuffix,
  removeEdges,
  removeNodes,
} from "./graph";
import type { NodeManifest } from "./types";

const manifest: NodeManifest = {
  manifest_schema_version: "1.0",
  id: "input.test",
  node_version: "0.1.0",
  label: "Test input",
  description: "Non-executing test node.",
  category: "Input",
  status: "example",
  ports: [
    {
      id: "out",
      label: "Output",
      direction: "output",
      data_type: "raw_eeg",
      required: true,
    },
  ],
  parameters: [],
  review_behavior: "none",
  citations: [],
  license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
  capability_requirements: [],
};

test("creates manifest-backed nodes and connects, disconnects, and removes them", () => {
  const first = instantiateNode(manifest, "first", { x: 0, y: 0 });
  const second = instantiateNode(manifest, "second", { x: 100, y: 0 });
  let workflow = { ...emptyWorkflow(), nodes: [first, second] };
  workflow = buildConnectionCandidate(
    workflow,
    "edge",
    "first",
    "out",
    "second",
    "out",
  );
  expect(workflow.edges).toHaveLength(1);

  workflow = removeEdges(workflow, new Set(["edge"]));
  expect(workflow.edges).toHaveLength(0);

  workflow = removeNodes(workflow, new Set(["first"]));
  expect(workflow.nodes.map((node) => node.id)).toEqual(["second"]);
});

test("derives the id counter from loaded workflows and avoids collisions", () => {
  const loaded = {
    ...emptyWorkflow(),
    nodes: [
      instantiateNode(manifest, "input-test-1", { x: 0, y: 0 }),
      instantiateNode(manifest, "input-test-7", { x: 100, y: 0 }),
    ],
    edges: [
      {
        id: "edge-7",
        source: { node_id: "input-test-1", port_id: "out" },
        target: { node_id: "input-test-7", port_id: "out" },
      },
    ],
  };
  expect(maxIdSuffix(loaded)).toBe(7);

  const counter = { current: maxIdSuffix(loaded) + 1 };
  const first = allocateUniqueId("input-test", loaded, counter);
  expect(first).toBe("input-test-8");
  const withFirst = {
    ...loaded,
    nodes: [...loaded.nodes, instantiateNode(manifest, first, { x: 0, y: 0 })],
  };
  const second = allocateUniqueId("input-test", withFirst, counter);
  expect(second).toBe("input-test-9");
  expect(new Set([first, second]).size).toBe(2);
});
