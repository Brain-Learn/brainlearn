import { expect, test } from "vitest";

import { assertNodeManifest } from "./api";

test("rejects registry payloads that do not match manifest contract 1.0", () => {
  expect(() => assertNodeManifest({ manifest_schema_version: "2.0" })).toThrow(
    "The node registry response does not match contract version 1.0.",
  );
  expect(() =>
    assertNodeManifest({
      manifest_schema_version: "1.0",
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
    }),
  ).toThrow("The node registry response does not match contract version 1.0.");
});
