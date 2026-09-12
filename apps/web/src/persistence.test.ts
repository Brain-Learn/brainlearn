import { expect, test } from "vitest";

import { emptyWorkflow } from "./graph";
import {
  DRAFT_STORAGE_KEY,
  TOKEN_STORAGE_KEY,
  clearDraft,
  loadDraft,
  loadSessionToken,
  saveDraft,
  saveSessionToken,
} from "./persistence";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => [...values.keys()][index] ?? null,
    removeItem: (key: string) => {
      values.delete(key);
    },
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  } as Storage;
}

test("saves, loads, and clears an unsaved workflow draft", () => {
  const storage = memoryStorage();
  const workflow = { ...emptyWorkflow(), id: "draft-workflow" };
  saveDraft(
    { savedAt: "2026-09-12T00:00:00.000Z", workflow, projectPath: null },
    storage,
  );

  expect(storage.getItem(DRAFT_STORAGE_KEY)).toContain("draft-workflow");
  const loaded = loadDraft(storage);
  expect(loaded?.workflow.id).toBe("draft-workflow");

  clearDraft(storage);
  expect(loadDraft(storage)).toBeNull();
});

test("rejects corrupt drafts instead of crashing recovery", () => {
  const storage = memoryStorage();
  storage.setItem(DRAFT_STORAGE_KEY, "{not-json");
  expect(loadDraft(storage)).toBeNull();
  storage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ savedAt: "today" }));
  expect(loadDraft(storage)).toBeNull();
});

test("keeps the session token out of persistent local storage and drafts", () => {
  const session = memoryStorage();
  const persistent = memoryStorage();
  saveSessionToken("secret-token", session);
  expect(loadSessionToken(session)).toBe("secret-token");
  expect(persistent.getItem(TOKEN_STORAGE_KEY)).toBeNull();

  const workflow = { ...emptyWorkflow(), id: "draft-without-token" };
  saveDraft(
    { savedAt: "2026-09-12T00:00:00.000Z", workflow, projectPath: null },
    persistent,
  );
  expect(persistent.getItem(DRAFT_STORAGE_KEY)).not.toContain("secret-token");
});
