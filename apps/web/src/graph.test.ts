import { expect, test } from "vitest";

import {
  buildConnectionCandidate,
  emptyWorkflow,
  instantiateNode,
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
