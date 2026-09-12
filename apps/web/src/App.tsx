import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  BrainCircuit,
  CircleAlert,
  MonitorCog,
  Play,
  Redo2,
  Search,
  Trash2,
  Undo2,
} from "lucide-react";

import { fetchNodeRegistry, validateWorkflow } from "./api";
import {
  buildConnectionCandidate,
  emptyWorkflow,
  instantiateNode,
  removeEdges,
  removeNodes,
  updateParameter,
} from "./graph";
import type {
  NodeManifest,
  ParameterSchema,
  ValidationIssue,
  ValidationResult,
  Workflow,
  WorkflowNode,
} from "./types";
import { WorkflowCard, type WorkflowCardNode } from "./WorkflowCard";

const nodeTypes = { workflow: WorkflowCard };

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

function Inspector({
  node,
  manifest,
  issues,
  onParameterChange,
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
  const nextId = useRef(1);

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

  const refreshValidation = useCallback(async (candidate: Workflow) => {
    try {
      const response = await validateWorkflow(candidate);
      setValidation(response.validation);
      return response;
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to validate workflow",
      );
      return undefined;
    }
  }, []);

  const commit = useCallback(
    (next: Workflow, message: string) => {
      setPast((items) => [...items, workflow]);
      setFuture([]);
      setWorkflow(next);
      setStatus(message);
      void refreshValidation(next);
    },
    [refreshValidation, workflow],
  );

  const undo = () => {
    const previous = past.at(-1);
    if (!previous) return;
    setPast((items) => items.slice(0, -1));
    setFuture((items) => [workflow, ...items]);
    setWorkflow(previous);
    setStatus("Undid graph edit");
    void refreshValidation(previous);
  };

  const redo = () => {
    const next = future[0];
    if (!next) return;
    setFuture((items) => items.slice(1));
    setPast((items) => [...items, workflow]);
    setWorkflow(next);
    setStatus("Redid graph edit");
    void refreshValidation(next);
  };

  const addManifestNode = (manifest: NodeManifest) => {
    const ordinal = nextId.current++;
    const id = `${manifest.id.replaceAll(".", "-")}-${ordinal}`;
    const node = instantiateNode(manifest, id, {
      x: 90 + ((workflow.nodes.length * 210) % 840),
      y: 90 + (Math.floor(workflow.nodes.length / 4) % 3) * 180,
    });
    commit(
      { ...workflow, nodes: [...workflow.nodes, node] },
      `Added ${manifest.label}`,
    );
    setSelectedId(id);
  };

  const deleteSelected = () => {
    if (!selectedId) return;
    commit(
      removeNodes(workflow, new Set([selectedId])),
      "Removed selected node",
    );
    setSelectedId(undefined);
  };

  const onConnect = async (connection: Connection) => {
    if (!connection.sourceHandle || !connection.targetHandle) return;
    const candidate = buildConnectionCandidate(
      workflow,
      `edge-${nextId.current++}`,
      connection.source,
      connection.sourceHandle,
      connection.target,
      connection.targetHandle,
    );
    const response = await refreshValidation(candidate);
    if (!response) return;
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

  const nodes = useMemo<WorkflowCardNode[]>(
    () =>
      workflow.nodes.map((node) => ({
        id: node.id,
        type: "workflow",
        position: node.position,
        data: {
          ...node,
          selected: node.id === selectedId,
          issues: validation.issues
            .filter((issue) => issue.node_id === node.id)
            .map((issue) => issue.message),
        },
      })),
    [workflow.nodes, selectedId, validation.issues],
  );
  const edges = useMemo<Edge[]>(
    () =>
      workflow.edges.map((edge) => {
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
      }),
    [workflow.edges, validation.issues],
  );
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
            <button key={manifest.id} onClick={() => addManifestNode(manifest)}>
              <span>
                <small>{manifest.category} · EXAMPLE</small>
                {manifest.label}
              </span>
            </button>
          ))}
        </div>
        <div className="library-note">
          Select a manifest to add it. All registry nodes are non-executing
          examples.
        </div>
      </aside>

      <section className="canvas" aria-label="Workflow canvas">
        {error ? (
          <div className="load-error">
            <CircleAlert /> {error}
          </div>
        ) : (
          <ReactFlowProvider>
            <ReactFlow
              deleteKeyCode={["Backspace", "Delete"]}
              edges={edges}
              fitView
              nodes={nodes}
              nodeTypes={nodeTypes}
              onConnect={(connection) => void onConnect(connection)}
              onEdgesDelete={(deleted) =>
                commit(
                  removeEdges(
                    workflow,
                    new Set(deleted.map((edge) => edge.id)),
                  ),
                  "Disconnected ports",
                )
              }
              onNodeClick={(_, node) => setSelectedId(node.id)}
              onNodeDragStop={(_, node) =>
                commit(
                  {
                    ...workflow,
                    nodes: workflow.nodes.map((item) =>
                      item.id === node.id
                        ? { ...item, position: node.position }
                        : item,
                    ),
                  },
                  "Moved node",
                )
              }
              onNodesDelete={(deleted: Node[]) => {
                const ids = new Set(deleted.map((node) => node.id));
                commit(removeNodes(workflow, ids), "Removed node");
                if (selectedId && ids.has(selectedId)) setSelectedId(undefined);
              }}
            >
              <Background color="#27364a" gap={22} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>
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
