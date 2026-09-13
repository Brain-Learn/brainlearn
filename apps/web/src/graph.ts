import type {
  NodeManifest,
  ParameterDefinition,
  Workflow,
  WorkflowEdge,
  WorkflowNode,
} from "./types";

export function emptyWorkflow(): Workflow {
  return {
    schema_version: "1.0",
    id: "untitled-workflow",
    metadata: {
      name: "Untitled EEG workflow",
      description: "Editable in-memory example graph. No nodes execute.",
      created_with: "BrainLearn 0.2.0",
      modality: "EEG",
      status: "example",
    },
    nodes: [],
    edges: [],
  };
}

export function instantiateNode(
  manifest: NodeManifest,
  id: string,
  position: { x: number; y: number },
): WorkflowNode {
  const parameters: ParameterDefinition[] = manifest.parameters.map(
    (parameter) => ({
      id: parameter.id,
      label: parameter.label,
      value: parameter.default,
      required: parameter.required,
      description: parameter.description,
    }),
  );
  return {
    id,
    type: manifest.id,
    label: manifest.label,
    category: manifest.category,
    description: manifest.description,
    position,
    ports: manifest.ports,
    parameters,
    pauses_for_review: manifest.review_behavior === "required",
  };
}

export function removeNodes(
  workflow: Workflow,
  nodeIds: Set<string>,
): Workflow {
  return {
    ...workflow,
    nodes: workflow.nodes.filter((node) => !nodeIds.has(node.id)),
    edges: workflow.edges.filter(
      (edge) =>
        !nodeIds.has(edge.source.node_id) && !nodeIds.has(edge.target.node_id),
    ),
  };
}

export function removeEdges(
  workflow: Workflow,
  edgeIds: Set<string>,
): Workflow {
  return {
    ...workflow,
    edges: workflow.edges.filter((edge) => !edgeIds.has(edge.id)),
  };
}

export function addEdge(workflow: Workflow, edge: WorkflowEdge): Workflow {
  return { ...workflow, edges: [...workflow.edges, edge] };
}

export function buildConnectionCandidate(
  workflow: Workflow,
  edgeId: string,
  source: string,
  sourcePort: string,
  target: string,
  targetPort: string,
): Workflow {
  return addEdge(workflow, {
    id: edgeId,
    source: { node_id: source, port_id: sourcePort },
    target: { node_id: target, port_id: targetPort },
  });
}

export function updateParameter(
  workflow: Workflow,
  nodeId: string,
  parameterId: string,
  value: unknown,
): Workflow {
  return {
    ...workflow,
    nodes: workflow.nodes.map((node) =>
      node.id === nodeId
        ? {
            ...node,
            parameters: node.parameters.map((parameter) =>
              parameter.id === parameterId
                ? { ...parameter, value }
                : parameter,
            ),
          }
        : node,
    ),
  };
}

export interface CanvasPosition {
  x: number;
  y: number;
}

export function positionsEqual(a: CanvasPosition, b: CanvasPosition): boolean {
  return a.x === b.x && a.y === b.y;
}

export function defaultInsertionPosition(workflow: Workflow): CanvasPosition {
  return {
    x: 90 + ((workflow.nodes.length * 210) % 840),
    y: 90 + (Math.floor(workflow.nodes.length / 4) % 3) * 180,
  };
}

export function insertNodeAt(
  workflow: Workflow,
  manifest: NodeManifest,
  id: string,
  position: CanvasPosition,
): Workflow {
  return {
    ...workflow,
    nodes: [...workflow.nodes, instantiateNode(manifest, id, position)],
  };
}

export function applyPositionChangesToNodes<
  NodeT extends { id: string; position: CanvasPosition },
>(
  nodes: NodeT[],
  changes: Array<{ id: string; position: CanvasPosition }>,
): NodeT[] {
  if (!changes.length) return nodes;
  const nextById = new Map(
    changes.map((change) => [change.id, change.position]),
  );
  let touched = false;
  const next = nodes.map((node) => {
    const position = nextById.get(node.id);
    if (!position || positionsEqual(node.position, position)) return node;
    touched = true;
    return { ...node, position: { ...position } };
  });
  return touched ? next : nodes;
}

export function commitDragPositions(
  workflow: Workflow,
  finalPositions: Record<string, CanvasPosition>,
): { workflow: Workflow; changed: boolean } {
  let changed = false;
  const nodes = workflow.nodes.map((node) => {
    const final = finalPositions[node.id];
    if (!final || positionsEqual(node.position, final)) return node;
    changed = true;
    return { ...node, position: { ...final } };
  });
  if (!changed) return { workflow, changed: false };
  return { workflow: { ...workflow, nodes }, changed: true };
}

export type DragCommitDecision =
  { readonly action: "discard" | "ignore" } | { readonly action: "commit" };

export function decideDragCommit(
  base: Workflow | null,
  latest: Workflow,
  finalPositions: Record<string, CanvasPosition>,
): DragCommitDecision {
  if (base !== null && base !== latest) return { action: "discard" };
  return commitDragPositions(latest, finalPositions).changed
    ? { action: "commit" }
    : { action: "ignore" };
}

export const PALETTE_DRAG_MIME = "application/x-brainlearn-node";

export function encodePaletteDrag(manifestId: string): string {
  return manifestId;
}

export function decodePalettePayload(
  raw: string | null | undefined,
): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (!/^[A-Za-z0-9_.-]+$/.test(trimmed)) return null;
  return trimmed;
}
export function maxIdSuffix(workflow: Workflow): number {
  let max = 0;
  const collect = (id: string) => {
    const match = /-(\d+)$/.exec(id);
    if (match) {
      const value = Number.parseInt(match[1], 10);
      if (Number.isFinite(value) && value > max) max = value;
    }
  };
  for (const node of workflow.nodes) collect(node.id);
  for (const edge of workflow.edges) collect(edge.id);
  return max;
}

export function allocateUniqueId(
  prefix: string,
  workflow: Workflow,
  counter: { current: number },
): string {
  const existing = new Set<string>();
  for (const node of workflow.nodes) existing.add(node.id);
  for (const edge of workflow.edges) existing.add(edge.id);
  let candidate = `${prefix}-${counter.current++}`;
  while (existing.has(candidate)) {
    candidate = `${prefix}-${counter.current++}`;
  }
  return candidate;
}
