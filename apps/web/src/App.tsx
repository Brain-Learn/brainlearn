import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  BrainCircuit,
  CircleAlert,
  MonitorCog,
  Play,
  Redo2,
  RotateCcw,
  Search,
  Trash2,
  Undo2,
} from "lucide-react";

import {
  fetchExampleWorkflow,
  fetchExampleWorkflows,
  fetchNodeRegistry,
  validateWorkflow,
} from "./api";
import type { ExampleInfo } from "./api";
import {
  NODE_REMOVAL_TRANSITION_MS,
  resolveFitViewDuration,
  usePrefersReducedMotion,
} from "./motion";
import {
  clearDraft,
  loadDraft,
  loadSessionToken,
  saveDraft,
  saveSessionToken,
} from "./persistence";
import {
  createProject,
  listRecentProjects,
  openProject,
  saveProject,
  saveProjectAs,
  type RecentEntry,
} from "./projects";
import {
  allocateUniqueId,
  applyPositionChangesToNodes,
  buildConnectionCandidate,
  commitDragPositions,
  decodePalettePayload,
  decideDragCommit,
  DEFAULT_PRESENTATION_ACCENT,
  defaultInsertionPosition,
  emptyWorkflow,
  encodePaletteDrag,
  hasPresentationOverrides,
  insertNodeAt,
  isPresentationAccent,
  maxIdSuffix,
  PALETTE_DRAG_MIME,
  removeEdges,
  removeNodes,
  resetPresentation,
  updateParameter,
  updatePresentation,
  type CanvasPosition,
} from "./graph";
import type {
  NodeManifest,
  NodePresentation,
  ParameterSchema,
  ValidationIssue,
  ValidationResult,
  Workflow,
  WorkflowNode,
} from "./types";
import {
  PRESENTATION_ACCENTS,
  PRESENTATION_NOTES_MAX_LENGTH,
  PRESENTATION_TITLE_MAX_LENGTH,
} from "./types";
import { WorkflowCard, type WorkflowCardNode } from "./WorkflowCard";

const nodeTypes = { workflow: WorkflowCard };

const NO_LEAVING_NODES: WorkflowNode[] = [];

function toCanvasNodes(
  workflow: Workflow,
  selectedId: string | undefined,
  validation: ValidationResult,
  leaving: WorkflowNode[] = [],
): WorkflowCardNode[] {
  const nodes: WorkflowCardNode[] = workflow.nodes.map((node) => ({
    id: node.id,
    type: "workflow",
    position: { ...node.position },
    data: {
      ...node,
      selected: node.id === selectedId,
      issues: validation.issues
        .filter((issue) => issue.node_id === node.id)
        .map((issue) => issue.message),
    },
  }));
  const liveIds = new Set(workflow.nodes.map((node) => node.id));
  for (const node of leaving) {
    if (liveIds.has(node.id)) continue;
    nodes.push({
      id: node.id,
      type: "workflow",
      position: { ...node.position },
      className: "leaving",
      data: { ...node, selected: false, issues: [] },
    });
  }
  return nodes;
}

function toCanvasEdges(
  workflow: Workflow,
  validation: ValidationResult,
): Edge[] {
  return workflow.edges.map((edge) => {
    const invalid = validation.issues.some(
      (issue) => issue.edge_id === edge.id,
    );
    return {
      id: edge.id,
      source: edge.source.node_id,
      sourceHandle: edge.source.port_id,
      target: edge.target.node_id,
      targetHandle: edge.target.port_id,
      markerEnd: { type: MarkerType.ArrowClosed },
      className: invalid ? "invalid-edge" : undefined,
    };
  });
}

