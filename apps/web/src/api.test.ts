import { expect, test, vi } from "vitest";

import { assertNodeManifest, renderWorkflowPython } from "./api";
import type { Workflow } from "./types";

test("rejects registry payloads that do not match manifest contract 1.2", () => {
  expect(() => assertNodeManifest({ manifest_schema_version: "1.0" })).toThrow(
    "The node registry response does not match contract version 1.2.",
  );
  expect(() => assertNodeManifest({ manifest_schema_version: "2.0" })).toThrow(
    "The node registry response does not match contract version 1.2.",
  );
  expect(() =>
    assertNodeManifest({
      manifest_schema_version: "1.2",
      id: "broken",
      node_version: "0.1.0",
      label: "Broken",
      description: "Broken manifest",
      category: "Test",
      status: "example",
      ports: [{ id: "missing-fields" }],
      parameters: [],
      review_behavior: "none",
      citations: [],
      license: { name: "Test" },
      capability_requirements: [],
      software_dependencies: [],
      code_renderers: [],
    }),
  ).toThrow("The node registry response does not match contract version 1.2.");
});

test("renders current workflow through the authenticated versioned Python API", async () => {
  const fetch = vi.fn(
    async (_input: string | URL | Request, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(new Headers(init?.headers).get("Authorization")).toBe(
        "Bearer session-token",
      );
      return {
        ok: true,
        json: async () => ({
          schema_version: "1.0",
          renderer_id: "brainlearn.eeg.mne-filter.python",
          renderer_version: "1.2.0",
          language: "python",
          filename: "brainlearn_eeg_filter.py",
          dependencies: ["mne==1.13.2", "mne-bids==0.20.0"],
          source: "print('filter')\n",
        }),
      };
    },
  );
  vi.stubGlobal("fetch", fetch);
  const workflow: Workflow = {
    schema_version: "1.0",
    id: "demo",
    metadata: {
      name: "Demo",
      description: "",
      created_with: "test",
      modality: "EEG",
      status: "experimental",
    },
    nodes: [],
    edges: [],
  };

  const result = await renderWorkflowPython(workflow, "session-token");

  expect(fetch).toHaveBeenCalledWith(
    "/api/workflows/render/python",
    expect.objectContaining({ body: JSON.stringify(workflow) }),
  );
  expect(result.renderer_version).toBe("1.2.0");
  expect(result.source).toContain("filter");
  vi.unstubAllGlobals();
});
