import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { NodeManifest, Workflow } from "./types";

const bidsManifest: NodeManifest = {
  manifest_schema_version: "1.0",
  id: "input.bids_eeg",
  node_version: "0.1.0",
  label: "BIDS EEG",
  description: "Choose a dataset.",
  category: "Input",
  status: "example",
  ports: [
    {
      id: "dataset",
      label: "Dataset",
      direction: "output",
      data_type: "bids_dataset",
      required: true,
    },
  ],
  parameters: [
    {
      id: "root",
      label: "Dataset root",
      value_type: "string",
      default: "synthetic/example-bids",
      required: true,
    },
  ],
  review_behavior: "none",
  citations: [],
  license: { name: "BSD 3-Clause License", spdx_id: "BSD-3-Clause" },
  capability_requirements: [],
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/registry/nodes")) {
        return { ok: true, json: async () => [bidsManifest] };
      }
      if (url.endsWith("/api/workflows/validate") && init?.body) {
        const workflow = JSON.parse(String(init.body)) as Workflow;
        const missing = workflow.nodes.some((node) =>
          node.parameters.some(
            (parameter) => parameter.required && parameter.value === null,
          ),
        );
        return {
          ok: true,
          json: async () => ({
            workflow,
            validation: {
              valid: !missing,
              issues: missing
                ? [
                    {
                      code: "missing_required_parameter",
                      node_id: workflow.nodes[0]?.id,
                      message: "Dataset root is required.",
                    },
                  ]
                : [],
            },
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

test("adds and removes a registry node from an empty canvas", async () => {
  render(<App />);
  expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument();
  const addButton = await screen.findByRole("button", { name: /BIDS EEG/ });

  fireEvent.click(addButton);

  await waitFor(() =>
    expect(screen.getByLabelText("Dataset root")).toBeInTheDocument(),
  );
  expect(
    screen.queryByText("Build an example EEG graph"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Remove node" }));
  expect(screen.getByText("Build an example EEG graph")).toBeInTheDocument();
});

test("edits a manifest parameter and supports undo and redo", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));
  const input = await screen.findByLabelText("Dataset root");

  fireEvent.change(input, { target: { value: "datasets/changed" } });
  expect(input).toHaveValue("datasets/changed");
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(screen.getByLabelText("Dataset root")).toHaveValue(
    "synthetic/example-bids",
  );
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  expect(screen.getByLabelText("Dataset root")).toHaveValue("datasets/changed");
});

test("shows backend validation issues on the selected node", async () => {
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /BIDS EEG/ }));

  fireEvent.change(await screen.findByLabelText("Dataset root"), {
    target: { value: "" },
  });

  expect(await screen.findAllByText("Dataset root is required.")).toHaveLength(
    2,
  );
});
