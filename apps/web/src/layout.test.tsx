import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { NodeManifest, Workflow } from "./types";

function loadStylesheet(): string {
  const candidates = [
    join(process.cwd(), "apps/web/src/styles.css"),
    join(process.cwd(), "src/styles.css"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return readFileSync(candidate, "utf-8");
  }
  throw new Error(
    "Cannot locate apps/web/src/styles.css from " + process.cwd(),
  );
}

const styles = loadStylesheet();

function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = styles.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "s"));
  if (!match) throw new Error(`No ${selector} rule in styles.css`);
  return match[1];
}

test("library panel scrolls internally while the page stays fixed", () => {
  expect(ruleBody(".library")).toMatch(/overflow-y:\s*auto/);
  expect(ruleBody(".panel")).toMatch(/min-height:\s*0/);
  expect(ruleBody("body")).toMatch(/overflow:\s*hidden/);
  expect(ruleBody(".app-shell")).toMatch(/height:\s*100vh/);
});

const bidsManifest: NodeManifest = {
  manifest_schema_version: "1.0",
  id: "input.bids_eeg",
  node_version: "0.1.0",
  label: "BIDS EEG",
  description: "Choose a dataset.",
  category: "Input",
  status: "example",
  ports: [],
  parameters: [],
  review_behavior: "none",
  citations: [],
  license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
  capability_requirements: [],
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/examples")) {
        return {
          ok: true,
          json: async () => ({
            examples: [
              {
                id: "eeg-first-look",
                name: "EEG first look",
                description: "",
                schema_version: "1.0",
              },
              {
                id: "demo-branched",
                name: "Branched demonstration",
                description: "",
                schema_version: "1.0",
              },
            ],
          }),
        };
      }
      if (url.endsWith("/api/workflows/validate") && init?.body) {
        const workflow = JSON.parse(String(init.body)) as Workflow;
        return {
          ok: true,
          json: async () => ({
            workflow,
            validation: { valid: true, issues: [] },
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("registry, examples, and project controls live inside the scroll container", async () => {
  render(<App />);
  await screen.findByRole("button", { name: /BIDS EEG/ });
  const sidebar = document.querySelector("aside.library");
  expect(sidebar).not.toBeNull();
  for (const name of [
    /BIDS EEG/,
    /EEG first look/,
    /Branched demonstration/,
    /^Create$/,
    /^Open$/,
    /^Save$/,
    /^Save as$/,
    /^Recent$/,
  ]) {
    const control = await screen.findByRole("button", { name });
    expect(sidebar).toContainElement(control);
  }
});