export function WorkflowCanvas({
  workflow,
  selectedId,
  validation,
  registry,
  onSelect,
  onCommitDrag,
  onDropNode,
  onConnect,
  onEdgesDelete,
  onNodesDelete,
  leavingNodes = NO_LEAVING_NODES,
}: {
  workflow: Workflow;
  selectedId: string | undefined;
  validation: ValidationResult;
  registry: NodeManifest[];
  onSelect: (id: string) => void;
  onCommitDrag: (
    base: Workflow,
    positions: Record<string, CanvasPosition>,
  ) => void;
  onDropNode: (manifest: NodeManifest, position: CanvasPosition) => void;
  onConnect: (connection: Connection) => void;
  onEdgesDelete: (deleted: Edge[]) => void;
  onNodesDelete: (deleted: Node[]) => void;
  leavingNodes?: WorkflowNode[];
}) {
  const { screenToFlowPosition } = useReactFlow();
  const reducedMotion = usePrefersReducedMotion();
  const [canvasNodes, setCanvasNodes] = useState<WorkflowCardNode[]>(() =>
    toCanvasNodes(workflow, selectedId, validation, leavingNodes),
  );
  const [isDragOver, setIsDragOver] = useState(false);
  const dragActive = useRef(false);
  const dragBase = useRef<Workflow | null>(null);
  const latest = useRef({ workflow, selectedId, validation, leavingNodes });
  latest.current = { workflow, selectedId, validation, leavingNodes };

  useEffect(() => {
    if (dragActive.current) return;
    setCanvasNodes(
      toCanvasNodes(workflow, selectedId, validation, leavingNodes),
    );
  }, [workflow, selectedId, validation, leavingNodes]);

  const canvasEdges = useMemo(
    () => toCanvasEdges(workflow, validation),
    [workflow, validation],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<WorkflowCardNode>[]) => {
      const positions: Array<{ id: string; position: CanvasPosition }> = [];
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          positions.push({ id: change.id, position: { ...change.position } });
        }
      }
      if (!positions.length) return;
      setCanvasNodes((prev) => applyPositionChangesToNodes(prev, positions));
    },
    [],
  );

  const handleDragStart = useCallback(() => {
    dragActive.current = true;
    dragBase.current = latest.current.workflow;
  }, []);

  const handleDragStop = useCallback(
    (_event: unknown, _node: unknown, nodes: Node[]) => {
      const snapshot = latest.current;
      const base = dragBase.current;
      dragBase.current = null;
      dragActive.current = false;
      const positions: Record<string, CanvasPosition> = {};
      for (const item of nodes) {
        positions[item.id] = { x: item.position.x, y: item.position.y };
      }
      if (
        decideDragCommit(base, snapshot.workflow, positions).action !== "commit"
      ) {
        setCanvasNodes(
          toCanvasNodes(
            snapshot.workflow,
            snapshot.selectedId,
            snapshot.validation,
            snapshot.leavingNodes,
          ),
        );
        return;
      }
      onCommitDrag(base ?? snapshot.workflow, positions);
    },
    [onCommitDrag],
  );

  const handleDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setIsDragOver(false);
      const transfer = event.dataTransfer;
      if (!transfer) return;
      let raw: string | null = null;
      try {
        raw = transfer.getData(PALETTE_DRAG_MIME);
      } catch {
        raw = null;
      }
      if (!raw) {
        try {
          raw = transfer.getData("text/plain");
        } catch {
          raw = null;
        }
      }
      const manifestId = decodePalettePayload(raw);
      if (!manifestId) return;
      const manifest = registry.find((item) => item.id === manifestId);
      if (!manifest) return;
      let position: CanvasPosition;
      try {
        position = screenToFlowPosition({
          x: event.clientX,
          y: event.clientY,
        });
      } catch {
        return;
      }
      if (!Number.isFinite(position.x) || !Number.isFinite(position.y)) return;
      onDropNode(manifest, position);
    },
    [onDropNode, registry, screenToFlowPosition],
  );

  return (
    <div
      className={`canvas-flow${isDragOver ? " drag-over" : ""}`}
      data-testid="canvas-drop-zone"
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={(event) => handleDrop(event)}
    >
      <ReactFlow
        deleteKeyCode={["Backspace", "Delete"]}
        edges={canvasEdges}
        fitView
        fitViewOptions={{ duration: resolveFitViewDuration(reducedMotion) }}
        nodes={canvasNodes}
        nodeTypes={nodeTypes}
        onConnect={onConnect}
        onEdgesDelete={onEdgesDelete}
        onNodeClick={(_, node) => onSelect(node.id)}
        onNodeDragStart={handleDragStart}
        onNodeDragStop={handleDragStop}
        onNodesChange={handleNodesChange}
        onNodesDelete={onNodesDelete}
      >
        <Background color="#27364a" gap={22} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function parameterValue(
  schema: ParameterSchema,
  raw: string,
  checked: boolean,
): unknown {
  if (schema.value_type === "boolean") return checked;
  if (raw === "" && schema.required) return null;
  if (schema.value_type === "number" || schema.value_type === "integer") {
    const number = Number(raw);
    return Number.isFinite(number) ? number : null;
  }
  return raw;
}

function PresentationEditor({
  node,
  onPatch,
  onReset,
}: {
  node: WorkflowNode;
  onPatch: (patch: Partial<NodePresentation>, message: string) => void;
  onReset: () => void;
}) {
  const committedTitle = node.presentation?.title ?? "";
  const committedNotes = node.presentation?.notes ?? "";
  const [titleDraft, setTitleDraft] = useState(committedTitle);
  const [notesDraft, setNotesDraft] = useState(committedNotes);
  useEffect(() => {
    setTitleDraft(committedTitle);
  }, [committedTitle]);
  useEffect(() => {
    setNotesDraft(committedNotes);
  }, [committedNotes]);
  const accent = node.presentation?.accent ?? DEFAULT_PRESENTATION_ACCENT;
  const compact = node.presentation?.compact ?? false;

  const commitTitle = () => {
    const trimmed = titleDraft.trim();
    const next = trimmed === "" ? null : trimmed;
    if (next !== (node.presentation?.title ?? null)) {
      onPatch(
        { title: next },
        next === null ? "Cleared custom title" : "Changed custom title",
      );
    }
  };
  const commitNotes = () => {
    if (notesDraft !== committedNotes) {
      onPatch({ notes: notesDraft }, "Changed display notes");
    }
  };

  return (
    <div>
      <label className="parameter">
        <span>Custom title</span>
        <input
          aria-label="Custom title"
          maxLength={PRESENTATION_TITLE_MAX_LENGTH}
          onBlur={commitTitle}
          onChange={(event) => setTitleDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter")
              (event.target as HTMLInputElement).blur();
          }}
          placeholder={node.label}
          type="text"
          value={titleDraft}
        />
      </label>
      <label className="parameter">
        <span>Accent color</span>
        <select
          aria-label="Accent color"
          onChange={(event) => {
            const next = event.target.value;
            onPatch(
              {
                accent: isPresentationAccent(next)
                  ? next
                  : DEFAULT_PRESENTATION_ACCENT,
              },
              "Changed accent color",
            );
          }}
          value={accent}
        >
          {PRESENTATION_ACCENTS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
      <label className="parameter checkbox">
        <input
          checked={compact}
          onChange={(event) =>
            onPatch(
              { compact: event.target.checked },
              event.target.checked
                ? "Enabled compact display"
                : "Disabled compact display",
            )
          }
          type="checkbox"
        />
        <span>Compact display</span>
      </label>
      <label className="parameter">
        <span>Notes (plain text)</span>
        <textarea
          aria-label="Display notes"
          maxLength={PRESENTATION_NOTES_MAX_LENGTH}
          onBlur={commitNotes}
          onChange={(event) => setNotesDraft(event.target.value)}
          rows={3}
          value={notesDraft}
        />
      </label>
      <button
        className="reset-button"
        disabled={!hasPresentationOverrides(node)}
        onClick={onReset}
      >
        <RotateCcw size={14} /> Reset to manifest defaults
      </button>
    </div>
  );
}

function Inspector({
  node,
  manifest,
  issues,
  onParameterChange,
  onPresentationPatch,
  onPresentationReset,
  onRemove,
}: {
  node?: WorkflowNode;
  manifest?: NodeManifest;
  issues: ValidationIssue[];
  onParameterChange: (
    parameter: ParameterSchema,
    raw: string,
    checked: boolean,
  ) => void;
  onPresentationPatch: (
    patch: Partial<NodePresentation>,
    message: string,
  ) => void;
  onPresentationReset: () => void;
  onRemove: () => void;
}) {
  if (!node || !manifest) {
    return (
      <div className="empty-panel">
        Select a node to inspect its manifest, scientific ports, and parameters.
      </div>
    );
  }
  return (
    <div className="inspector-content">
      <div className="eyebrow">
        {manifest.category} · {manifest.status}
      </div>
      <h2>{manifest.label}</h2>
      <p>{manifest.description}</p>
      <div className="manifest-version">
        Manifest {manifest.manifest_schema_version} · Node{" "}
        {manifest.node_version}
      </div>
      {manifest.review_behavior === "required" && (
        <div className="notice">
          <CircleAlert size={15} /> Requires an explicit researcher decision
        </div>
      )}
      {issues.map((issue) => (
        <div
          className="validation-issue"
          key={`${issue.code}-${issue.message}`}
        >
          {issue.message}
        </div>
      ))}
      <h3>Parameters</h3>
      {manifest.parameters.length ? (
        manifest.parameters.map((schema) => {
          const parameter = node.parameters.find(
            (item) => item.id === schema.id,
          );
          if (schema.value_type === "boolean") {
            return (
              <label className="parameter checkbox" key={schema.id}>
                <input
                  checked={Boolean(parameter?.value)}
                  onChange={(event) =>
                    onParameterChange(
                      schema,
                      event.target.value,
                      event.target.checked,
                    )
                  }
                  type="checkbox"
                />
                <span>{schema.label}</span>
              </label>
            );
          }
          if (schema.options?.length) {
            return (
              <label className="parameter" key={schema.id}>
                <span>{schema.label}</span>
                <select
                  aria-label={schema.label}
                  onChange={(event) =>
                    onParameterChange(schema, event.target.value, false)
                  }
                  value={String(parameter?.value ?? "")}
                >
                  {schema.options.map((option) => (
                    <option key={option}>{option}</option>
                  ))}
                </select>
              </label>
            );
          }
          return (
            <label className="parameter" key={schema.id}>
              <span>{schema.label}</span>
              <input
                aria-label={schema.label}
                max={schema.maximum}
                min={schema.minimum}
                onChange={(event) =>
                  onParameterChange(schema, event.target.value, false)
                }
                required={schema.required}
                type={schema.value_type === "string" ? "text" : "number"}
                value={String(parameter?.value ?? "")}
              />
              {schema.description && <small>{schema.description}</small>}
            </label>
          );
        })
      ) : (
        <p className="muted">
          This example node has no configurable parameters.
        </p>
      )}
      <h3>Presentation</h3>
      <p className="presentation-note">
        Display only — never affects computation identity or cache reuse.
      </p>
      <PresentationEditor
        key={node.id}
        node={node}
        onPatch={onPresentationPatch}
        onReset={onPresentationReset}
      />
      <h3>Scientific ports</h3>
      {manifest.ports.map((port) => (
        <div className="port-row" key={port.id}>
          <span>{port.label}</span>
          <code>{port.data_type}</code>
        </div>
      ))}
      <h3>License and citations</h3>
      <p>
        {manifest.license.spdx_id ?? manifest.license.name} ·{" "}
        {manifest.citations.length} citations
      </p>
      <button className="remove-button" onClick={onRemove}>
        <Trash2 size={14} /> Remove node
      </button>
    </div>
  );
}

function App() {
  const [registry, setRegistry] = useState<NodeManifest[]>([]);
  const [examples, setExamples] = useState<ExampleInfo[]>([]);
  const [exampleMessage, setExampleMessage] = useState("");
  const [workflow, setWorkflow] = useState<Workflow>(() => emptyWorkflow());
  const [past, setPast] = useState<Workflow[]>([]);
  const [future, setFuture] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [validation, setValidation] = useState<ValidationResult>({
    valid: true,
    issues: [],
  });
  const [status, setStatus] = useState("Empty example canvas");
  const [error, setError] = useState<string>();
  const [activeProjectPath, setActiveProjectPath] = useState("");
  const [activeProjectName, setActiveProjectName] = useState("");
  const [folderInput, setFolderInput] = useState("");
  const [nameInput, setNameInput] = useState("Untitled project");
  const [sessionToken, setSessionToken] = useState(() => loadSessionToken());
  const [recent, setRecent] = useState<RecentEntry[]>([]);
  const [projectMessage, setProjectMessage] = useState("");
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  const [leavingNodes, setLeavingNodes] = useState<WorkflowNode[]>([]);
  const reducedMotion = usePrefersReducedMotion();
  const leavingTimer = useRef<number | null>(null);
  const nextId = useRef(1);
  const workflowRev = useRef(0);
  const validationSeq = useRef(0);
  const projectOpSeq = useRef(0);
  const exampleOpSeq = useRef(0);

  const syncIdCounter = useCallback((candidate: Workflow) => {
    nextId.current = Math.max(nextId.current, maxIdSuffix(candidate) + 1);
  }, []);

  useEffect(() => {
    fetchNodeRegistry()
      .then(setRegistry)
      .catch((reason: unknown) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load the node registry",
        ),
      );
  }, []);

  useEffect(() => {
    fetchExampleWorkflows()
      .then(setExamples)
      .catch(() => setExamples([]));
  }, []);

  const refreshValidation = useCallback(async (candidate: Workflow) => {
    const sequence = ++validationSeq.current;
    try {
      const response = await validateWorkflow(candidate);
      if (sequence !== validationSeq.current) return undefined;
      setValidation(response.validation);
      return response;
    } catch (reason) {
      if (sequence !== validationSeq.current) return undefined;
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to validate workflow",
      );
      return undefined;
    }
  }, []);

  useEffect(() => {
    const draft = loadDraft();
    if (draft && (draft.workflow.nodes.length > 0 || draft.projectPath)) {
      setWorkflow(draft.workflow);
      workflowRev.current += 1;
      syncIdCounter(draft.workflow);
      setPast([]);
      setFuture([]);
      if (draft.projectPath) {
        setActiveProjectPath(draft.projectPath);
        setFolderInput(draft.projectPath);
        if (draft.projectName) {
          setActiveProjectName(draft.projectName);
          setNameInput(draft.projectName);
        }
      }
      setDraftNotice(`Recovered unsaved edits from ${draft.savedAt}.`);
      void refreshValidation(draft.workflow);
    }
  }, [refreshValidation, syncIdCounter]);

  useEffect(() => {
    saveDraft({
      savedAt: new Date().toISOString(),
      workflow,
      projectPath: activeProjectPath || null,
      projectName: activeProjectName || null,
    });
  }, [workflow, activeProjectPath, activeProjectName]);

  useEffect(() => {
    saveSessionToken(sessionToken);
  }, [sessionToken]);

  useEffect(
    () => () => {
      if (leavingTimer.current !== null) {
        window.clearTimeout(leavingTimer.current);
      }
    },
    [],
  );

  useEffect(() => {
    setLeavingNodes((prev) => {
      if (prev.length === 0) return prev;
      const next = prev.filter(
        (node) => !workflow.nodes.some((item) => item.id === node.id),
      );
      return next.length === prev.length ? prev : next;
    });
  }, [workflow]);

  const commit = useCallback(
    (next: Workflow, message: string) => {
      setPast((items) => [...items, workflow]);
      setFuture([]);
      setWorkflow(next);
      workflowRev.current += 1;
      syncIdCounter(next);
      setStatus(message);
      void refreshValidation(next);
    },
    [refreshValidation, syncIdCounter, workflow],
  );

  const undo = () => {
    const previous = past.at(-1);
    if (!previous) return;
    setPast((items) => items.slice(0, -1));
    setFuture((items) => [workflow, ...items]);
    setWorkflow(previous);
    workflowRev.current += 1;
    setStatus("Undid graph edit");
    void refreshValidation(previous);
  };

  const redo = () => {
    const next = future[0];
    if (!next) return;
    setFuture((items) => items.slice(1));
    setPast((items) => [...items, workflow]);
    setWorkflow(next);
    workflowRev.current += 1;
    setStatus("Redid graph edit");
    void refreshValidation(next);
  };

  const addNodeAt = useCallback(
    (manifest: NodeManifest, position: CanvasPosition) => {
      const id = allocateUniqueId(
        manifest.id.replaceAll(".", "-"),
        workflow,
        nextId,
      );
      commit(
        insertNodeAt(workflow, manifest, id, position),
        `Added ${manifest.label}`,
      );
      setSelectedId(id);
    },
    [commit, workflow],
  );

  const addManifestNode = useCallback(
    (manifest: NodeManifest, position?: CanvasPosition) => {
      addNodeAt(manifest, position ?? defaultInsertionPosition(workflow));
    },
    [addNodeAt, workflow],
  );

  const handlePaletteDragStart = useCallback(
    (event: React.DragEvent, manifest: NodeManifest) => {
      try {
        event.dataTransfer.setData(
          PALETTE_DRAG_MIME,
          encodePaletteDrag(manifest.id),
        );
        event.dataTransfer.setData("text/plain", manifest.id);
        event.dataTransfer.effectAllowed = "copy";
      } catch {
        // Drag payloads are best-effort; click insertion still works.
      }
    },
    [],
  );

  const handlePaletteKeyDown = useCallback(
    (event: React.KeyboardEvent, manifest: NodeManifest) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        addManifestNode(manifest);
      }
    },
    [addManifestNode],
  );

  const commitDrag = useCallback(
    (base: Workflow, positions: Record<string, CanvasPosition>) => {
      if (base !== workflow) return;
      const result = commitDragPositions(workflow, positions);
      if (!result.changed) return;
      commit(result.workflow, "Moved node");
    },
    [commit, workflow],
  );

  const handleDropNode = useCallback(
    (manifest: NodeManifest, position: CanvasPosition) => {
      addNodeAt(manifest, position);
    },
    [addNodeAt],
  );

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  const handleEdgesDelete = useCallback(
    (deleted: Edge[]) => {
      commit(
        removeEdges(workflow, new Set(deleted.map((edge) => edge.id))),
        "Disconnected ports",
      );
    },
    [commit, workflow],
  );

  const removeNodesWithTransition = useCallback(
    (ids: Set<string>, message: string) => {
      if (ids.size === 0) return;
      const gone = workflow.nodes.filter((node) => ids.has(node.id));
      commit(removeNodes(workflow, ids), message);
      if (gone.length === 0 || reducedMotion) return;
      setLeavingNodes(gone);
      if (leavingTimer.current !== null) {
        window.clearTimeout(leavingTimer.current);
      }
      leavingTimer.current = window.setTimeout(() => {
        setLeavingNodes([]);
        leavingTimer.current = null;
      }, NODE_REMOVAL_TRANSITION_MS);
    },
    [commit, workflow, reducedMotion],
  );

  const handleNodesDelete = useCallback(
    (deleted: Node[]) => {
      const ids = new Set(deleted.map((node) => node.id));
      removeNodesWithTransition(ids, "Removed node");
      setSelectedId((current) =>
        current && ids.has(current) ? undefined : current,
      );
    },
    [removeNodesWithTransition],
  );

  const deleteSelected = () => {
    if (!selectedId) return;
    removeNodesWithTransition(new Set([selectedId]), "Removed selected node");
    setSelectedId(undefined);
  };

  const onConnect = async (connection: Connection) => {
    if (!connection.sourceHandle || !connection.targetHandle) return;
    const revisionAtStart = workflowRev.current;
    const baseWorkflow = workflow;
    const candidate = buildConnectionCandidate(
      baseWorkflow,
      allocateUniqueId("edge", baseWorkflow, nextId),
      connection.source,
      connection.sourceHandle,
      connection.target,
      connection.targetHandle,
    );
    const response = await refreshValidation(candidate);
    if (!response) return;
    if (workflowRev.current !== revisionAtStart) {
      setStatus("Graph changed while validating connection");
      return;
    }
    if (!response.validation.valid) {
      setStatus("Connection rejected by scientific validation");
      return;
    }
    commit(response.workflow, "Connected compatible scientific ports");
  };

  const validateRoundTrip = async () => {
    const response = await refreshValidation(workflow);
    if (!response) return;
    const same = JSON.stringify(response.workflow) === JSON.stringify(workflow);
    setStatus(
      response.validation.valid && same
        ? "Valid graph · API round-trip matched"
        : response.validation.valid
          ? "Valid graph · round-trip changed"
          : "Graph has validation issues",
    );
  };

  const applyProjectPayload = (
    payload: {
      path: string;
      manifest: { name: string };
      workflow: Workflow;
      validation: ValidationResult;
    },
    message: string,
  ) => {
    setWorkflow(payload.workflow);
    workflowRev.current += 1;
    syncIdCounter(payload.workflow);
    setPast([]);
    setFuture([]);
    setActiveProjectPath(payload.path);
    setActiveProjectName(payload.manifest.name);
    setFolderInput(payload.path);
    if (payload.manifest.name) setNameInput(payload.manifest.name);
    setValidation(payload.validation);
    setProjectMessage(message);
    setStatus(message);
  };

  const applyProjectPayloadIfCurrent = (
    payload: {
      path: string;
      manifest: { name: string };
      workflow: Workflow;
      validation: ValidationResult;
    },
    revisionAtStart: number,
    operationAtStart: number,
    message: string,
  ): boolean => {
    if (
      operationAtStart !== projectOpSeq.current ||
      workflowRev.current !== revisionAtStart
    ) {
      const staleMessage =
        "Ignored a stale project response; canvas and project unchanged.";
      setProjectMessage(staleMessage);
      setStatus(staleMessage);
      return false;
    }
    applyProjectPayload(payload, message);
    return true;
  };

  const projectError = (reason: unknown) => {
    setProjectMessage(
      reason instanceof Error ? reason.message : "Project request failed.",
    );
  };

  const handleCreate = async () => {
    const revisionAtStart = workflowRev.current;
    const operationAtStart = ++projectOpSeq.current;
    const snapshot = workflow;
    const destination = folderInput;
    try {
      const payload = await createProject(
        destination,
        nameInput,
        snapshot,
        sessionToken,
      );
      applyProjectPayloadIfCurrent(
        payload,
        revisionAtStart,
        operationAtStart,
        `Created project at ${payload.path}`,
      );
    } catch (reason) {
      projectError(reason);
    }
  };

  const handleOpen = async () => {
    const revisionAtStart = workflowRev.current;
    const operationAtStart = ++projectOpSeq.current;
    const requested = folderInput;
    try {
      const payload = await openProject(requested, sessionToken);
      applyProjectPayloadIfCurrent(
        payload,
        revisionAtStart,
        operationAtStart,
        `Opened project at ${payload.path}`,
      );
    } catch (reason) {
      projectError(reason);
    }
  };

  const handleSave = async () => {
    if (!activeProjectPath) {
      setProjectMessage("Open or create a project before saving.");
      return;
    }
    const revisionAtStart = workflowRev.current;
    const operationAtStart = ++projectOpSeq.current;
    const snapshot = workflow;
    const target = activeProjectPath;
    try {
      const payload = await saveProject(
        target,
        snapshot,
        sessionToken,
        activeProjectName || undefined,
      );
      applyProjectPayloadIfCurrent(
        payload,
        revisionAtStart,
        operationAtStart,
        `Saved project at ${payload.path}`,
      );
    } catch (reason) {
      projectError(reason);
    }
  };

  const handleSaveAs = async () => {
    const revisionAtStart = workflowRev.current;
    const operationAtStart = ++projectOpSeq.current;
    const snapshot = workflow;
    const destination = folderInput;
    try {
      const payload = await saveProjectAs(
        destination,
        snapshot,
        sessionToken,
        nameInput,
      );
      applyProjectPayloadIfCurrent(
        payload,
        revisionAtStart,
        operationAtStart,
        `Saved copy at ${payload.path}`,
      );
    } catch (reason) {
      projectError(reason);
    }
  };

  const handleRecent = async () => {
    try {
      setRecent(await listRecentProjects(sessionToken));
      setProjectMessage("Loaded recent projects.");
    } catch (reason) {
      projectError(reason);
    }
  };

  const handleLoadExample = async (exampleId: string) => {
    const revisionAtStart = workflowRev.current;
    const operationAtStart = ++exampleOpSeq.current;
    try {
      const loaded = await fetchExampleWorkflow(exampleId);
      if (operationAtStart !== exampleOpSeq.current) {
        return;
      }
      if (workflowRev.current !== revisionAtStart) {
        setExampleMessage(
          "Ignored a stale example response; canvas unchanged.",
        );
        return;
      }
      setPast([]);
      setFuture([]);
      setWorkflow(loaded);
      workflowRev.current += 1;
      syncIdCounter(loaded);
      setSelectedId(undefined);
      setLeavingNodes([]);
      setExampleMessage(`Loaded example ${loaded.metadata.name}.`);
      setStatus(`Loaded example ${loaded.metadata.name}`);
      void refreshValidation(loaded);
    } catch (reason) {
      if (operationAtStart !== exampleOpSeq.current) {
        return;
      }
      if (workflowRev.current !== revisionAtStart) {
        setExampleMessage(
          "Ignored a stale example response; canvas unchanged.",
        );
        return;
      }
      setExampleMessage(
        reason instanceof Error
          ? reason.message
          : "Unable to load the example.",
      );
    }
  };

  const dismissDraft = () => {
    clearDraft();
    setDraftNotice(null);
  };

  const selected = workflow.nodes.find((node) => node.id === selectedId);
  const selectedManifest = registry.find(
    (manifest) => manifest.id === selected?.type,
  );
  const selectedIssues = validation.issues.filter(
    (issue) => issue.node_id === selectedId,
  );

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <BrainCircuit size={22} /> BrainLearn <span>Research workspace</span>
        </div>
        <div className="workflow-title">
          <span>{workflow.metadata.name}</span>
          <span className="example-badge">EXAMPLE · NO EXECUTION</span>
        </div>
        <div className="top-actions">
          <button aria-label="Undo" disabled={!past.length} onClick={undo}>
            <Undo2 size={15} />
          </button>
          <button aria-label="Redo" disabled={!future.length} onClick={redo}>
            <Redo2 size={15} />
          </button>
          <button className="run-button" disabled>
            <Play size={15} /> Run workflow
          </button>
        </div>
      </header>

      <aside className="library panel">
        <div className="panel-heading">
          <span>Node registry</span>
          <span>{registry.length}</span>
        </div>
        <div className="search">
          <Search size={14} />
          <input aria-label="Search nodes" placeholder="Search nodes" />
        </div>
        <div className="library-list">
          {registry.map((manifest) => (
            <button
              className="palette-button"
              data-testid={`palette-add-${manifest.id}`}
              draggable
              key={manifest.id}
              onClick={() => addManifestNode(manifest)}
              onDragStart={(event) => handlePaletteDragStart(event, manifest)}
              onKeyDown={(event) => handlePaletteKeyDown(event, manifest)}
            >
              <span>
                <small>{manifest.category} · EXAMPLE</small>
                {manifest.label}
              </span>
            </button>
          ))}
        </div>
        <div className="library-note">
          Select a manifest to add it, drag it onto the canvas at an exact
          position, or focus it and press Enter. EEG nodes are non-executing
          examples; Demonstration nodes run locally as explicitly non-scientific
          examples.
        </div>
        <div className="panel-heading">
          <span>Examples</span>
          <span>{examples.length}</span>
        </div>
        <div className="library-list">
          {examples.map((example) => (
            <button
              data-testid={`example-load-${example.id}`}
              key={example.id}
              onClick={() => void handleLoadExample(example.id)}
              title={example.description}
            >
              <span>
                <small>EXAMPLE · v{example.schema_version}</small>
                {example.name}
              </span>
            </button>
          ))}
        </div>
        {exampleMessage && (
          <div className="project-message" role="status">
            {exampleMessage}
          </div>
        )}
        <div className="panel-heading">
          <span>Local project</span>
        </div>
        <div className="project-panel">
          <div className="project-message" role="status">
            Active project: {activeProjectPath || "none — open or create one"}
          </div>
          <label className="parameter">
            <span>Session token</span>
            <input
              aria-label="Session token"
              onChange={(event) => setSessionToken(event.target.value)}
              placeholder="Paste token from service terminal"
              type="password"
              value={sessionToken}
            />
          </label>
          <label className="parameter">
            <span>Project folder (absolute path)</span>
            <input
              aria-label="Project folder"
              onChange={(event) => setFolderInput(event.target.value)}
              placeholder="/Users/researcher/brainlearn-demo"
              type="text"
              value={folderInput}
            />
          </label>
          <label className="parameter">
            <span>Project name</span>
            <input
              aria-label="Project name"
              onChange={(event) => setNameInput(event.target.value)}
              type="text"
              value={nameInput}
            />
          </label>
          <div className="project-buttons">
            <button onClick={() => void handleCreate()}>Create</button>
            <button onClick={() => void handleOpen()}>Open</button>
            <button
              disabled={!activeProjectPath}
              onClick={() => void handleSave()}
              title={
                activeProjectPath
                  ? `Save to ${activeProjectPath}`
                  : "Open or create a project before saving"
              }
            >
              Save
            </button>
            <button onClick={() => void handleSaveAs()}>Save as</button>
            <button onClick={() => void handleRecent()}>Recent</button>
          </div>
          {projectMessage && (
            <div className="project-message">{projectMessage}</div>
          )}
          {recent.length > 0 && (
            <ul className="recent-list">
              {recent.map((entry) => (
                <li key={entry.path}>
                  <button
                    onClick={() => {
                      setFolderInput(entry.path);
                      if (entry.name) setNameInput(entry.name);
                    }}
                  >
                    {entry.name || entry.path}
                  </button>
                  <small>{entry.path}</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>

      <section className="canvas" aria-label="Workflow canvas">
        {draftNotice && (
          <div className="draft-banner" role="status">
            <span>{draftNotice} Unsaved edits recover after reload.</span>
            <button onClick={dismissDraft}>Discard</button>
          </div>
        )}
        {error ? (
          <div className="load-error">
            <CircleAlert /> {error}
          </div>
        ) : (
          <ReactFlowProvider>
            <WorkflowCanvas
              onCommitDrag={commitDrag}
              onConnect={(connection) => void onConnect(connection)}
              onDropNode={handleDropNode}
              onEdgesDelete={handleEdgesDelete}
              onNodesDelete={handleNodesDelete}
              onSelect={handleSelect}
              registry={registry}
              selectedId={selectedId}
              validation={validation}
              workflow={workflow}
              leavingNodes={leavingNodes}
            />
          </ReactFlowProvider>
        )}
        {!workflow.nodes.length && !error && (
          <div className="empty-canvas">
            <BrainCircuit size={34} />
            <strong>Build an example EEG graph</strong>
            <span>
              Add nodes from the backend registry, then connect matching port
              types.
            </span>
          </div>
        )}
      </section>

      <aside className="inspector panel">
        <div className="panel-heading">
          <span>Inspector</span>
          <MonitorCog size={16} />
        </div>
        <Inspector
          key={selected?.id ?? "none"}
          issues={selectedIssues}
          manifest={selectedManifest}
          node={selected}
          onParameterChange={(schema, raw, checked) => {
            if (!selected) return;
            commit(
              updateParameter(
                workflow,
                selected.id,
                schema.id,
                parameterValue(schema, raw, checked),
              ),
              `Changed ${schema.label}`,
            );
          }}
          onPresentationPatch={(patch, message) => {
            if (!selected) return;
            commit(updatePresentation(workflow, selected.id, patch), message);
          }}
          onPresentationReset={() => {
            if (!selected) return;
            commit(
              resetPresentation(workflow, selected.id),
              "Reset presentation to manifest defaults",
            );
          }}
          onRemove={deleteSelected}
        />
      </aside>

      <section className="run-drawer">
        <div>
          <span className={`status-dot ${validation.valid ? "" : "invalid"}`} />{" "}
          {status}
        </div>
        <div className="issue-summary">
          {validation.issues.length
            ? validation.issues.map((issue) => (
                <span key={`${issue.code}-${issue.message}`}>
                  {issue.message}
                </span>
              ))
            : "No backend validation issues"}
        </div>
        <button onClick={() => void validateRoundTrip()}>Validate graph</button>
      </section>
    </main>
  );
}

export default App;
