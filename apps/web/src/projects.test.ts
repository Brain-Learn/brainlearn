import { afterEach, expect, test, vi } from "vitest";

import { emptyWorkflow } from "./graph";
import { createProject, listRecentProjects } from "./projects";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("sends the session token when creating a project", async () => {
  const payload = {
    path: "/tmp/demo",
    manifest: {
      id: "demo",
      name: "Demo",
      project_schema_version: "1.0",
      workflow_id: "demo",
      updated_at: "2026-09-12T00:00:00.000Z",
    },
    workflow: emptyWorkflow(),
    validation: { valid: true, issues: [] },
  };
  const fetchMock = vi.fn(async () => ({
    ok: true,
    json: async () => payload,
  }));
  vi.stubGlobal("fetch", fetchMock);

  const result = await createProject(
    "/tmp/demo",
    "Demo",
    emptyWorkflow(),
    "secret",
  );
  expect(result.path).toBe("/tmp/demo");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/projects/create",
    expect.objectContaining({
      headers: expect.objectContaining({ Authorization: "Bearer secret" }),
    }),
  );
});

test("requires a token and surfaces backend detail errors", async () => {
  await expect(
    createProject("/tmp/demo", "Demo", emptyWorkflow(), ""),
  ).rejects.toThrow("session token is required");

  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: false,
      status: 403,
      json: async () => ({ detail: "Outside the explicitly opened folders." }),
    })),
  );
  await expect(
    createProject("/tmp/elsewhere", "Demo", emptyWorkflow(), "secret"),
  ).rejects.toThrow("Outside the explicitly opened folders.");
});

test("lists recent projects with the session token", async () => {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    json: async () => [{ path: "/tmp/demo", name: "Demo", last_opened: "now" }],
  }));
  vi.stubGlobal("fetch", fetchMock);
  const recent = await listRecentProjects("secret");
  expect(recent).toHaveLength(1);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/projects/recent",
    expect.objectContaining({
      headers: expect.objectContaining({ Authorization: "Bearer secret" }),
    }),
  );
});
