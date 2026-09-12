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

export interface ParameterSchema {
  id: string;
  label: string;
  value_type: "string" | "number" | "integer" | "boolean";
  default: unknown;
  required: boolean;
  description?: string;
  minimum?: number;
  maximum?: number;
  options?: string[];
}

export interface NodeManifest {
  manifest_schema_version: "1.0";
  id: string;
  node_version: string;
  label: string;
  description: string;
  category: string;
  status: "example" | "experimental" | "certified";
  ports: PortDefinition[];
  parameters: ParameterSchema[];
  review_behavior: "none" | "required";
  citations: Array<{ title: string; doi?: string; url?: string }>;
  license: { name: string; spdx_id?: string; url?: string };
  capability_requirements: Array<{
    id: string;
    required: boolean;
    description: string;
  }>;
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

export interface ValidationIssue {
  code: string;
  message: string;
  node_id?: string;
  edge_id?: string;
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
}

export interface WorkflowValidationResponse {
  workflow: Workflow;
  validation: ValidationResult;
}
