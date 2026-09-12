import type {
  NodeManifest,
  Workflow,
  WorkflowValidationResponse,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isPort(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.label === "string" &&
    ["input", "output"].includes(String(value.direction)) &&
    [
      "bids_dataset",
      "raw_eeg",
      "reviewed_eeg",
      "epochs",
      "evoked",
      "spectrum",
      "report",
    ].includes(String(value.data_type)) &&
    typeof value.required === "boolean"
  );
}

function isParameterSchema(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.label === "string" &&
    ["string", "number", "integer", "boolean"].includes(
      String(value.value_type),
    ) &&
    "default" in value &&
    typeof value.required === "boolean"
  );
}

export function assertNodeManifest(
  value: unknown,
): asserts value is NodeManifest {
  if (
    !isRecord(value) ||
    value.manifest_schema_version !== "1.0" ||
    typeof value.id !== "string" ||
    typeof value.node_version !== "string" ||
    typeof value.label !== "string" ||
    typeof value.description !== "string" ||
    typeof value.category !== "string" ||
    !["example", "experimental", "certified"].includes(String(value.status)) ||
    !Array.isArray(value.ports) ||
    !value.ports.every(isPort) ||
    !Array.isArray(value.parameters) ||
    !value.parameters.every(isParameterSchema) ||
    !["none", "required"].includes(String(value.review_behavior)) ||
    !Array.isArray(value.citations) ||
    !isRecord(value.license) ||
    typeof value.license.name !== "string" ||
    !Array.isArray(value.capability_requirements) ||
    !value.capability_requirements.every(
      (requirement) =>
        isRecord(requirement) &&
        typeof requirement.id === "string" &&
        typeof requirement.required === "boolean" &&
        typeof requirement.description === "string",
    )
  ) {
    throw new Error(
      "The node registry response does not match contract version 1.0.",
    );
  }
}

export async function fetchNodeRegistry(): Promise<NodeManifest[]> {
  const response = await fetch("/api/registry/nodes");
  if (!response.ok) throw new Error(`Registry API returned ${response.status}`);
  const payload: unknown = await response.json();
  if (!Array.isArray(payload))
    throw new Error("The node registry response must be a list.");
  payload.forEach(assertNodeManifest);
  return payload;
}

export async function validateWorkflow(
  workflow: Workflow,
): Promise<WorkflowValidationResponse> {
  const response = await fetch("/api/workflows/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(workflow),
  });
  if (!response.ok)
    throw new Error(`Validation API returned ${response.status}`);
  return (await response.json()) as WorkflowValidationResponse;
}
