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

export const PRESENTATION_ACCENTS = [
  "teal",
  "blue",
  "violet",
  "amber",
  "rose",
  "slate",
] as const;

export type PresentationAccent = (typeof PRESENTATION_ACCENTS)[number];

export const PRESENTATION_TITLE_MAX_LENGTH = 80;
export const PRESENTATION_NOTES_MAX_LENGTH = 2000;

export interface NodePresentation {
  title: string | null;
  accent: PresentationAccent;
  compact: boolean;
  notes: string;
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
  presentation?: NodePresentation | null;
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

export type RunState =
  | "queued"
  | "running"
  | "waiting_for_review"
  | "succeeded"
  | "failed"
  | "cancelled";

export type NodeRunState = RunState | "dependency_skipped" | "cache_reused";

export type RunEventKind =
  | "run_queued"
  | "run_started"
  | "node_queued"
  | "node_started"
  | "node_succeeded"
  | "node_failed"
  | "node_skipped"
  | "node_cancelled"
  | "cache_reused"
  | "review_requested"
  | "review_decided"
  | "run_succeeded"
  | "run_failed"
  | "run_cancelled";

export interface RunEvent {
  schema_version: "1.0";
  seq: number;
  at: string;
  kind: RunEventKind;
  node_run_id: string | null;
  attempt: number | null;
  message: string;
}

export interface ArtifactRecord {
  schema_version: "1.0";
  artifact_id: string;
  path: string;
  media_type: string;
  byte_size: number;
  sha256: string;
  produced_by_node: string;
  port_id: string;
}

export interface FailureRecord {
  schema_version: "1.0";
  code: string;
  message: string;
  node_run_id: string | null;
  at: string;
}

export interface ReviewPauseRecord {
  schema_version: "1.0";
  id: string;
  node_run_id: string;
  input_identity: string;
  requested_at: string;
  decided_at: string | null;
  decision: "approved" | "rejected" | null;
  note: string;
}

export interface EnvironmentRecord {
  schema_version: "1.0";
  operating_system: string;
  architecture: string;
  python_version: string;
  packages: Record<string, string>;
  accelerator: string;
}

export interface NodeRunRecord {
  schema_version: "1.0";
  id: string;
  node_id: string;
  node_type: string;
  node_version: string;
  dependencies: string[];
  attempt: number;
  state: NodeRunState;
  started_at: string | null;
  finished_at: string | null;
  inputs: Record<string, string>;
  parameters: Record<string, unknown>;
  environment_identity: string;
  seed: number | null;
  settings: Record<string, unknown>;
  content_identity: string;
  artifacts: ArtifactRecord[];
  failure: FailureRecord | null;
  review_pause: ReviewPauseRecord | null;
}

export interface RunRecord {
  schema_version: "1.0";
  id: string;
  workflow_id: string;
  workflow_schema_version: "1.0";
  workflow_identity: string;
  state: RunState;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  environment: EnvironmentRecord;
  seed: number | null;
  node_runs: NodeRunRecord[];
  events: RunEvent[];
  failure: FailureRecord | null;
}

export interface RunResponse {
  path: string;
  run_id: string;
  run: RunRecord;
}

export type DownloadState =
  "queued" | "downloading" | "paused" | "cancelled" | "failed" | "succeeded";

export interface DownloadFileState {
  path: string;
  byte_size: number;
  sha256: string | null;
  bytes_completed: number;
  verified: boolean;
}

export interface DownloadFailure {
  code: string;
  message: string;
}

export interface DownloadRecord {
  schema_version: "1.0";
  download_id: string;
  provider: string;
  dataset_id: string;
  snapshot: string;
  catalog_identity: string;
  catalog_entry: CatalogEntry;
  expected_total_bytes: number;
  files: DownloadFileState[];
  bytes_completed: number;
  state: DownloadState;
  attempt: number;
  max_attempts: number;
  created_at: string;
  updated_at: string;
  failure: DownloadFailure | null;
  lock_identity: string | null;
}

export interface DatasetListItem {
  provider: string;
  dataset_id: string;
  title: string;
  public: boolean;
  latest_snapshot: string | null;
}

export interface DatasetListResponse {
  provider: string;
  items: DatasetListItem[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface DatasetCitation {
  title: string;
  doi: string | null;
  url: string | null;
}

export interface DatasetExpectedFile {
  path: string;
  byte_size: number;
  sha256: string | null;
}

export interface CatalogEntry {
  schema_version: "1.0";
  catalog_identity: string;
  provider: string;
  dataset_id: string;
  snapshot: string;
  title: string;
  modality: string;
  task: string;
  participants: number;
  formats: string[];
  approximate_total_bytes: number;
  expected_total_bytes: number;
  expected_files: DatasetExpectedFile[];
  access: "public" | "restricted" | "credentialed";
  license_name: string;
  license_spdx: string | null;
  reuse_statement: string;
  citations: DatasetCitation[];
  landing_page: string;
  compatible_templates: string[];
  curator: string;
  review_status: "pending" | "verified";
  reviewed_at: string | null;
  limitations: string;
}

export interface DatasetVerifiedFile {
  path: string;
  byte_size: number;
  sha256: string;
}

export interface DatasetLock {
  schema_version: "1.0";
  dataset_identity: string;
  catalog_identity: string | null;
  provider: string;
  dataset_id: string;
  snapshot: string;
  access: "public" | "restricted" | "credentialed";
  title: string;
  modality: string;
  task: string;
  participants: number;
  formats: string[];
  citations: DatasetCitation[];
  compatible_templates: string[];
  landing_page: string | null;
  limitations: string;
  retrieved_at: string;
  local_path: string;
  expected_total_bytes: number;
  expected_files: DatasetVerifiedFile[];
  license_name: string | null;
  license_spdx: string | null;
  reuse_statement: string | null;
}

export type LocalImportState = "scanning" | "ready" | "failed" | "cancelled";

export interface LocalImportFailure {
  code: string;
  message: string;
}

export interface LocalImportRecord {
  schema_version: "1.0";
  import_id: string;
  state: LocalImportState;
  local_path: string;
  lock: DatasetLock | null;
  failure: LocalImportFailure | null;
  created_at: string;
  updated_at: string;
}
