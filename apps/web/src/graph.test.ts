import { expect, test } from "vitest";

import {
  allocateUniqueId,
  applyPositionChangesToNodes,
  buildConnectionCandidate,
  commitDragPositions,
  decodePalettePayload,
  decideDragCommit,
  defaultInsertionPosition,
  defaultPresentation,
  emptyWorkflow,
  hasPresentationOverrides,
  insertNodeAt,
  instantiateNode,
  isPresentationAccent,
  maxIdSuffix,
  positionsEqual,
  removeEdges,
  removeNodes,
  resetPresentation,
  resolvePresentation,
  updatePresentation,
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

test("new nodes omit presentation and resolve to manifest defaults", () => {
  const node = instantiateNode(manifest, "n1", { x: 0, y: 0 });
  expect(node.presentation).toBeUndefined();
  expect(resolvePresentation(node)).toEqual(defaultPresentation());
  expect(hasPresentationOverrides(node)).toBe(false);
});

test("presentation resolution falls back to defaults for missing or invalid values", () => {
  expect(resolvePresentation({})).toEqual({
    title: null,
    accent: "teal",
    compact: false,
    notes: "",
  });
  expect(
    resolvePresentation({
      presentation: {
        title: "  ",
        accent: "neon",
        compact: 1,
        notes: 7,
      } as unknown as {
        title: string | null;
        accent: "teal";
        compact: boolean;
        notes: string;
      },
    }),
  ).toEqual({ title: null, accent: "teal", compact: false, notes: "" });
  expect(isPresentationAccent("violet")).toBe(true);
  expect(isPresentationAccent("neon")).toBe(false);
  expect(isPresentationAccent(null)).toBe(false);
});

test("presentation updates normalize bounds and keep scientific fields untouched", () => {
  const base = {
    ...emptyWorkflow(),
    nodes: [instantiateNode(manifest, "n1", { x: 0, y: 0 })],
  };
  const renamed = updatePresentation(base, "n1", { title: "  My step  " });
  expect(renamed.nodes[0].presentation).toMatchObject({ title: "My step" });
  expect(renamed.nodes[0].type).toBe("input.test");
  expect(renamed.nodes[0].ports).toBe(base.nodes[0].ports);
  expect(renamed.nodes[0].parameters).toBe(base.nodes[0].parameters);

  const long = updatePresentation(base, "n1", {
    title: "t".repeat(200),
    accent: "rose",
    compact: true,
    notes: "n".repeat(5000),
  });
  expect(long.nodes[0].presentation?.title).toHaveLength(80);
  expect(long.nodes[0].presentation?.notes).toHaveLength(2000);
  expect(hasPresentationOverrides(long.nodes[0])).toBe(true);

  const cleared = updatePresentation(long, "n1", {
    title: null,
    accent: "teal",
    compact: false,
    notes: "",
  });
  expect(cleared.nodes[0].presentation).toBeUndefined();

  const withTitle = updatePresentation(base, "n1", { title: "Keep me" });
  const invalidAccent = updatePresentation(withTitle, "n1", {
    accent: "neon" as "teal",
  });
  expect(invalidAccent.nodes[0].presentation).toMatchObject({
    title: "Keep me",
    accent: "teal",
  });
});

test("presentation reset removes overrides and leaves other nodes alone", () => {
  const first = instantiateNode(manifest, "n1", { x: 0, y: 0 });
  const second = instantiateNode(manifest, "n2", { x: 10, y: 10 });
  const base = { ...emptyWorkflow(), nodes: [first, second] };
  const customized = updatePresentation(base, "n1", { title: "Custom" });
  expect(customized.nodes[0].presentation?.title).toBe("Custom");

  const reset = resetPresentation(customized, "n1");
  expect(reset.nodes[0].presentation).toBeUndefined();
  expect(reset.nodes[1]).toBe(second);

  const untouched = resetPresentation(reset, "n1");
  expect(untouched).toEqual(reset);
});

test("nullable wire presentation resolves to defaults and stays editable", () => {
  const node = {
    ...instantiateNode(manifest, "n1", { x: 0, y: 0 }),
    presentation: null,
  };
  expect(resolvePresentation(node)).toEqual(defaultPresentation());
  expect(hasPresentationOverrides(node)).toBe(false);

  const edited = updatePresentation(
    { ...emptyWorkflow(), nodes: [node] },
    "n1",
    { title: "From null" },
  );
  expect(edited.nodes[0].presentation?.title).toBe("From null");

  const reset = resetPresentation({ ...emptyWorkflow(), nodes: [node] }, "n1");
  expect(reset.nodes[0].presentation).toBeUndefined();
});
