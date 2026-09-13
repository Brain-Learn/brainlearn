import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Check, Eye, Pause } from "lucide-react";

import { resolvePresentation } from "./graph";
import type { WorkflowNode } from "./types";

export type WorkflowCardData = WorkflowNode &
  Record<string, unknown> & { selected?: boolean; issues?: string[] };
export type WorkflowCardNode = Node<WorkflowCardData, "workflow">;

export function WorkflowCard({ data: node }: NodeProps<WorkflowCardNode>) {
  const inputPorts = node.ports.filter((port) => port.direction === "input");
  const outputPorts = node.ports.filter((port) => port.direction === "output");
  const presentation = resolvePresentation(node);

  return (
    <article
      className={`workflow-card accent-${presentation.accent}${presentation.compact ? " compact" : ""} ${node.selected ? "selected" : ""} ${node.issues?.length ? "invalid" : ""}`}
    >
      {inputPorts.map((port, index) => (
        <Handle
          className="port input-port"
          id={port.id}
          key={port.id}
          position={Position.Left}
          style={{ top: 50 + index * 18 }}
          type="target"
        />
      ))}
      <div className="node-kind">{node.category}</div>
      <div className="node-title">
        {node.pauses_for_review ? <Eye size={15} /> : <Check size={15} />}
        <strong>{presentation.title ?? node.label}</strong>
      </div>
      <div className="node-meta">
        {node.pauses_for_review ? (
          <span className="review">
            <Pause size={11} /> Review checkpoint
          </span>
        ) : (
          <span>Ready</span>
        )}
      </div>
      {outputPorts.map((port, index) => (
        <Handle
          className="port output-port"
          id={port.id}
          key={port.id}
          position={Position.Right}
          style={{ top: 50 + index * 18 }}
          type="source"
        />
      ))}
    </article>
  );
}
