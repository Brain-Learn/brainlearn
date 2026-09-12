import type { Workflow } from "./types";

export const DRAFT_STORAGE_KEY = "brainlearn.unsaved-workflow.v1";
export const TOKEN_STORAGE_KEY = "brainlearn.sessionToken";

export interface DraftState {
  savedAt: string;
  workflow: Workflow;
  projectPath: string | null;
}

function isWorkflow(value: unknown): value is Workflow {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.schema_version === "1.0" &&
    typeof record.id === "string" &&
    Array.isArray(record.nodes) &&
    Array.isArray(record.edges)
  );
}

export function loadDraft(storage: Storage = localStorage): DraftState | null {
  try {
    const raw = storage.getItem(DRAFT_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const record = parsed as Record<string, unknown>;
    if (typeof record.savedAt !== "string") return null;
    if (!isWorkflow(record.workflow)) return null;
    const projectPath =
      record.projectPath === null || typeof record.projectPath === "string"
        ? record.projectPath
        : null;
    return {
      savedAt: record.savedAt,
      workflow: record.workflow,
      projectPath,
    };
  } catch {
    return null;
  }
}

export function saveDraft(
  draft: DraftState,
  storage: Storage = localStorage,
): void {
  try {
    storage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // Browser storage may be unavailable or full; the in-memory graph stays
    // authoritative and the failure is surfaced through the project status.
  }
}

export function clearDraft(storage: Storage = localStorage): void {
  try {
    storage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    // Ignored: clearing is best-effort.
  }
}

export function loadSessionToken(storage?: Storage): string {
  try {
    const store =
      storage ??
      (typeof sessionStorage !== "undefined" ? sessionStorage : undefined);
    return store?.getItem(TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveSessionToken(token: string, storage?: Storage): void {
  try {
    const store =
      storage ??
      (typeof sessionStorage !== "undefined" ? sessionStorage : undefined);
    if (!store) return;
    if (token) store.setItem(TOKEN_STORAGE_KEY, token);
    else store.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Ignored: the token input still works for the current session.
  }
}
