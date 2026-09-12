import type {
  ValidationResult,
  Workflow,
  WorkflowValidationResponse,
} from "./types";

export interface ProjectPayload {
  path: string;
  manifest: {
    id: string;
    name: string;
    project_schema_version: string;
    workflow_id: string;
    updated_at: string;
  };
  workflow: Workflow;
  validation: ValidationResult;
}

export interface RecentEntry {
  path: string;
  name: string;
  last_opened: string;
}

function authHeaders(token: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function readError(response: Response, fallback: string): Promise<Error> {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof (body as Record<string, unknown>).detail === "string"
    ) {
      return new Error((body as Record<string, unknown>).detail as string);
    }
  } catch {
    // Fall through to the generic message.
  }
  return new Error(`${fallback} (HTTP ${response.status})`);
}

async function postProject(
  url: string,
  body: unknown,
  token: string,
): Promise<ProjectPayload> {
  if (!token) {
    throw new Error(
      "A session token is required. Copy it from the BrainLearn service terminal.",
    );
  }
  const response = await fetch(url, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await readError(response, `Project request failed`);
  return (await response.json()) as ProjectPayload;
}

export async function createProject(
  path: string,
  name: string,
  workflow: Workflow,
  token: string,
): Promise<ProjectPayload> {
  return postProject("/api/projects/create", { path, name, workflow }, token);
}

export async function openProject(
  path: string,
  token: string,
): Promise<ProjectPayload> {
  return postProject("/api/projects/open", { path }, token);
}

export async function saveProject(
  path: string,
  workflow: Workflow,
  token: string,
  name?: string,
): Promise<ProjectPayload> {
  return postProject("/api/projects/save", { path, workflow, name }, token);
}

export async function saveProjectAs(
  destPath: string,
  workflow: Workflow,
  token: string,
  name?: string,
): Promise<ProjectPayload> {
  return postProject(
    "/api/projects/save-as",
    { dest_path: destPath, workflow, name },
    token,
  );
}

export async function listRecentProjects(
  token: string,
): Promise<RecentEntry[]> {
  if (!token) {
    throw new Error(
      "A session token is required. Copy it from the BrainLearn service terminal.",
    );
  }
  const response = await fetch("/api/projects/recent", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw await readError(response, "Unable to list projects");
  return (await response.json()) as RecentEntry[];
}

export type { WorkflowValidationResponse };
