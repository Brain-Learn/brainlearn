import { expect, test } from "vitest";

import {
  allocateUniqueId,
  applyPositionChangesToNodes,
  buildConnectionCandidate,
  commitDragPositions,
  decodePalettePayload,
  decideDragCommit,
  defaultInsertionPosition,
  emptyWorkflow,
  insertNodeAt,
  instantiateNode,
  maxIdSuffix,
  positionsEqual,
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

test("applies transient position changes without touching workflow identity", () => {
  const nodes = [
    { id: "a", position: { x: 0, y: 0 } },
    { id: "b", position: { x: 10, y: 10 } },
  ];
  const moved = applyPositionChangesToNodes(nodes, [
    { id: "a", position: { x: 5, y: 7 } },
    { id: "a", position: { x: 9, y: 11 } },
  ]);
  expect(moved.find((node) => node.id === "a")?.position).toEqual({
    x: 9,
    y: 11,
  });
  expect(moved.find((node) => node.id === "b")?.position).toEqual({
    x: 10,
    y: 10,
  });
  expect(nodes[0].position).toEqual({ x: 0, y: 0 });
});

test("ignores empty or equal transient changes", () => {
  const nodes = [{ id: "a", position: { x: 1, y: 2 } }];
  expect(applyPositionChangesToNodes(nodes, [])).toBe(nodes);
  expect(
    applyPositionChangesToNodes(nodes, [{ id: "a", position: { x: 1, y: 2 } }]),
  ).toBe(nodes);
  expect(
    applyPositionChangesToNodes(nodes, [
      { id: "missing", position: { x: 9, y: 9 } },
    ]),
  ).toBe(nodes);
});

test("drag commit copies exact final positions once and detects no-movement clicks", () => {
  const workflow = {
    ...emptyWorkflow(),
    nodes: [instantiateNode(manifest, "n1", { x: 0, y: 0 })],
  };
  const same = commitDragPositions(workflow, { n1: { x: 0, y: 0 } });
  expect(same.changed).toBe(false);
  expect(same.workflow).toBe(workflow);

  const moved = commitDragPositions(workflow, { n1: { x: 33.5, y: -12 } });
  expect(moved.changed).toBe(true);
  expect(moved.workflow.nodes[0].position).toEqual({ x: 33.5, y: -12 });
  expect(workflow.nodes[0].position).toEqual({ x: 0, y: 0 });
  expect(positionsEqual({ x: 1, y: 2 }, { x: 1, y: 2 })).toBe(true);
  expect(positionsEqual({ x: 1, y: 2 }, { x: 1, y: 3 })).toBe(false);
});

test("click, keyboard, and drop insertion share fields except position", () => {
  const base = emptyWorkflow();
  const counter = { current: 1 };
  const clickId = allocateUniqueId("input-test", base, counter);
  const clicked = insertNodeAt(
    base,
    manifest,
    clickId,
    defaultInsertionPosition(base),
  );
  const dropped = insertNodeAt(base, manifest, clickId, { x: 321, y: 654 });
  const { position: clickPosition, ...clickRest } = clicked.nodes[0];
  const { position: dropPosition, ...dropRest } = dropped.nodes[0];
  expect(clickRest).toEqual(dropRest);
  expect(clickPosition).toEqual(defaultInsertionPosition(base));
  expect(dropPosition).toEqual({ x: 321, y: 654 });
});

test("drag commit decisions discard replaced workflows and ignore no-movement stops", () => {
  const base = {
    ...emptyWorkflow(),
    nodes: [instantiateNode(manifest, "n1", { x: 0, y: 0 })],
  };
  const differentIds = {
    ...emptyWorkflow(),
    nodes: [instantiateNode(manifest, "n2", { x: 50, y: 50 })],
  };
  const overlappingIds = {
    ...emptyWorkflow(),
    nodes: [instantiateNode(manifest, "n1", { x: 400, y: 400 })],
  };
  expect(
    decideDragCommit(base, differentIds, { n1: { x: 33, y: 44 } }).action,
  ).toBe("discard");
  expect(
    decideDragCommit(base, overlappingIds, { n1: { x: 34, y: 45 } }).action,
  ).toBe("discard");
  expect(decideDragCommit(base, base, { n1: { x: 0, y: 0 } }).action).toBe(
    "ignore",
  );
  expect(decideDragCommit(null, base, { n1: { x: 0, y: 0 } }).action).toBe(
    "ignore",
  );
  expect(decideDragCommit(base, base, { n1: { x: 33.5, y: -12 } }).action).toBe(
    "commit",
  );
  expect(decideDragCommit(null, base, { n1: { x: 33.5, y: -12 } }).action).toBe(
    "commit",
  );
});

test("palette payload decoding rejects invalid and missing payloads", () => {
  expect(decodePalettePayload("input.bids_eeg")).toBe("input.bids_eeg");
  expect(decodePalettePayload("  input-test_1.2  ")).toBe("input-test_1.2");
  expect(decodePalettePayload(null)).toBeNull();
  expect(decodePalettePayload(undefined)).toBeNull();
  expect(decodePalettePayload("")).toBeNull();
  expect(decodePalettePayload("   ")).toBeNull();
  expect(decodePalettePayload("../evil")).toBeNull();
  expect(decodePalettePayload("has space")).toBeNull();
  expect(decodePalettePayload('{"id":"x"}')).toBeNull();
});
