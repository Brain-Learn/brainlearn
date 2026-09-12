export type ScientificType =
  | "bids_dataset"
  | "raw_eeg"
  | "reviewed_eeg"
  | "epochs"
  | "evoked"
  | "spectrum"
  | "report";

export interface PortDefinition {
  id: string;
  label: string;
  direction: "input" | "output";
  data_type: ScientificType;
  required: boolean;
}

export interface ParameterDefinition {
  id: string;
  label: string;
  value: unknown;
  required: boolean;
  description?: string;
}

export interface WorkflowNode {
  id: string;
  type: string;
  label: string;
  category: string;
  description?: string;
  position: { x: number; y: number };
  ports: PortDefinition[];
  parameters: ParameterDefinition[];
  pauses_for_review: boolean;
}

export interface WorkflowEdge {
  id: string;
  source: { node_id: string; port_id: string };
  target: { node_id: string; port_id: string };
}

export interface Workflow {
  schema_version: "1.0";
  id: string;
  metadata: {
    name: string;
    description: string;
    created_with: string;
    modality: "EEG";
    status: "example" | "experimental" | "certified";
  };
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}
