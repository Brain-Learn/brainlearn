import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Activity,
  BookOpen,
  BrainCircuit,
  ChevronRight,
  CircleAlert,
  Database,
  Filter,
  MonitorCog,
  Play,
  Search,
  SlidersHorizontal,
} from "lucide-react";

import type { Workflow, WorkflowNode } from "./types";
import { WorkflowCard, type WorkflowCardNode } from "./WorkflowCard";

const nodeTypes = { workflow: WorkflowCard };

const library = [
  { category: "Input", label: "BIDS EEG", icon: Database },
  { category: "Quality control", label: "Inspect Signal", icon: Activity },
  { category: "Preprocessing", label: "Band-pass Filter", icon: Filter },
  { category: "Quality control", label: "ICA Review", icon: SlidersHorizontal },
  { category: "Output", label: "Report", icon: BookOpen },
];

function Inspector({ node }: { node?: WorkflowNode }) {
  if (!node) {
    return (
      <div className="empty-panel">
        Select a node to inspect its scientific inputs and parameters.
      </div>
    );
  }
  return (
    <div className="inspector-content">
      <div className="eyebrow">{node.category}</div>
      <h2>{node.label}</h2>
      <p>{node.description}</p>
      {node.pauses_for_review && (
        <div className="notice">
          <CircleAlert size={15} /> Requires an explicit researcher decision
        </div>
      )}
      <h3>Parameters</h3>
      {node.parameters.length ? (
        node.parameters.map((parameter) => (
          <label className="parameter" key={parameter.id}>
            <span>{parameter.label}</span>
            <input readOnly value={String(parameter.value ?? "")} />
          </label>
        ))
      ) : (
        <p className="muted">
          This example node has no configurable parameters.
        </p>
      )}
      <h3>Scientific ports</h3>
      {node.ports.map((port) => (
        <div className="port-row" key={port.id}>
          <span>{port.label}</span>
          <code>{port.data_type}</code>
        </div>
      ))}
    </div>
  );
}

function App() {
  const [workflow, setWorkflow] = useState<Workflow>();
  const [selectedId, setSelectedId] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    fetch("/api/workflows/example")
      .then((response) => {
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        return response.json() as Promise<Workflow>;
      })
      .then((result) => {
        setWorkflow(result);
        setSelectedId(result.nodes[2]?.id ?? result.nodes[0]?.id);
      })
      .catch((reason: unknown) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load the example workflow",
        ),
      );
  }, []);

  const nodes = useMemo<WorkflowCardNode[]>(
    () =>
      workflow?.nodes.map((node) => ({
        id: node.id,
        type: "workflow",
        position: node.position,
        data: { ...node, selected: node.id === selectedId },
      })) ?? [],
    [workflow, selectedId],
  );
  const edges = useMemo<Edge[]>(
    () =>
      workflow?.edges.map((edge) => ({
        id: edge.id,
        source: edge.source.node_id,
        sourceHandle: edge.source.port_id,
        target: edge.target.node_id,
        targetHandle: edge.target.port_id,
        markerEnd: { type: MarkerType.ArrowClosed },
      })) ?? [],
    [workflow],
  );
  const selected = workflow?.nodes.find((node) => node.id === selectedId);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <BrainCircuit size={22} /> BrainLearn <span>Research workspace</span>
        </div>
        <div className="workflow-title">
          <span>{workflow?.metadata.name ?? "Loading workflow…"}</span>
          <span className="example-badge">EXAMPLE · NO EXECUTION</span>
        </div>
        <button className="run-button" disabled>
          <Play size={15} /> Run workflow
        </button>
      </header>

      <aside className="library panel">
        <div className="panel-heading">
          <span>Node library</span>
          <ChevronRight size={15} />
        </div>
        <div className="search">
          <Search size={14} />
          <input aria-label="Search nodes" placeholder="Search nodes" />
        </div>
        <div className="library-list">
          {library.map(({ category, label, icon: Icon }) => (
            <button key={label}>
              <Icon size={15} />
              <span>
                <small>{category}</small>
                {label}
              </span>
            </button>
          ))}
        </div>
        <div className="library-note">
          Nodes shown here are interface examples. EEG adapters come after core
          validation.
        </div>
      </aside>

      <section className="canvas" aria-label="Workflow canvas">
        {error ? (
          <div className="load-error">
            <CircleAlert /> Could not load the local API: {error}
          </div>
        ) : (
          <ReactFlowProvider>
            <ReactFlow
              edges={edges}
              fitView
              nodes={nodes}
              nodeTypes={nodeTypes}
              nodesDraggable
              nodesConnectable={false}
              onNodeClick={(_, node) => setSelectedId(node.id)}
            >
              <Background color="#27364a" gap={22} size={1} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </ReactFlowProvider>
        )}
      </section>

      <aside className="inspector panel">
        <div className="panel-heading">
          <span>Inspector</span>
          <MonitorCog size={16} />
        </div>
        <Inspector node={selected} />
      </aside>

      <section className="run-drawer">
        <div>
          <span className="status-dot" /> Ready to validate
        </div>
        <span>
          Execution is intentionally unavailable in this foundation slice.
        </span>
        <button disabled>Open run log</button>
      </section>
    </main>
  );
}

export default App;
